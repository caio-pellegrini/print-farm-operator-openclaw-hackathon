import base64
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from experiments.farm_domain import connect_database
from experiments.identity_workflow import UploadError, inspect_job, issue_channel_assertion, normalize_channel_context, resolve_identity
from experiments.intake_correlation import (
    cleanup_expired_intakes,
    get_pending_context,
    ingest_inbound_event,
    read_pending_attachment,
)
from experiments.openclaw_channel_bridge import dispatch_plugin_request

ROOT = Path(__file__).resolve().parents[1]
ANALYZER = ROOT / "experiments/stl-analysis/analyze_stl.py"
SECRET = b"pending-intake-correlation-test-key-32-bytes"
AUDIENCE = "print-farm-app"


def triangle_stl() -> bytes:
    data = bytearray(b"correlation triangle".ljust(80, b" "))
    data += struct.pack("<I", 1)
    data += struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 20, 0, 0, 0, 20, 12, 0)
    return bytes(data)


class IntakeCorrelationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "farm.sqlite"
        self.pending = self.root / "private" / "pending"
        self.spool = self.root / "private" / "spool"
        self.jobs = self.root / "private" / "jobs"
        self.jobs.mkdir(parents=True, mode=0o700)
        self.key_file = self.root / "identity.key"
        self.key_file.write_bytes(SECRET)
        self.key_file.chmod(0o600)

    def tearDown(self):
        self.temp.cleanup()

    def event(self, message_id, *, sender="customer-1", conversation="dm-1", text=None,
              attachments=None, channel="chat"):
        return {"channel": channel, "account_id": "farm-account", "sender_id": sender,
                "conversation_id": conversation, "message_id": message_id,
                "timestamp": "2026-09-27T20:00:00+00:00", "text": text,
                "attachments": attachments or []}

    def attachment(self, filename="part.stl", data=None):
        return {"filename": filename, "data": data or triangle_stl()}

    def payload(self, event):
        return {"operation": "ingest_event", "database": str(self.db),
                "spool_root": str(self.spool), "pending_intake_root": str(self.pending),
                "private_jobs_root": str(self.jobs), "key_file": str(self.key_file),
                "audience": AUDIENCE, "account_id": event["account_id"],
                "sender_id": event["sender_id"], "channel": event["channel"],
                "conversation_id": event["conversation_id"], "message_id": event["message_id"],
                "timestamp": event["timestamp"], "text": event["text"],
                "attachments": [{"filename": item["filename"],
                                 "data_b64": base64.b64encode(item["data"]).decode()}
                                for item in event["attachments"]],
                "analyzer_script": str(ANALYZER), "is_group": False}

    def dispatch(self, event):
        return dispatch_plugin_request(self.payload(event))

    def test_text_then_attachment_creates_customer_job_once(self):
        first = self.dispatch(self.event("text-1", text="Please quote 3 units"))
        self.assertEqual(first["status"], "pending")
        second = self.dispatch(self.event("file-1", attachments=[self.attachment()]))
        self.assertEqual(second["status"], "created")
        self.assertEqual(second["quantity"], 3)
        assertion = issue_channel_assertion(SECRET, normalize_channel_context(
            channel="chat", external_account_id="farm-account", external_sender_id="customer-1"), AUDIENCE)
        principal = resolve_identity(self.db, assertion, secret=SECRET,
            issuer="openclaw-channel:chat:farm-account", audience=AUDIENCE)
        self.assertEqual(principal["roles"], ["CUSTOMER"])
        self.assertEqual(inspect_job(self.db, principal, second["job_id"])["job_id"], second["job_id"])
        with connect_database(self.db) as connection:
            source = connection.execute("SELECT source_type,resolution_status,original_filename FROM model_sources").fetchone()
            statuses = connection.execute("SELECT r.status,a.status FROM pending_intake_requests r JOIN pending_intake_attachments a ON a.attachment_id=r.matched_attachment_id").fetchone()
        self.assertEqual(tuple(source), ("ATTACHMENT", "resolved", "part.stl"))
        self.assertEqual(tuple(statuses), ("completed", "consumed"))

    def test_replay_real_openclaw_whatsapp_text_and_media_event_shapes(self):
        # Mirrors the Gateway hook projection: same WhatsApp account, sender and
        # DM conversation, with raw text on one event and an STL media fact on
        # the next. The media-only event may have generated agent-facing content.
        sender = "redacted-sender-fixture@s.whatsapp.net"
        text_event = self.event("wamid-text-fixture", sender=sender,
            conversation=sender, channel="whatsapp", text="Please quote 3 units")
        text_event["account_id"] = "default"
        text_event["timestamp"] = "2026-09-27T23:49:47.209-03:00"
        self.assertEqual(self.dispatch(text_event)["status"], "pending")

        attachment_event = self.event("wamid-media-fixture", sender=sender,
            conversation=sender, channel="whatsapp",
            attachments=[self.attachment("opaque-model.stl")])
        attachment_event["account_id"] = "default"
        attachment_event["timestamp"] = "2026-09-27T23:49:47.663-03:00"
        created = self.dispatch(attachment_event)
        self.assertEqual(created["status"], "created")
        self.assertEqual(created["quantity"], 3)
        state = get_pending_context(self.db, channel="whatsapp", account_id="default",
            sender_id=sender, conversation_id=sender)
        self.assertEqual(state["status"], "completed")
        with connect_database(self.db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM print_jobs").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT status FROM pending_intake_attachments").fetchone()[0], "consumed")

    def test_attachment_then_text_correlates(self):
        first = self.dispatch(self.event("file-first", attachments=[self.attachment()]))
        self.assertEqual(first["status"], "pending")
        second = self.dispatch(self.event("text-second", text="One prototype in PLA"))
        self.assertEqual(second["status"], "created")
        self.assertTrue(second["job_id"])

    def test_delayed_attachment_within_window_matches_without_debounce(self):
        self.assertEqual(self.dispatch(self.event("late-text", text="Print this part"))["status"], "pending")
        with connect_database(self.db) as connection:
            connection.execute("UPDATE pending_intake_requests SET created_at='2026-09-27T19:50:00+00:00'")
        delayed = self.dispatch(self.event("late-file", attachments=[self.attachment()]))
        self.assertEqual(delayed["status"], "created")

    def test_expired_pending_request_does_not_match_later_attachment(self):
        self.assertEqual(ingest_inbound_event(self.db, self.pending,
            self.event("expired-text", text="Print this part"))["status"], "pending")
        with connect_database(self.db) as connection:
            connection.execute("UPDATE pending_intake_requests SET expires_at='2000-01-01T00:00:00+00:00'")
        result = ingest_inbound_event(self.db, self.pending,
            self.event("post-expiry-file", attachments=[self.attachment()]))
        self.assertEqual(result["status"], "pending")
        state = get_pending_context(self.db, channel="chat", account_id="farm-account",
            sender_id="customer-1", conversation_id="dm-1")
        self.assertEqual(state["status"], "awaiting_request")

    def test_unrelated_customers_and_conversations_do_not_cross_match(self):
        self.assertEqual(self.dispatch(self.event("a-text", text="Customer A request"))["status"], "pending")
        self.assertEqual(self.dispatch(self.event("b-file", sender="customer-2",
            attachments=[self.attachment()]))["status"], "pending")
        self.assertEqual(self.dispatch(self.event("wrong-conversation-file", conversation="other-dm",
            attachments=[self.attachment()]))["status"], "pending")
        with connect_database(self.db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM print_jobs").fetchone()[0], 0)

    def test_event_redelivery_and_attachment_reuse_are_rejected(self):
        self.assertEqual(self.dispatch(self.event("reuse-text", text="Make one"))["status"], "pending")
        result = self.dispatch(self.event("reuse-file", attachments=[self.attachment()]))
        self.assertEqual(result["status"], "created")
        self.assertEqual(self.dispatch(self.event("reuse-file", attachments=[self.attachment()]))["status"], "duplicate")
        with connect_database(self.db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM print_jobs").fetchone()[0], 1)

    def test_completed_intake_reply_claim_suppresses_duplicate_turns(self):
        from experiments.openclaw_channel_bridge import (
            claim_intake_reply, list_unreplied_completed_intakes, mark_intake_reply_sent,
        )

        self.dispatch(self.event("reply-text", text="Please quote one unit"))
        file_event = self.event("reply-file", attachments=[self.attachment()])
        created = self.dispatch(file_event)
        with connect_database(self.db) as connection:
            intake_id = connection.execute("SELECT intake_id FROM pending_intake_requests WHERE status='completed'").fetchone()[0]

        context = {"channel": "chat", "account_id": "farm-account", "sender_id": "customer-1",
                   "conversation_id": "dm-1", "intake_id": intake_id, "job_id": created["job_id"]}
        pending_reply = list_unreplied_completed_intakes(self.db)
        first = claim_intake_reply(self.db, **context, run_id="turn-text")
        second = claim_intake_reply(self.db, **context, run_id="turn-media")
        marked = mark_intake_reply_sent(self.db, intake_id=intake_id, run_id="turn-text",
                                        platform_message_id="provider-message-1")
        third = claim_intake_reply(self.db, **context, run_id="turn-retry")
        redelivery = self.dispatch(file_event)

        self.assertEqual(first["status"], "claimed")
        self.assertTrue(first["send"])
        self.assertEqual(first["safe_filename"], "model.stl")
        self.assertEqual(second, {"status": "duplicate", "send": False})
        self.assertEqual(marked["status"], "sent")
        self.assertEqual(third, {"status": "duplicate", "send": False})
        self.assertEqual(redelivery["status"], "duplicate")
        self.assertEqual(len(pending_reply["items"]), 1)
        self.assertEqual(pending_reply["items"][0]["job_id"], created["job_id"])
        self.assertEqual(list_unreplied_completed_intakes(self.db)["items"], [])
        with connect_database(self.db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM print_jobs").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM stl_analyses").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_settings WHERE key LIKE 'intake_success_reply:%'").fetchone()[0], 1)

    def test_same_stl_in_later_new_request_gets_its_own_reply_claim(self):
        from experiments.openclaw_channel_bridge import claim_intake_reply, mark_intake_reply_sent

        def complete(prefix):
            self.dispatch(self.event(f"{prefix}-text", text="Please quote one unit"))
            created = self.dispatch(self.event(f"{prefix}-file", attachments=[self.attachment()]))
            with connect_database(self.db) as connection:
                request = connection.execute("""SELECT intake_id FROM pending_intake_requests
                    WHERE status='completed' ORDER BY created_at DESC LIMIT 1""").fetchone()
            context = {"channel": "chat", "account_id": "farm-account", "sender_id": "customer-1",
                "conversation_id": "dm-1", "intake_id": request["intake_id"], "job_id": created["job_id"]}
            return context, claim_intake_reply(self.db, **context, run_id=f"{prefix}-turn")

        first_context, first = complete("first")
        self.assertTrue(first["send"])
        self.assertEqual(mark_intake_reply_sent(self.db, intake_id=first_context["intake_id"],
            run_id="first-turn", platform_message_id="provider-first")["status"], "sent")

        second_context, second = complete("later")
        self.assertTrue(second["send"])
        self.assertNotEqual(first_context["intake_id"], second_context["intake_id"])
        self.assertNotEqual(first_context["job_id"], second_context["job_id"])
        with connect_database(self.db) as connection:
            digests = [row[0] for row in connection.execute(
                "SELECT sha256 FROM pending_intake_attachments ORDER BY created_at")]
            self.assertEqual(digests[0], digests[1])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM print_jobs").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM stl_analyses").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_settings WHERE key LIKE 'intake_success_reply:%'").fetchone()[0], 2)
        self.assertEqual(claim_intake_reply(self.db, **first_context, run_id="first-retry"),
            {"status": "duplicate", "send": False})
        self.assertEqual(claim_intake_reply(self.db, **second_context, run_id="later-retry"),
            {"status": "duplicate", "send": False})

    def test_two_consecutive_valid_intakes_each_have_one_reply_claim(self):
        from experiments.openclaw_channel_bridge import claim_intake_reply, mark_intake_reply_sent

        successes = []
        for index in (1, 2):
            prefix = f"consecutive-{index}"
            self.dispatch(self.event(f"{prefix}-text", text=f"Please quote {index} units"))
            model = bytearray(triangle_stl())
            model[0] = index
            created = self.dispatch(self.event(f"{prefix}-file", attachments=[self.attachment(
                f"model-{index}.stl", bytes(model))]))
            with connect_database(self.db) as connection:
                intake_id = connection.execute("""SELECT intake_id FROM pending_intake_requests
                    WHERE status='completed' ORDER BY created_at DESC LIMIT 1""").fetchone()[0]
            context = {"channel": "chat", "account_id": "farm-account", "sender_id": "customer-1",
                "conversation_id": "dm-1", "intake_id": intake_id, "job_id": created["job_id"]}
            claimed = claim_intake_reply(self.db, **context, run_id=f"{prefix}-turn")
            self.assertTrue(claimed["send"])
            self.assertEqual(mark_intake_reply_sent(self.db, intake_id=intake_id,
                run_id=f"{prefix}-turn", platform_message_id=f"provider-{prefix}")["status"], "sent")
            successes.append(intake_id)

        self.assertEqual(len(set(successes)), 2)
        with connect_database(self.db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM print_jobs").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM stl_analyses").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workflow_settings WHERE key LIKE 'intake_success_reply:%' AND value LIKE '%\"status\":\"sent\"%'").fetchone()[0], 2)

    def test_only_explicit_not_sent_outcome_can_reopen_a_reply_claim(self):
        from experiments.openclaw_channel_bridge import (
            claim_intake_reply, list_unreplied_completed_intakes,
            mark_intake_reply_not_sent, mark_intake_reply_uncertain,
        )

        self.dispatch(self.event("reopen-text", text="Please quote one unit"))
        created = self.dispatch(self.event("reopen-file", attachments=[self.attachment()]))
        with connect_database(self.db) as connection:
            intake_id = connection.execute("SELECT intake_id FROM pending_intake_requests WHERE status='completed'").fetchone()[0]
        context = {"channel": "chat", "account_id": "farm-account", "sender_id": "customer-1",
                   "conversation_id": "dm-1", "intake_id": intake_id, "job_id": created["job_id"]}
        self.assertTrue(claim_intake_reply(self.db, **context, run_id="attempt-1")["send"])
        self.assertEqual(mark_intake_reply_uncertain(self.db, intake_id=intake_id,
            run_id="attempt-1")["status"], "delivery_uncertain")
        self.assertEqual(list_unreplied_completed_intakes(self.db)["items"], [])
        self.assertEqual(mark_intake_reply_not_sent(self.db, intake_id=intake_id,
            run_id="attempt-1")["status"], "not_releasable")

        # A provider's explicit no-send result is the only automatic reopen path.
        with connect_database(self.db) as connection:
            key = f"intake_success_reply:{intake_id}"
            record = connection.execute("SELECT value FROM workflow_settings WHERE key=?", (key,)).fetchone()
            import json
            value = json.loads(record[0])
            value["status"] = "claimed"
            connection.execute("UPDATE workflow_settings SET value=? WHERE key=?", (json.dumps(value), key))
        self.assertEqual(mark_intake_reply_not_sent(self.db, intake_id=intake_id,
            run_id="attempt-1")["status"], "not_sent")
        reopened = list_unreplied_completed_intakes(self.db)
        self.assertEqual(len(reopened["items"]), 1)
        self.assertTrue(claim_intake_reply(self.db, **context, run_id="attempt-2")["send"])

    def test_operator_reconciliation_requires_absence_evidence_and_reopens_one_claim(self):
        from experiments.openclaw_channel_bridge import (
            claim_intake_reply, list_unreplied_completed_intakes,
            mark_intake_reply_sent, mark_intake_reply_uncertain,
            reconcile_intake_reply_absent,
        )

        self.dispatch(self.event("operator-reconcile-text", text="Please quote one unit"))
        created = self.dispatch(self.event("operator-reconcile-file", attachments=[self.attachment()]))
        with connect_database(self.db) as connection:
            intake_id = connection.execute("SELECT intake_id FROM pending_intake_requests WHERE status='completed'").fetchone()[0]
        context = {"channel": "chat", "account_id": "farm-account", "sender_id": "customer-1",
                   "conversation_id": "dm-1", "intake_id": intake_id, "job_id": created["job_id"]}
        self.assertTrue(claim_intake_reply(self.db, **context, run_id="ambiguous-attempt")["send"])
        self.assertEqual(reconcile_intake_reply_absent(self.db, intake_id=intake_id,
            run_id="wrong-attempt")["status"], "not_claim_owner")
        self.assertEqual(mark_intake_reply_uncertain(self.db, intake_id=intake_id,
            run_id="ambiguous-attempt")["status"], "delivery_uncertain")
        self.assertEqual(reconcile_intake_reply_absent(self.db, intake_id=intake_id,
            run_id="ambiguous-attempt")["status"], "not_sent")
        self.assertEqual(len(list_unreplied_completed_intakes(self.db)["items"]), 1)
        self.assertTrue(claim_intake_reply(self.db, **context, run_id="reconciled-attempt")["send"])
        self.assertEqual(reconcile_intake_reply_absent(self.db, intake_id=intake_id,
            run_id="reconciled-attempt")["status"], "not_reconcilable")
        self.assertEqual(mark_intake_reply_sent(self.db, intake_id=intake_id,
            run_id="reconciled-attempt", platform_message_id="provider-reconciled")["status"], "sent")
        self.assertEqual(reconcile_intake_reply_absent(self.db, intake_id=intake_id,
            run_id="reconciled-attempt")["status"], "not_reconcilable")

    def test_intake_reply_claim_enforces_sender_and_job_ownership(self):
        from experiments.identity_workflow import AuthorizationError
        from experiments.openclaw_channel_bridge import claim_intake_reply

        self.dispatch(self.event("owned-reply-text", text="Make one"))
        created = self.dispatch(self.event("owned-reply-file", attachments=[self.attachment()]))
        with connect_database(self.db) as connection:
            intake_id = connection.execute("SELECT intake_id FROM pending_intake_requests WHERE status='completed'").fetchone()[0]
        with self.assertRaises(AuthorizationError):
            claim_intake_reply(self.db, channel="chat", account_id="farm-account",
                sender_id="customer-2", conversation_id="dm-2", intake_id=intake_id,
                job_id=created["job_id"], run_id="unauthorized-turn")
            self.assertEqual(connection.execute("SELECT status FROM pending_intake_attachments").fetchone()[0], "consumed")

    def test_ambiguous_pair_is_not_auto_consumed_and_explicit_selection_works(self):
        self.assertEqual(self.dispatch(self.event("amb-text-1", text="First request"))["status"], "pending")
        self.assertEqual(self.dispatch(self.event("amb-text-2", text="Second request"))["status"], "pending")
        result = self.dispatch(self.event("amb-file-1", attachments=[self.attachment("first.stl")]))
        self.assertEqual(result["status"], "ambiguous")
        clarification = self.dispatch(self.event("amb-clarification", text="The first file goes with the first request"))
        self.assertEqual(clarification["status"], "ambiguous")
        with connect_database(self.db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM print_jobs").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM pending_intake_requests").fetchone()[0], 2)
            request_id = connection.execute("SELECT request_id FROM pending_intake_requests WHERE message_id='amb-text-1'").fetchone()[0]
            attachment_id = connection.execute("SELECT attachment_id FROM pending_intake_attachments WHERE message_id='amb-file-1'").fetchone()[0]
        resolved = dispatch_plugin_request({**self.payload(self.event("noop")),
            "operation": "resolve_intake", "request_id": request_id, "attachment_id": attachment_id})
        self.assertEqual(resolved["status"], "created")

    def test_pending_state_survives_application_process_restart(self):
        self.assertEqual(ingest_inbound_event(self.db, self.pending,
            self.event("before-restart", text="A request before restart"))["status"], "pending")
        # A separate Python process resolves the saved request after restart.
        with connect_database(self.db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM pending_intake_requests WHERE status='pending'").fetchone()[0], 1)
        later_event = self.event("after-restart", attachments=[self.attachment()])
        later_event["attachments"] = [{"filename": item["filename"],
            "data": base64.b64encode(item["data"]).decode()} for item in later_event["attachments"]]
        child_code = (
            "import base64,json,sys; from pathlib import Path; "
            "from experiments.intake_correlation import ingest_inbound_event; "
            "event=json.loads(sys.argv[3]); "
            "event['attachments']=[{'filename':x['filename'],'data':base64.b64decode(x['data'])} for x in event['attachments']]; "
            "print(json.dumps(ingest_inbound_event(Path(sys.argv[1]),Path(sys.argv[2]),event)))"
        )
        completed = subprocess.run([sys.executable, "-c", child_code, str(self.db), str(self.pending),
            json.dumps(later_event)], cwd=ROOT, text=True, capture_output=True, check=True, timeout=10)
        self.assertEqual(json.loads(completed.stdout)["status"], "matched")

    def test_cleanup_expires_pending_upload_files(self):
        ingest_inbound_event(self.db, self.pending,
            self.event("cleanup-file", attachments=[self.attachment()]))
        ingest_inbound_event(self.db, self.pending,
            self.event("cleanup-text", text="Please quote this model"))
        file_path = next(self.pending.glob("*.pending"))
        with connect_database(self.db) as connection:
            connection.execute("UPDATE pending_intake_attachments SET expires_at='2000-01-01T00:00:00+00:00'")
            connection.execute("UPDATE pending_intake_requests SET expires_at='2000-01-01T00:00:00+00:00'")
        cleanup = cleanup_expired_intakes(self.db, self.pending)
        self.assertEqual(cleanup["expired_attachments"], 1)
        self.assertEqual(cleanup["expired_requests"], 1)
        self.assertFalse(file_path.exists())

    def test_rejects_non_stl_and_oversized_attachment(self):
        for bad in (self.attachment("part.gcode"), self.attachment(data=b"bad")):
            with self.assertRaises(ValueError):
                ingest_inbound_event(self.db, self.pending,
                    self.event("bad-" + str(id(bad)), attachments=[bad]))

    def test_pending_attachment_symlink_substitution_is_rejected(self):
        ingest_inbound_event(self.db, self.pending,
            self.event("symlink-file", attachments=[self.attachment()]))
        with connect_database(self.db) as connection:
            attachment_id = connection.execute("SELECT attachment_id FROM pending_intake_attachments").fetchone()[0]
            pair = dict(connection.execute("SELECT * FROM pending_intake_attachments WHERE attachment_id=?",
                                           (attachment_id,)).fetchone())
        path = self.pending / pair["spool_name"]
        path.unlink()
        outside = self.root / "outside.stl"
        outside.write_bytes(triangle_stl())
        path.symlink_to(outside)
        with self.assertRaises(UploadError):
            read_pending_attachment(self.pending, pair)


if __name__ == "__main__":
    unittest.main()
