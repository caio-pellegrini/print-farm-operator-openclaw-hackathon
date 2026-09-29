import os
import struct
import tempfile
import unittest
import base64
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from experiments.farm_domain import connect_database
from experiments.openclaw_channel_bridge import (
    dispatch_plugin_request,
    submit_public_whatsapp_request,
    submit_verified_channel_attachment,
)
from experiments.identity_workflow import (
    AuthorizationError, IdentityError, UploadError, bind_verified_user,
    bootstrap_first_owner, cleanup_expired_job_files, cleanup_expired_uploads, inspect_job, issue_test_assertion,
    link_verified_identity, customer_scoped_principal,
    owner_add_material, owner_set_business_configuration, register_openclaw_upload,
    issue_channel_assertion, normalize_channel_context, resolve_identity,
    set_user_roles, submit_customer_request,
    update_job_status,
)

ROOT = Path(__file__).resolve().parents[1]
ANALYZER = ROOT / "experiments/stl-analysis/analyze_stl.py"
ISSUER = "https://farm.example/openclaw-bridge"
AUDIENCE = "print-farm-app"
SECRET = b"test-only-identity-bridge-secret"


def triangle_stl() -> bytes:
    data = bytearray(b"stage5 synthetic triangle".ljust(80, b" "))
    data += struct.pack("<I", 1)
    data += struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 20, 0, 0, 0, 20, 12, 0)
    return bytes(data)


class IdentityWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "farm.sqlite"
        self.spool = self.root / "private-intake"
        self.jobs = self.root / "private-jobs"
        self.jobs.mkdir(mode=0o700)
        bootstrap = issue_test_assertion(SECRET, ISSUER, "bootstrap-admin", AUDIENCE)
        bootstrap_first_owner(self.database, bootstrap, secret=SECRET, issuer=ISSUER,
                              audience=AUDIENCE, display_name="Bootstrap Owner")
        self.admin = resolve_identity(self.database, bootstrap, secret=SECRET,
                                      issuer=ISSUER, audience=AUDIENCE)

    def tearDown(self):
        self.temp.cleanup()

    def bind(self, subject, name, roles):
        assertion = issue_test_assertion(SECRET, ISSUER, subject, AUDIENCE)
        provisioned = bind_verified_user(self.database, self.admin, assertion, secret=SECRET,
            issuer=ISSUER, audience=AUDIENCE, display_name=name, roles=roles)
        principal = resolve_identity(self.database, assertion, secret=SECRET,
                                     issuer=ISSUER, audience=AUDIENCE)
        self.assertEqual(principal["user_id"], provisioned["user_id"])
        return principal

    def upload(self, owner, ref="ocw_0123456789abcdefghijklmnop"):
        content = triangle_stl()
        register_openclaw_upload(self.database, self.spool, upload_ref=ref,
            submitted_filename="customer-part.stl",
            attachment_reader=lambda requested: content if requested == ref else b"",
            owner_user_id=owner["user_id"])
        return ref

    def submit(self, customer, ref=None):
        ref = ref or self.upload(customer)
        return submit_customer_request(self.database, self.jobs, customer,
            upload_ref=ref, request_summary="Print one prototype in black PLA",
            analyzer_script=ANALYZER)

    def test_channel_normalization_preserves_sender_separately_from_group_context(self):
        normalized = normalize_channel_context(channel="whatsapp", external_account_id="default",
            external_sender_id="sender-test-123", conversation_context="120363@g.us",
            attachment_reference="ocw_abcdefghijklmnopqrstuvwx")
        self.assertEqual(normalized["external_sender_id"], "sender-test-123")
        self.assertEqual(normalized["conversation_context"], "120363@g.us")
        self.assertNotEqual(normalized["external_sender_id"], normalized["conversation_context"])

    def test_channel_assertion_subject_uses_sender_and_not_conversation(self):
        first = normalize_channel_context(channel="whatsapp", external_account_id="default",
            external_sender_id="sender-test-123", conversation_context="group-one@g.us")
        second = normalize_channel_context(channel="whatsapp", external_account_id="default",
            external_sender_id="sender-test-123", conversation_context="group-two@g.us")
        first_token = issue_channel_assertion(SECRET, first, AUDIENCE)
        second_token = issue_channel_assertion(SECRET, second, AUDIENCE)
        provisioned = bind_verified_user(self.database, self.admin, first_token, secret=SECRET,
            issuer="openclaw-channel:whatsapp:default", audience=AUDIENCE,
            display_name="Channel Customer", roles=["CUSTOMER"])
        self.assertEqual(resolve_identity(self.database, first_token, secret=SECRET,
            issuer="openclaw-channel:whatsapp:default", audience=AUDIENCE)["external_identity"],
            resolve_identity(self.database, second_token, secret=SECRET,
            issuer="openclaw-channel:whatsapp:default", audience=AUDIENCE)["external_identity"])
        self.assertEqual(resolve_identity(self.database, first_token, secret=SECRET,
            issuer="openclaw-channel:whatsapp:default", audience=AUDIENCE)["user_id"],
            provisioned["user_id"])

    def test_channel_bridge_binds_staged_attachment_to_sender_and_job(self):
        customer_assertion = issue_channel_assertion(SECRET,
            normalize_channel_context(channel="whatsapp", external_account_id="default",
                external_sender_id="wa-customer-001", conversation_context="group@g.us"),
            AUDIENCE)
        customer = bind_verified_user(self.database, self.admin, customer_assertion,
            secret=SECRET, issuer="openclaw-channel:whatsapp:default", audience=AUDIENCE,
            display_name="WhatsApp Customer", roles=["CUSTOMER"])
        # The customer mapping above uses the same stable channel issuer/subject.
        self.assertEqual(customer["user_id"], resolve_identity(self.database,
            customer_assertion, secret=SECRET, issuer="openclaw-channel:whatsapp:default",
            audience=AUDIENCE)["user_id"])
        content = triangle_stl()
        opaque_ref = "ocw_channelattachment123456789"
        result = submit_verified_channel_attachment(self.database, self.spool, self.jobs,
            signing_key=SECRET, audience=AUDIENCE, channel="whatsapp",
            external_account_id="default", external_sender_id="wa-customer-001",
            conversation_context="group@g.us", attachment_reference=opaque_ref,
            submitted_filename="prototype.stl",
            attachment_reader=lambda requested: content if requested == opaque_ref else b"",
            request_summary="One prototype in PLA", analyzer_script=ANALYZER)
        self.assertEqual(result["status"], "created")
        self.assertTrue(result["job_id"])
        self.assertEqual(result["safe_filename"], "model.stl")
        self.assertEqual(inspect_job(self.database,
            resolve_identity(self.database, customer_assertion, secret=SECRET,
                issuer="openclaw-channel:whatsapp:default", audience=AUDIENCE),
            result["job_id"])["safe_filename"], "model.stl")

    def test_public_whatsapp_first_valid_request_creates_customer_only(self):
        content = triangle_stl()
        opaque_ref = "ocw_publicintake123456789abc"
        kwargs = dict(signing_key=SECRET, audience=AUDIENCE,
            external_account_id="default", external_sender_id="public-customer-1",
            conversation_context="wa-dm-1", attachment_reference=opaque_ref,
            submitted_filename="part.stl",
            attachment_reader=lambda requested: content if requested == opaque_ref else b"",
            request_summary="Print this prototype", analyzer_script=ANALYZER,
            quantity=2, is_group=False)
        result = submit_public_whatsapp_request(self.database, self.spool, self.jobs, **kwargs)
        self.assertTrue(result["customer_created"])
        assertion = issue_channel_assertion(SECRET, normalize_channel_context(
            channel="whatsapp", external_account_id="default",
            external_sender_id="public-customer-1", conversation_context="another-context"), AUDIENCE)
        customer = resolve_identity(self.database, assertion, secret=SECRET,
            issuer="openclaw-channel:whatsapp:default", audience=AUDIENCE)
        self.assertEqual(customer["roles"], ["CUSTOMER"])
        self.assertEqual(set(customer["capabilities"]), {"request.submit", "request.read_own"})
        self.assertTrue(inspect_job(self.database, customer, result["job_id"])["job_id"])
        self.assertEqual(inspect_job(self.database, customer, result["job_id"])["quantity"], 2)
        with self.assertRaises(AuthorizationError):
            set_user_roles(self.database, customer, customer["user_id"], ["OWNER"])
        with self.assertRaises(AuthorizationError):
            update_job_status(self.database, customer, result["job_id"], "in_progress")
        other_request = submit_public_whatsapp_request(self.database, self.spool, self.jobs,
            **{**kwargs, "external_sender_id": "public-customer-2",
               "attachment_reference": "ocw_publicintake-second-12345",
               "attachment_reader": lambda _ref: content})
        with self.assertRaises(AuthorizationError):
            inspect_job(self.database, customer, other_request["job_id"])
        with self.assertRaises(AuthorizationError):
            owner_add_material(self.database, customer, name="PLA", density=1.24,
                density_source="supplier", density_source_ref="spec", cost_per_kg=80,
                selling_price_per_kg=150, currency="BRL", material_id="customer-pla")
        with self.assertRaises(AuthorizationError):
            owner_set_business_configuration(self.database, customer, currency="BRL")

    def test_public_whatsapp_group_and_casual_message_cannot_bootstrap_customer(self):
        content = triangle_stl()
        args = dict(signing_key=SECRET, audience=AUDIENCE, external_account_id="default",
            external_sender_id="public-group-sender", conversation_context="group-context",
            attachment_reference="ocw_groupintake123456789abcd", submitted_filename="part.stl",
            attachment_reader=lambda _ref: content, request_summary="Print this",
            analyzer_script=ANALYZER)
        with self.assertRaises(AuthorizationError):
            submit_public_whatsapp_request(self.database, self.spool, self.jobs, **args, is_group=True)
        with self.assertRaises(ValueError):
            submit_public_whatsapp_request(self.database, self.spool, self.jobs,
                **{**args, "request_summary": "  "}, is_group=False)
        with connect_database(self.database) as connection:
            before = connection.execute("SELECT COUNT(*) FROM farm_users").fetchone()[0]
        with self.assertRaises(ValueError):
            submit_public_whatsapp_request(self.database, self.spool, self.jobs,
                **args, quantity=21, is_group=False)
        with connect_database(self.database) as connection:
            after = connection.execute("SELECT COUNT(*) FROM farm_users").fetchone()[0]
        self.assertEqual(after, before)

    def test_plugin_stdin_bridge_rejects_non_private_signing_key(self):
        key_file = self.root / "signing.key"
        key_file.write_bytes(SECRET)
        key_file.chmod(0o644)
        payload = {"operation": "submit", "database": str(self.database),
            "spool_root": str(self.spool), "private_jobs_root": str(self.jobs),
            "key_file": str(key_file), "audience": AUDIENCE, "account_id": "default",
            "sender_id": "bridge-key-test", "attachment_ref": "ocw_keytest123456789012",
            "attachment_b64": base64.b64encode(triangle_stl()).decode(),
            "filename": "part.stl", "request_summary": "A test part",
            "analyzer_script": str(ANALYZER)}
        with self.assertRaises(PermissionError):
            dispatch_plugin_request(payload)

    def test_plugin_stdin_bridge_submits_a_real_private_job_record(self):
        key_file = self.root / "signing-private.key"
        key_file.write_bytes(SECRET)
        key_file.chmod(0o600)
        payload = {"operation": "submit", "database": str(self.database),
            "spool_root": str(self.spool), "private_jobs_root": str(self.jobs),
            "key_file": str(key_file), "audience": AUDIENCE, "account_id": "default",
            "sender_id": "bridge-realistic-sender", "attachment_ref": "ocw_bridgecli12345678901",
            "attachment_b64": base64.b64encode(triangle_stl()).decode(),
            "filename": "customer-upload.stl", "request_summary": "A real bridge test part",
            "analyzer_script": str(ANALYZER)}
        completed = subprocess.run([sys.executable, str(ROOT / "experiments/openclaw_channel_bridge.py")],
            input=json.dumps(payload), text=True, capture_output=True, check=True, timeout=20,
            cwd=ROOT)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "created")
        self.assertFalse(result["quote_issued"])
        self.assertEqual(result["safe_filename"], "model.stl")
        assertion = issue_channel_assertion(SECRET, normalize_channel_context(
            channel="whatsapp", external_account_id="default",
            external_sender_id="bridge-realistic-sender"), AUDIENCE)
        customer = resolve_identity(self.database, assertion, secret=SECRET,
            issuer="openclaw-channel:whatsapp:default", audience=AUDIENCE)
        self.assertEqual(customer["roles"], ["CUSTOMER"])
        self.assertEqual(inspect_job(self.database, customer, result["job_id"])["job_id"], result["job_id"])

    def test_public_path_attenuates_linked_owner_to_customer_capabilities(self):
        normalized = normalize_channel_context(channel="whatsapp", external_account_id="default",
            external_sender_id="shared-owner-sender", conversation_context="dm")
        assertion = issue_channel_assertion(SECRET, normalized, AUDIENCE)
        staff = bind_verified_user(self.database, self.admin, assertion, secret=SECRET,
            issuer="openclaw-channel:whatsapp:default", audience=AUDIENCE,
            display_name="Staff user", roles=["OWNER", "OPERATOR"])
        scoped = customer_scoped_principal(staff)
        self.assertEqual(scoped["user_id"], staff["user_id"])
        self.assertEqual(set(scoped["capabilities"]), {"request.submit", "request.read_own"})
        with self.assertRaises(AuthorizationError):
            set_user_roles(self.database, scoped, staff["user_id"], ["CUSTOMER"])
        with self.assertRaises(AuthorizationError):
            owner_set_business_configuration(self.database, scoped, currency="BRL")
        with self.assertRaises(AuthorizationError):
            update_job_status(self.database, scoped, "missing-job", "in_progress")

    def test_invalid_public_upload_does_not_create_customer(self):
        with connect_database(self.database) as connection:
            before = connection.execute("SELECT COUNT(*) FROM farm_users").fetchone()[0]
        with self.assertRaises(UploadError):
            submit_public_whatsapp_request(self.database, self.spool, self.jobs,
                signing_key=SECRET, audience=AUDIENCE, external_account_id="default",
                external_sender_id="bad-upload-sender", conversation_context="dm",
                attachment_reference="ocw_badupload123456789abcd", submitted_filename="part.stl",
                attachment_reader=lambda _ref: b"not-an-stl", request_summary="Invalid file",
                analyzer_script=ANALYZER, is_group=False)
        with connect_database(self.database) as connection:
            after = connection.execute("SELECT COUNT(*) FROM farm_users").fetchone()[0]
        self.assertEqual(after, before)

    def test_owner_approved_identity_link_preserves_one_farm_user(self):
        owner = self.bind("link-owner", "Owner", ["OWNER"])
        customer = self.bind("linked-customer-wa", "Customer", ["CUSTOMER"])
        extra_identity = issue_test_assertion(SECRET, "openclaw-channel:telegram:internal",
            "linked-customer-tg", AUDIENCE)
        linked = link_verified_identity(self.database, owner, customer["user_id"], extra_identity,
            secret=SECRET, issuer="openclaw-channel:telegram:internal", audience=AUDIENCE)
        resolved = resolve_identity(self.database, extra_identity, secret=SECRET,
            issuer="openclaw-channel:telegram:internal", audience=AUDIENCE)
        self.assertEqual(linked["user_id"], customer["user_id"])
        self.assertEqual(resolved["user_id"], customer["user_id"])
        self.assertEqual(resolved["roles"], ["CUSTOMER"])
        with self.assertRaises(AuthorizationError):
            link_verified_identity(self.database, customer, owner["user_id"],
                issue_test_assertion(SECRET, "openclaw-channel:telegram:internal", "new-id", AUDIENCE),
                secret=SECRET, issuer="openclaw-channel:telegram:internal", audience=AUDIENCE)

    def test_upload_reference_cannot_be_consumed_by_another_customer(self):
        owner = self.bind("upload-owner-customer", "Upload Owner", ["CUSTOMER"])
        stranger = self.bind("upload-stranger", "Other Customer", ["CUSTOMER"])
        ref = self.upload(owner, "ocw_ownerboundabcdefghijkl")
        with self.assertRaises(AuthorizationError):
            self.submit(stranger, ref)

    def test_verified_identity_binding_rejects_invalid_unbound_and_expired(self):
        self.bind("owner-1", "Owner", ["OWNER"])
        assertion = issue_test_assertion(SECRET, ISSUER, "owner-1", AUDIENCE)
        self.assertEqual(resolve_identity(self.database, assertion, secret=SECRET,
                         issuer=ISSUER, audience=AUDIENCE)["roles"], ["OWNER"])
        with self.assertRaises(IdentityError):
            resolve_identity(self.database, assertion + "x", secret=SECRET,
                             issuer=ISSUER, audience=AUDIENCE)
        with self.assertRaises(IdentityError):
            resolve_identity(self.database, assertion, secret=SECRET,
                             issuer="https://wrong.example", audience=AUDIENCE)
        with self.assertRaises(IdentityError):
            resolve_identity(self.database, assertion, secret=SECRET,
                             issuer=ISSUER, audience="wrong-audience")
        with self.assertRaises(IdentityError):
            resolve_identity(self.database, issue_test_assertion(SECRET, ISSUER, "nobody", AUDIENCE),
                             secret=SECRET, issuer=ISSUER, audience=AUDIENCE)
        with self.assertRaises(IdentityError):
            resolve_identity(self.database, issue_test_assertion(SECRET, ISSUER, "owner-1", AUDIENCE,
                             lifetime_seconds=10, now_epoch=100), secret=SECRET,
                             issuer=ISSUER, audience=AUDIENCE, now_epoch=111)
        with self.assertRaises(AuthorizationError):
            bootstrap_first_owner(self.database, assertion, secret=SECRET, issuer=ISSUER,
                                  audience=AUDIENCE, display_name="Second Bootstrap")

    def test_solo_owner_operator_roles_are_composable_and_customer_is_separate(self):
        solo = self.bind("solo-owner", "Solo farm owner", ["OWNER", "OPERATOR"])
        customer = self.bind("customer-1", "Customer One", ["CUSTOMER"])
        self.assertEqual(set(solo["roles"]), {"OWNER", "OPERATOR"})
        self.assertEqual(set(solo["capabilities"]), {"job.read_any", "job.status.update", "users.manage", "farm.configure"})
        request = self.submit(customer)
        inspected = inspect_job(self.database, solo, request["job_id"])
        self.assertEqual(inspected["status"], "created")
        self.assertEqual(inspected["quote_status"], "draft")
        self.assertFalse(request["quote_issued"])
        with self.assertRaises(ValueError):
            update_job_status(self.database, solo, request["job_id"], "in_progress")
        self.assertEqual(inspect_job(self.database, solo, request["job_id"])["status"], "created")
        with self.assertRaises(AuthorizationError):
            update_job_status(self.database, customer, request["job_id"], "completed")

    def test_team_customer_owner_operator_allow_and_deny_paths(self):
        owner = self.bind("owner-2", "Team Owner", ["OWNER"])
        operator = self.bind("operator-2", "Team Operator", ["OPERATOR"])
        customer = self.bind("customer-2", "Team Customer", ["CUSTOMER"])
        other_customer = self.bind("customer-3", "Other Customer", ["CUSTOMER"])
        request = self.submit(customer)
        self.assertEqual(inspect_job(self.database, customer, request["job_id"])["job_id"], request["job_id"])
        for staff in (owner, operator):
            self.assertEqual(inspect_job(self.database, staff, request["job_id"])["job_id"], request["job_id"])
        with self.assertRaises(AuthorizationError):
            inspect_job(self.database, other_customer, request["job_id"])
        with self.assertRaises(AuthorizationError):
            update_job_status(self.database, customer, request["job_id"], "queued")
        with self.assertRaises(ValueError):
            update_job_status(self.database, operator, request["job_id"], "queued")
        with self.assertRaises(AuthorizationError):
            submit_customer_request(self.database, self.jobs, operator, upload_ref="not-a-ref",
                request_summary="forbidden", analyzer_script=ANALYZER)
        with self.assertRaises(AuthorizationError):
            set_user_roles(self.database, operator, customer["user_id"], ["OPERATOR"])
        elevation = issue_test_assertion(SECRET, ISSUER, "untrusted-elevation", AUDIENCE)
        with self.assertRaises(AuthorizationError):
            bind_verified_user(self.database, customer, elevation, secret=SECRET,
                issuer=ISSUER, audience=AUDIENCE, display_name="Escalated", roles=["OWNER"])
        with self.assertRaises(AuthorizationError):
            owner_add_material(self.database, operator, name="PLA", density=1.24,
                density_source="supplier", density_source_ref="datasheet", cost_per_kg=80,
                selling_price_per_kg=150, currency="BRL", material_id="pla")
        material = owner_add_material(self.database, owner, name="PLA", density=1.24,
            density_source="supplier datasheet", density_source_ref="datasheet-1", cost_per_kg=80,
            selling_price_per_kg=150, currency="BRL", material_id="pla")
        self.assertEqual(material["version"], 1)
        business = owner_set_business_configuration(self.database, owner, currency="BRL",
            machine_hour_cost=2, energy_kwh_per_hour=.1, energy_cost_per_kwh=1,
            minimum_margin_fraction=.35, minimum_job_fee=10, setup_fee=0, source="owner")
        self.assertEqual(business["version"], 1)
        changed = set_user_roles(self.database, owner, operator["user_id"], ["OPERATOR", "CUSTOMER"])
        self.assertEqual(set(changed["roles"]), {"CUSTOMER", "OPERATOR"})
        # Customer ownership is immutable: the other customer cannot edit/read the first request.
        with self.assertRaises(AuthorizationError):
            update_job_status(self.database, other_customer, request["job_id"], "cancelled")
        self.assertEqual(owner["roles"], ["OWNER"])
        self.assertEqual(operator["roles"], ["OPERATOR"])

    def test_upload_reference_file_validation_ownership_and_private_job_copy(self):
        customer = self.bind("customer-upload", "Uploader", ["CUSTOMER"])
        self.bind("owner-upload", "Owner", ["OWNER", "OPERATOR"])
        content = triangle_stl()
        ref = "ocw_abcdefghijklmnopqrstuvwx"
        record = register_openclaw_upload(self.database, self.spool, upload_ref=ref,
            submitted_filename="customer-part.stl", attachment_reader=lambda _: content,
            owner_user_id=customer["user_id"])
        request = self.submit(customer, ref)
        job_dir = self.jobs / request["job_id"]
        target = job_dir / "model.stl"
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_bytes(), content)
        self.assertEqual(oct(job_dir.stat().st_mode & 0o777), "0o700")
        self.assertEqual(oct(target.stat().st_mode & 0o777), "0o600")
        self.assertEqual(request["safe_filename"], "model.stl")
        self.assertEqual(len(record["sha256"]), 64)
        self.assertFalse(list(self.spool.glob("*.upload")))
        connection = connect_database(self.database)
        row = connection.execute("SELECT owner_user_id,relative_path FROM job_files WHERE job_id=?", (request["job_id"],)).fetchone()
        upload = connection.execute("SELECT status,job_id FROM trusted_uploads WHERE upload_ref=?", (ref,)).fetchone()
        connection.close()
        self.assertEqual(row["owner_user_id"], customer["user_id"])
        self.assertEqual(row["relative_path"], f"{request['job_id']}/model.stl")
        self.assertEqual(tuple(upload), ("consumed", request["job_id"]))
        with self.assertRaises(UploadError):
            self.submit(customer, ref)
        with self.assertRaises(UploadError):
            register_openclaw_upload(self.database, self.spool, upload_ref="../../etc/passwd",
                submitted_filename="x.stl", attachment_reader=lambda _: content, owner_user_id=customer["user_id"])
        with self.assertRaises(UploadError):
            register_openclaw_upload(self.database, self.spool, upload_ref="ocw_traversalabcdefghijklmnop",
                submitted_filename="../../unsafe.stl", attachment_reader=lambda _: content, owner_user_id=customer["user_id"])
        with self.assertRaises(UploadError):
            register_openclaw_upload(self.database, self.spool, upload_ref="ocw_badextensionidentifier123",
                submitted_filename="part.gcode", attachment_reader=lambda _: content, owner_user_id=customer["user_id"])
        with self.assertRaises(UploadError):
            register_openclaw_upload(self.database, self.spool, upload_ref="ocw_oversizeabcdefghijklmnop",
                submitted_filename="large.stl", attachment_reader=lambda _: b"x" * (25 * 1024 * 1024 + 1), owner_user_id=customer["user_id"])
        with self.assertRaises(UploadError):
            register_openclaw_upload(self.database, self.spool, upload_ref="ocw_invalidstlabcdefghijklmnop",
                submitted_filename="fake.stl", attachment_reader=lambda _: b"not an STL", owner_user_id=customer["user_id"])

    def test_symlink_spool_abuse_and_digest_change_are_rejected(self):
        content = triangle_stl()
        ref = "ocw_symlinkabcdefghijklmnop"
        customer = self.bind("symlink-customer", "Customer", ["CUSTOMER"])
        register_openclaw_upload(self.database, self.spool, upload_ref=ref,
            submitted_filename="part.stl", attachment_reader=lambda _: content,
            owner_user_id=customer["user_id"])
        connection = connect_database(self.database)
        name = connection.execute("SELECT spool_name FROM trusted_uploads WHERE upload_ref=?", (ref,)).fetchone()[0]
        connection.close()
        path = self.spool / name
        path.unlink()
        outside = self.root / "outside.stl"
        outside.write_bytes(content)
        path.symlink_to(outside)
        with self.assertRaises(UploadError):
            self.submit(customer, ref)
        self.assertTrue(outside.exists())

        changed_ref = "ocw_digestchangeabcdefghijkl"
        self.upload(customer, changed_ref)
        conn = connect_database(self.database)
        changed_name = conn.execute("SELECT spool_name FROM trusted_uploads WHERE upload_ref=?", (changed_ref,)).fetchone()[0]
        conn.close()
        changed_content = bytearray(triangle_stl())
        changed_content[-1] = 1
        (self.spool / changed_name).write_bytes(changed_content)
        with self.assertRaises(UploadError):
            self.submit(customer, changed_ref)

    def test_upload_and_job_file_retention_cleanup(self):
        customer = self.bind("cleanup-customer", "Cleanup Customer", ["CUSTOMER"])
        ref = self.upload(customer, "ocw_cleanupabcdefghijklmnop")
        connection = connect_database(self.database)
        old = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        connection.execute("UPDATE trusted_uploads SET expires_at=? WHERE upload_ref=?", (old, ref))
        connection.commit()
        connection.close()
        self.assertEqual(cleanup_expired_uploads(self.database, self.spool)["expired_uploads"], 1)
        customer = self.bind("retention-customer", "Customer", ["CUSTOMER"])
        request = self.submit(customer)
        connection = connect_database(self.database)
        connection.execute("UPDATE job_files SET retention_until=? WHERE job_id=?", (old, request["job_id"]))
        connection.commit()
        connection.close()
        self.assertEqual(cleanup_expired_job_files(self.database, self.jobs)["removed_job_files"], 1)
        self.assertFalse((self.jobs / request["job_id"]).exists())
        self.assertEqual(inspect_job(self.database, customer, request["job_id"])["safe_filename"], None)

    def test_stage4_quote_trust_gate_is_not_bypassed(self):
        customer = self.bind("quote-gate-customer", "Customer", ["CUSTOMER"])
        request = self.submit(customer)
        connection = connect_database(self.database)
        quote = connection.execute("SELECT status,quote_snapshot_json FROM quotes WHERE quote_id=(SELECT quote_id FROM orders WHERE order_id=?)", (request["order_id"],)).fetchone()
        connection.close()
        self.assertEqual(quote["status"], "draft")
        self.assertIn('"quote_ready": false', quote["quote_snapshot_json"])


if __name__ == "__main__":
    unittest.main()
