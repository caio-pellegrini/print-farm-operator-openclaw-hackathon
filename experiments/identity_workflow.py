#!/usr/bin/env python3
"""Identity-bound job authorization and trusted OpenClaw upload handoff.

The deployment bridge is the only component allowed to issue identity assertions
or register upload references. This module deliberately has no OpenClaw session
or conversation identifier in its authorization API.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import stat
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from experiments.farm_domain import add_business_configuration, add_material, connect_database, now

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_ASSERTION_LIFETIME_SECONDS = 300
CAPABILITIES = {
    "CUSTOMER": frozenset({"request.submit", "request.read_own"}),
    "OPERATOR": frozenset({"job.read_any", "job.status.update"}),
    "OWNER": frozenset({"job.read_any", "job.status.update", "users.manage", "farm.configure"}),
}
class AuthorizationError(PermissionError):
    pass


class IdentityError(PermissionError):
    pass


class UploadError(ValueError):
    pass


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_test_assertion(secret: bytes, issuer: str, subject: str, audience: str,
                         *, lifetime_seconds: int = 120, now_epoch: int | None = None) -> str:
    """Test helper mirroring the trusted bridge's HMAC assertion contract."""
    current = int(datetime.now(timezone.utc).timestamp()) if now_epoch is None else now_epoch
    claims = {"iss": issuer, "sub": subject, "aud": audience, "iat": current,
              "exp": current + lifetime_seconds, "jti": secrets.token_urlsafe(18)}
    body = _b64encode(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    signature = _b64encode(hmac.new(secret, body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def issue_channel_assertion(secret: bytes, normalized_context: dict, audience: str,
                            *, lifetime_seconds: int = 120,
                            now_epoch: int | None = None) -> str:
    """Issue an app assertion from verified bridge context; conversation ids are excluded."""
    channel = normalized_context.get("channel")
    account = normalized_context.get("external_account_id")
    sender = normalized_context.get("external_sender_id")
    if not all(isinstance(value, str) and value.strip() for value in (channel, account, sender)):
        raise IdentityError("The normalized bridge context lacks a verified sender.")
    issuer = f"openclaw-channel:{channel.strip()}:{account.strip()}"
    return issue_test_assertion(secret, issuer, sender.strip(), audience,
                                lifetime_seconds=lifetime_seconds, now_epoch=now_epoch)


def _verified_claims(assertion: str, secret: bytes, issuer: str, audience: str,
                     now_epoch: int | None = None) -> dict:
    try:
        body, signature = assertion.split(".", 1)
        claims = json.loads(_b64decode(body))
        expected = hmac.new(secret, body.encode(), hashlib.sha256).digest()
        supplied = _b64decode(signature)
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise IdentityError("The external identity assertion is malformed.") from error
    if not hmac.compare_digest(expected, supplied):
        raise IdentityError("The external identity assertion signature is invalid.")
    current = int(datetime.now(timezone.utc).timestamp()) if now_epoch is None else now_epoch
    if claims.get("iss") != issuer or claims.get("aud") != audience:
        raise IdentityError("The external identity assertion has the wrong issuer or audience.")
    issued, expires = claims.get("iat"), claims.get("exp")
    if (not isinstance(issued, int) or not isinstance(expires, int)
            or issued > current + 30 or expires <= current
            or expires <= issued or expires - issued > MAX_ASSERTION_LIFETIME_SECONDS):
        raise IdentityError("The external identity assertion is expired or outside its validity window.")
    if not isinstance(claims.get("sub"), str) or not claims["sub"].strip():
        raise IdentityError("The external identity assertion has no stable subject.")
    return claims


def normalize_channel_context(*, channel: str, external_sender_id: str,
                              external_account_id: str,
                              conversation_context: str | None = None,
                              attachment_reference: str | None = None) -> dict:
    """Normalize bridge context; conversation identity never replaces sender identity."""
    values = (channel, external_sender_id, external_account_id)
    if any(not isinstance(value, str) or not value.strip() or len(value) > 512 for value in values):
        raise IdentityError("A verified channel, account, and sender identity are required.")
    if conversation_context is not None and (not isinstance(conversation_context, str) or len(conversation_context) > 1024):
        raise IdentityError("Conversation context is invalid.")
    if attachment_reference is not None and (not isinstance(attachment_reference, str) or len(attachment_reference) > 1024):
        raise IdentityError("Attachment reference is invalid.")
    return {"channel": channel.strip(), "external_sender_id": external_sender_id.strip(),
            "external_account_id": external_account_id.strip(),
            "conversation_context": conversation_context,
            "attachment_reference": attachment_reference}


def _normalize_roles(issuer: str, subject: str, display_name: str, roles: list[str]) -> tuple[str, list[str]]:
    normalized = sorted(set(role.upper() for role in roles))
    if not issuer.strip() or not subject.strip() or not display_name.strip():
        raise ValueError("Issuer, stable subject, and display name are required.")
    if not normalized or any(role not in CAPABILITIES for role in normalized):
        raise ValueError("At least one valid role is required.")
    return f"{issuer}|{subject}", normalized


def _store_verified_user_in_connection(connection: sqlite3.Connection, issuer: str,
                                       subject: str, display_name: str,
                                       roles: list[str]) -> dict:
    external_identity, normalized = _normalize_roles(issuer, subject, display_name, roles)
    existing = connection.execute(
        "SELECT user_id FROM farm_user_identities WHERE external_identity=?", (external_identity,)
    ).fetchone()
    user_id = existing["user_id"] if existing else str(uuid.uuid4())
    if existing:
        owners = connection.execute(
            "SELECT COUNT(DISTINCT u.user_id) FROM farm_users u JOIN user_roles r USING(user_id) WHERE r.role='OWNER' AND u.user_id<>?",
            (user_id,),
        ).fetchone()[0]
        had_owner = connection.execute(
            "SELECT 1 FROM user_roles WHERE user_id=? AND role='OWNER'", (user_id,)
        ).fetchone()
        if had_owner and "OWNER" not in normalized and owners == 0:
            raise ValueError("Cannot remove the last OWNER role from the farm.")
        connection.execute("DELETE FROM user_roles WHERE user_id=?", (user_id,))
        connection.execute("UPDATE farm_users SET display_name=? WHERE user_id=?", (display_name, user_id))
    else:
        connection.execute("INSERT INTO farm_users(user_id,external_identity,display_name,created_at) VALUES (?,?,?,?)",
                           (user_id, external_identity, display_name, now()))
        connection.execute("INSERT INTO farm_user_identities(external_identity,user_id,linked_by_user_id,created_at) VALUES (?,?,NULL,?)",
                           (external_identity, user_id, now()))
    connection.executemany("INSERT INTO user_roles(user_id,role,created_at) VALUES (?,?,?)",
                           [(user_id, role, now()) for role in normalized])
    return {"user_id": user_id, "external_identity": external_identity, "roles": normalized}


def bootstrap_first_owner(db: Path, assertion: str, *, secret: bytes, issuer: str,
                          audience: str, display_name: str,
                          now_epoch: int | None = None) -> dict:
    """One-time local bootstrap. Only the first farm identity can receive OWNER here."""
    claims = _verified_claims(assertion, secret, issuer, audience, now_epoch)
    with connect_database(db) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM farm_users LIMIT 1").fetchone():
            raise AuthorizationError("The first-owner bootstrap has already been used.")
        return _store_verified_user_in_connection(connection, claims["iss"], claims["sub"], display_name, ["OWNER"])


def bind_verified_user(db: Path, principal: dict, assertion: str, *, secret: bytes,
                       issuer: str, audience: str, display_name: str,
                       roles: list[str], now_epoch: int | None = None) -> dict:
    """Owner-mediated binding for every identity after the one-time bootstrap."""
    require_capability(principal, "users.manage")
    claims = _verified_claims(assertion, secret, issuer, audience, now_epoch)
    with connect_database(db) as connection:
        return _store_verified_user_in_connection(connection, claims["iss"], claims["sub"], display_name, roles)


def link_verified_identity(db: Path, principal: dict, target_user_id: str, assertion: str, *,
                           secret: bytes, issuer: str, audience: str) -> dict:
    """OWNER-approved link of another verified channel identity to an existing user."""
    require_capability(principal, "users.manage")
    claims = _verified_claims(assertion, secret, issuer, audience)
    external_identity = f"{claims['iss']}|{claims['sub']}"
    with connect_database(db) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM farm_users WHERE user_id=?", (target_user_id,)).fetchone() is None:
            raise ValueError("The target farm user does not exist.")
        existing = connection.execute(
            "SELECT user_id FROM farm_user_identities WHERE external_identity=?", (external_identity,)
        ).fetchone()
        if existing:
            if existing["user_id"] != target_user_id:
                raise AuthorizationError("This verified identity is already linked to another farm user.")
            return {"user_id": target_user_id, "external_identity": external_identity, "already_linked": True}
        connection.execute(
            "INSERT INTO farm_user_identities(external_identity,user_id,linked_by_user_id,created_at) VALUES (?,?,?,?)",
            (external_identity, target_user_id, principal["user_id"], now()),
        )
    return {"user_id": target_user_id, "external_identity": external_identity, "already_linked": False}


def resolve_or_create_public_customer(db: Path, assertion: str, *, secret: bytes,
                                      issuer: str, audience: str,
                                      display_name: str = "Customer") -> tuple[dict, bool]:
    """Resolve a verified public sender or bootstrap a new CUSTOMER on first request."""
    claims = _verified_claims(assertion, secret, issuer, audience)
    external_identity = f"{claims['iss']}|{claims['sub']}"
    with connect_database(db) as connection:
        connection.execute("BEGIN IMMEDIATE")
        mapped = connection.execute(
            "SELECT user_id FROM farm_user_identities WHERE external_identity=?", (external_identity,)
        ).fetchone()
        created = False
        if mapped:
            user_id = mapped["user_id"]
        else:
            if not display_name.strip() or len(display_name) > 120:
                raise ValueError("Customer display name is invalid.")
            user_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO farm_users(user_id,external_identity,display_name,created_at) VALUES (?,?,?,?)",
                (user_id, external_identity, display_name.strip(), now()),
            )
            connection.execute(
                "INSERT INTO farm_user_identities(external_identity,user_id,linked_by_user_id,created_at) VALUES (?,?,NULL,?)",
                (external_identity, user_id, now()),
            )
            connection.execute(
                "INSERT INTO user_roles(user_id,role,created_at) VALUES (?,'CUSTOMER',?)", (user_id, now())
            )
            created = True
        user = connection.execute(
            "SELECT user_id,display_name FROM farm_users WHERE user_id=?", (user_id,)
        ).fetchone()
        roles = [row[0] for row in connection.execute(
            "SELECT role FROM user_roles WHERE user_id=? ORDER BY role", (user_id,)
        )]
    principal = {"user_id": user["user_id"], "display_name": user["display_name"],
                 "external_identity": external_identity, "roles": roles,
                 "capabilities": sorted(set().union(*(CAPABILITIES[role] for role in roles)))}
    return principal, created


def resolve_identity(db: Path, assertion: str, *, secret: bytes, issuer: str,
                     audience: str, now_epoch: int | None = None) -> dict:
    claims = _verified_claims(assertion, secret, issuer, audience, now_epoch)
    external_identity = f"{claims['iss']}|{claims['sub']}"
    with connect_database(db) as connection:
        user = connection.execute("SELECT u.user_id,u.display_name FROM farm_user_identities i JOIN farm_users u USING(user_id) WHERE i.external_identity=?",
                                  (external_identity,)).fetchone()
        if user is None:
            raise IdentityError("The verified external identity is not bound to a farm user.")
        roles = [row[0] for row in connection.execute(
            "SELECT role FROM user_roles WHERE user_id=? ORDER BY role", (user["user_id"],))]
    return {"user_id": user["user_id"], "display_name": user["display_name"],
            "external_identity": external_identity, "roles": roles,
            "capabilities": sorted(set().union(*(CAPABILITIES[role] for role in roles)))}


def require_capability(principal: dict, capability: str) -> None:
    if capability not in principal["capabilities"]:
        raise AuthorizationError(f"The user is not authorized for capability: {capability}.")


def customer_scoped_principal(principal: dict) -> dict:
    """Attenuate a verified user to the public/customer action set for this call."""
    return {**principal, "capabilities": sorted(CAPABILITIES["CUSTOMER"]),
            "authorization_scope": "CUSTOMER"}


def set_user_roles(db: Path, principal: dict, target_user_id: str, roles: list[str]) -> dict:
    require_capability(principal, "users.manage")
    normalized = sorted(set(role.upper() for role in roles))
    if not normalized or any(role not in CAPABILITIES for role in normalized):
        raise ValueError("At least one valid role is required.")
    with connect_database(db) as connection:
        target = connection.execute("SELECT user_id FROM farm_users WHERE user_id=?", (target_user_id,)).fetchone()
        if target is None:
            raise ValueError("The target farm user does not exist.")
        other_owner = connection.execute(
            "SELECT 1 FROM user_roles WHERE role='OWNER' AND user_id<>? LIMIT 1", (target_user_id,)
        ).fetchone()
        target_owner = connection.execute(
            "SELECT 1 FROM user_roles WHERE role='OWNER' AND user_id=?", (target_user_id,)
        ).fetchone()
        if target_owner and "OWNER" not in normalized and not other_owner:
            raise ValueError("Cannot remove the last OWNER role from the farm.")
        connection.execute("DELETE FROM user_roles WHERE user_id=?", (target_user_id,))
        connection.executemany("INSERT INTO user_roles(user_id,role,created_at) VALUES (?,?,?)",
                               [(target_user_id, role, now()) for role in normalized])
    return {"user_id": target_user_id, "roles": normalized, "changed_by": principal["user_id"]}


def owner_add_material(db: Path, principal: dict, **configuration) -> dict:
    require_capability(principal, "farm.configure")
    return add_material(db, **configuration)


def owner_set_business_configuration(db: Path, principal: dict, **configuration) -> dict:
    require_capability(principal, "farm.configure")
    return add_business_configuration(db, **configuration)


def _validate_stl(filename: str, data: bytes) -> None:
    if not isinstance(filename, str) or Path(filename).name != filename or "\\" in filename:
        raise UploadError("The submitted attachment must have a filename only.")
    if Path(filename).suffix.lower() != ".stl":
        raise UploadError("Only STL attachments are accepted.")
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise UploadError("The STL must be nonempty and no larger than 25 MiB.")
    # A binary STL is 84 bytes plus 50 bytes per triangle. ASCII STL starts with solid.
    binary_match = len(data) >= 84 and 84 + int.from_bytes(data[80:84], "little") * 50 == len(data)
    ascii_match = data.lstrip().lower().startswith(b"solid") and b"endsolid" in data[-4096:].lower()
    if not (binary_match or ascii_match):
        raise UploadError("The attachment does not have a valid binary or ASCII STL signature.")


def register_openclaw_upload(db: Path, spool_root: Path, *, upload_ref: str,
                             submitted_filename: str, attachment_reader: Callable[[str], bytes],
                             owner_user_id: str,
                             upload_ttl_minutes: int = 30) -> dict:
    """Trusted bridge callback: resolves an opaque OpenClaw ref and stores private bytes."""
    if not re.fullmatch(r"ocw_[A-Za-z0-9_-]{20,100}", upload_ref):
        raise UploadError("The attachment reference is not a trusted opaque OpenClaw upload reference.")
    if not isinstance(owner_user_id, str) or not owner_user_id.strip():
        raise UploadError("A verified application user must own the staged upload.")
    if not 1 <= upload_ttl_minutes <= 1440:
        raise ValueError("Upload reference retention must be between 1 minute and 24 hours.")
    data = attachment_reader(upload_ref)
    if not isinstance(data, bytes):
        raise UploadError("The trusted attachment bridge did not return bytes.")
    _validate_stl(submitted_filename, data)
    root = Path(spool_root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    spool_name = f"{uuid.uuid4().hex}.upload"
    path = root / spool_name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    created = datetime.now(timezone.utc)
    expires = (created + timedelta(minutes=upload_ttl_minutes)).isoformat()
    try:
        with connect_database(db) as connection:
            if connection.execute("SELECT 1 FROM farm_users WHERE user_id=?", (owner_user_id,)).fetchone() is None:
                raise IdentityError("The upload owner is not a bound farm user.")
            connection.execute("INSERT INTO workflow_settings(key,value) VALUES ('upload_spool_root',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                               (str(root.resolve()),))
            connection.execute("INSERT INTO trusted_uploads(upload_ref,submitted_filename,spool_name,size_bytes,sha256,status,expires_at,created_at,owner_user_id) VALUES (?,?,?,?,?,'available',?,?,?)",
                               (upload_ref, Path(submitted_filename).name, spool_name, len(data), hashlib.sha256(data).hexdigest(), expires, created.isoformat(), owner_user_id))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"upload_ref": upload_ref, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "expires_at": expires}


def _append_event(connection: sqlite3.Connection, job_id: str, actor_user_id: str,
                  event_type: str, details: dict) -> None:
    connection.execute("INSERT INTO job_events(event_id,job_id,actor_user_id,event_type,details_json,created_at) VALUES (?,?,?,?,?,?)",
                       (str(uuid.uuid4()), job_id, actor_user_id, event_type,
                        json.dumps(details, sort_keys=True), now()))


def submit_customer_request(db: Path, private_jobs_root: Path, principal: dict, *,
                            upload_ref: str, request_summary: str,
                            analyzer_script: Path, quantity: int = 1,
                            retention_days: int = 30,
                            intake_id: str | None = None) -> dict:
    require_capability(principal, "request.submit")
    if retention_days < 1 or retention_days > 365:
        raise ValueError("Job file retention must be between 1 and 365 days.")
    if not request_summary.strip() or len(request_summary) > 2000:
        raise ValueError("Request summary is required and must be at most 2,000 characters.")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1 or quantity > 20:
        raise ValueError("Request quantity must be between 1 and 20.")
    root = Path(private_jobs_root).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    with connect_database(db) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if intake_id:
            existing = connection.execute("""SELECT j.job_id,o.order_id,a.analysis_id,f.safe_filename,
                    j.status,q.status AS quote_status,f.retention_until,o.quantity
                FROM orders o JOIN print_jobs j ON j.order_id=o.order_id
                JOIN quotes q ON q.quote_id=o.quote_id
                JOIN stl_analyses a ON a.analysis_id=q.analysis_id
                LEFT JOIN job_files f ON f.job_id=j.job_id WHERE o.intake_id=?""", (intake_id,)).fetchone()
            if existing:
                return {"job_id": existing["job_id"], "order_id": existing["order_id"],
                        "analysis_id": existing["analysis_id"], "safe_filename": existing["safe_filename"],
                        "status": existing["status"], "quote_status": existing["quote_status"],
                        "quote_issued": False, "quantity": existing["quantity"],
                        "retention_until": existing["retention_until"], "idempotent_replay": True}
        upload = connection.execute("SELECT * FROM trusted_uploads WHERE upload_ref=?", (upload_ref,)).fetchone()
        if upload is None or upload["status"] != "available":
            raise UploadError("The upload reference is unknown, already used, or unavailable.")
        if upload["owner_user_id"] != principal["user_id"]:
            raise AuthorizationError("The upload belongs to another farm user.")
        if datetime.fromisoformat(upload["expires_at"]) <= datetime.now(timezone.utc):
            connection.execute("UPDATE trusted_uploads SET status='expired' WHERE upload_ref=?", (upload_ref,))
            connection.commit()
            raise UploadError("The upload reference has expired.")
        source_row = connection.execute("SELECT value FROM workflow_settings WHERE key='upload_spool_root'").fetchone()
        if source_row is None:
            raise UploadError("The trusted upload spool is unavailable.")
        spool_root = Path(source_row[0]).resolve()
        source = spool_root / upload["spool_name"]
        if source.parent.resolve() != spool_root or source.is_symlink():
            raise UploadError("The trusted upload spool entry failed path safety checks.")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(source, flags)
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != upload["size_bytes"] or metadata.st_size > MAX_UPLOAD_BYTES:
                raise UploadError("The staged attachment changed or is not a regular bounded file.")
            content = stream.read(MAX_UPLOAD_BYTES + 1)
        if hashlib.sha256(content).hexdigest() != upload["sha256"]:
            raise UploadError("The staged attachment digest changed after registration.")
        _validate_stl(upload["submitted_filename"], content)
        job_id = str(uuid.uuid4())
        analysis_id = str(uuid.uuid4())
        quote_id = str(uuid.uuid4())
        order_id = str(uuid.uuid4())
        safe_filename = "model.stl"
        job_dir = root / job_id
        job_dir.mkdir(mode=0o700)
        target = job_dir / safe_filename
        out_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(out_fd, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            completed = subprocess.run([sys.executable, str(analyzer_script.resolve()), str(target)],
                check=True, capture_output=True, text=True, timeout=10)
        except Exception:
            target.unlink(missing_ok=True)
            job_dir.rmdir()
            raise
        analysis = json.loads(completed.stdout)
        analysis.pop("file", None)
        created_at = now()
        retention_until = (datetime.now(timezone.utc) + timedelta(days=retention_days)).isoformat()
        try:
            source_id = str(uuid.uuid4())
            connection.execute("INSERT INTO model_sources(source_id,source_type,reference,original_filename,source_url,digest,resolution_status,created_at) VALUES (?,'ATTACHMENT',?, ?,NULL,?,'resolved',?)",
                               (source_id, upload_ref, upload["submitted_filename"], upload["sha256"], created_at))
            connection.execute("INSERT INTO stl_analyses(analysis_id,filename,dimensions_json,analysis_json,created_at,status,owner_user_id) VALUES (?,?,?,?,?,'completed',?)",
                               (analysis_id, safe_filename, json.dumps(analysis.get("dimensions_mm", {})), json.dumps(analysis), created_at, principal["user_id"]))
            snapshot = {"quote_ready": False, "quote_status": "draft", "reason": "No Stage 4 quote trust check has been run for this request."}
            connection.execute("INSERT INTO quotes(quote_id,analysis_id,customer_user_id,status,quote_snapshot_json,created_at) VALUES (?,?,?,'draft',?,?)",
                               (quote_id, analysis_id, principal["user_id"], json.dumps(snapshot), created_at))
            connection.execute("INSERT INTO orders(order_id,quote_id,customer_user_id,quantity,status,created_at,request_summary,model_source_id,intake_id) VALUES (?,?,?,?,'pending',?,?,?,?)",
                               (order_id, quote_id, principal["user_id"], quantity, created_at, request_summary.strip(), source_id, intake_id))
            connection.execute("INSERT INTO print_jobs(job_id,order_id,status,created_at) VALUES (?,?,'created',?)",
                               (job_id, order_id, created_at))
            connection.execute("INSERT INTO job_files(job_id,owner_user_id,safe_filename,relative_path,size_bytes,sha256,retention_until,created_at) VALUES (?,?,?,?,?,?,?,?)",
                               (job_id, principal["user_id"], safe_filename, f"{job_id}/{safe_filename}", len(content), upload["sha256"], retention_until, created_at))
            consumed = connection.execute("UPDATE trusted_uploads SET status='consumed',job_id=? WHERE upload_ref=? AND status='available'", (job_id, upload_ref))
            if consumed.rowcount != 1:
                raise UploadError("The upload reference was already consumed by another request.")
            _append_event(connection, job_id, principal["user_id"], "customer_request_submitted", {"analysis_id": analysis_id, "upload_ref": upload_ref})
        except Exception:
            target.unlink(missing_ok=True)
            job_dir.rmdir()
            raise
    source.unlink(missing_ok=True)
    return {"job_id": job_id, "order_id": order_id, "analysis_id": analysis_id,
            "safe_filename": safe_filename, "analysis": analysis, "status": "created",
            "quote_status": "draft", "quote_issued": False, "quantity": quantity,
            "retention_until": retention_until}


def _job_record(connection: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = connection.execute("""SELECT j.job_id,j.status,j.created_at,o.order_id,o.customer_user_id,o.quantity,
        o.request_summary,q.quote_id,q.status AS quote_status,f.safe_filename,f.size_bytes,
        f.sha256,f.retention_until,a.analysis_id,a.dimensions_json
        FROM print_jobs j JOIN orders o ON o.order_id=j.order_id
        JOIN quotes q ON q.quote_id=o.quote_id JOIN stl_analyses a ON a.analysis_id=q.analysis_id
        LEFT JOIN job_files f ON f.job_id=j.job_id WHERE j.job_id=?""", (job_id,)).fetchone()
    if row is None:
        raise ValueError("The job does not exist.")
    return row


def inspect_job(db: Path, principal: dict, job_id: str) -> dict:
    with connect_database(db) as connection:
        job = _job_record(connection, job_id)
        if "job.read_any" not in principal["capabilities"] and not (
                "request.read_own" in principal["capabilities"] and job["customer_user_id"] == principal["user_id"]):
            raise AuthorizationError("The user is not authorized to inspect this job.")
        return {"job_id": job["job_id"], "order_id": job["order_id"], "status": job["status"],
                "quantity": job["quantity"],
                "request_summary": job["request_summary"], "quote_status": job["quote_status"],
                "safe_filename": job["safe_filename"], "size_bytes": job["size_bytes"],
                "analysis_id": job["analysis_id"], "dimensions_mm": json.loads(job["dimensions_json"]),
                "retention_until": job["retention_until"]}


def update_job_status(db: Path, principal: dict, job_id: str, status: str, details: str = "") -> dict:
    require_capability(principal, "job.status.update")
    normalized = status.strip().upper() if isinstance(status, str) else ""
    # Keep the existing identity-workflow entry point safe for callers while
    # routing every state change through the production lifecycle rules.
    from experiments.production_workflow import (
        finish_job,
        mark_ready_for_production,
        start_job,
    )
    if normalized in {"QUEUED", "READY_FOR_PRODUCTION"}:
        result = mark_ready_for_production(db, principal, job_id)
    elif normalized == "IN_PROGRESS":
        result = start_job(db, principal, job_id)
    elif normalized in {"COMPLETED", "FAILED"}:
        result = finish_job(db, principal, job_id, normalized, details)
    else:
        raise ValueError("Use READY_FOR_PRODUCTION, IN_PROGRESS, COMPLETED, or FAILED production transitions.")
    return {"job_id": result["job_id"], "status": result["status"].lower(),
            "changed_by": principal["user_id"]}


def cleanup_expired_uploads(db: Path, spool_root: Path) -> dict:
    root = Path(spool_root).resolve()
    deleted = 0
    with connect_database(db) as connection:
        expired = connection.execute("SELECT upload_ref,spool_name FROM trusted_uploads WHERE status='available' AND expires_at<=?", (now(),)).fetchall()
        for item in expired:
            path = root / item["spool_name"]
            if path.parent.resolve() == root and not path.is_symlink():
                path.unlink(missing_ok=True)
            connection.execute("UPDATE trusted_uploads SET status='expired' WHERE upload_ref=?", (item["upload_ref"],))
            deleted += 1
    return {"expired_uploads": deleted}


def cleanup_expired_job_files(db: Path, private_jobs_root: Path) -> dict:
    root = Path(private_jobs_root).resolve()
    removed = 0
    with connect_database(db) as connection:
        rows = connection.execute("SELECT job_id,relative_path FROM job_files WHERE retention_until<=?", (now(),)).fetchall()
        for row in rows:
            relative = Path(row["relative_path"])
            if relative.is_absolute() or len(relative.parts) != 2 or relative.parts[0] != row["job_id"] or relative.parts[1] != "model.stl":
                raise RuntimeError("Stored job file path failed the cleanup safety check.")
            directory = root / row["job_id"]
            target = directory / relative.parts[1]
            if directory.parent.resolve() != root or target.is_symlink():
                raise RuntimeError("Stored job directory failed the cleanup safety check.")
            target.unlink(missing_ok=True)
            directory.rmdir()
            connection.execute("DELETE FROM job_files WHERE job_id=?", (row["job_id"],))
            removed += 1
    return {"removed_job_files": removed}
