#!/usr/bin/env python3
"""Channel-neutral boundary for a trusted OpenClaw ingress adapter.

Only a plugin hook may call this function, after it receives OpenClaw's trusted
sender/account fields and resolves the opaque attachment reference through the
host-managed media API. No session key, conversation id, or host path is an
authorization input.
"""

import base64
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.identity_workflow import (
    AuthorizationError,
    CAPABILITIES,
    connect_database,
    customer_scoped_principal,
    _validate_stl,
    normalize_channel_context,
    issue_channel_assertion,
    resolve_identity,
    resolve_or_create_public_customer,
    register_openclaw_upload,
    submit_customer_request,
)
from experiments.identity_workflow import inspect_job
from experiments.farm_domain import now
from experiments.intake_correlation import (
    cleanup_expired_intakes,
    get_pending_context,
    ingest_inbound_event,
    mark_intake_completed,
    matched_pair,
    read_pending_attachment,
    resolve_ambiguous_pair,
)


def submit_verified_channel_attachment(
    database: Path,
    spool_root: Path,
    private_jobs_root: Path,
    *,
    signing_key: bytes,
    audience: str,
    channel: str,
    external_account_id: str,
    external_sender_id: str,
    conversation_context: str | None,
    attachment_reference: str,
    submitted_filename: str,
    attachment_reader: Callable[[str], bytes],
    request_summary: str,
    analyzer_script: Path,
    quantity: int = 1,
) -> dict:
    """Bind one trusted managed attachment to its sender and newly created job.

    The caller supplies a resolver tied to the OpenClaw hook's opaque reference;
    it must not accept a model-provided path or URL. The output intentionally
    exposes only the generated job ID and safe filename.
    """
    normalized = normalize_channel_context(
        channel=channel,
        external_account_id=external_account_id,
        external_sender_id=external_sender_id,
        conversation_context=conversation_context,
        attachment_reference=attachment_reference,
    )
    assertion = issue_channel_assertion(signing_key, normalized, audience)
    issuer = f"openclaw-channel:{normalized['channel']}:{normalized['external_account_id']}"
    principal = resolve_identity(
        database,
        assertion,
        secret=signing_key,
        issuer=issuer,
        audience=audience,
    )
    register_openclaw_upload(
        database,
        spool_root,
        upload_ref=normalized["attachment_reference"],
        submitted_filename=submitted_filename,
        attachment_reader=attachment_reader,
        owner_user_id=principal["user_id"],
    )
    result = submit_customer_request(
        database,
        private_jobs_root,
        principal,
        upload_ref=normalized["attachment_reference"],
        request_summary=request_summary,
        analyzer_script=analyzer_script,
        quantity=quantity,
    )
    return {
        "job_id": result["job_id"],
        "safe_filename": result["safe_filename"],
        "status": result["status"],
    }


def submit_public_channel_request(
    database: Path,
    spool_root: Path,
    private_jobs_root: Path,
    *,
    signing_key: bytes,
    audience: str,
    channel: str,
    external_account_id: str,
    external_sender_id: str,
    conversation_context: str | None,
    attachment_reference: str,
    submitted_filename: str,
    attachment_reader: Callable[[str], bytes],
    request_summary: str,
    analyzer_script: Path,
    quantity: int = 1,
    is_group: bool,
    intake_id: str | None = None,
) -> dict:
    """Public normalized-channel path bootstraps CUSTOMER-only scope on a valid request."""
    if is_group:
        raise AuthorizationError("Public customer intake accepts direct messages only.")
    if len(request_summary.strip()) == 0 or len(request_summary) > 2000:
        raise ValueError("A customer request summary is required and must be at most 2,000 characters.")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1 or quantity > 20:
        raise ValueError("Request quantity must be between 1 and 20.")
    normalized = normalize_channel_context(
        channel=channel,
        external_account_id=external_account_id,
        external_sender_id=external_sender_id,
        conversation_context=conversation_context,
        attachment_reference=attachment_reference,
    )
    issuer = f"openclaw-channel:{normalized['channel']}:{normalized['external_account_id']}"
    assertion = issue_channel_assertion(signing_key, normalized, audience)

    # Validate the trusted media bytes and request before creating an identity.
    # The plugin-provided resolver is keyed by an opaque reference, never a path.
    content = attachment_reader(normalized["attachment_reference"])
    _validate_stl(submitted_filename, content)
    principal, created = resolve_or_create_public_customer(
        database,
        assertion,
        secret=signing_key,
        issuer=issuer,
        audience=audience,
    )
    # A public surface always applies a CUSTOMER ceiling, even if this verified
    # sender is explicitly linked to an internal staff identity.
    customer_principal = customer_scoped_principal(principal)
    if intake_id:
        with connect_database(database) as connection:
            replay = connection.execute("""SELECT j.job_id,o.customer_user_id,o.quantity,j.status,
                    f.safe_filename,q.status AS quote_status,f.retention_until
                FROM orders o JOIN print_jobs j ON j.order_id=o.order_id
                JOIN quotes q ON q.quote_id=o.quote_id LEFT JOIN job_files f ON f.job_id=j.job_id
                WHERE o.intake_id=?""", (intake_id,)).fetchone()
        if replay:
            if replay["customer_user_id"] != principal["user_id"]:
                raise AuthorizationError("The intake is already bound to another customer.")
            return {"job_id": replay["job_id"], "safe_filename": replay["safe_filename"] or "model.stl",
                    "status": replay["status"], "customer_created": created,
                    "quote_issued": False, "quantity": replay["quantity"],
                    "quote_status": replay["quote_status"], "retention_until": replay["retention_until"],
                    "idempotent_replay": True}
    with connect_database(database) as connection:
        staged = connection.execute("SELECT owner_user_id,status,sha256 FROM trusted_uploads WHERE upload_ref=?",
                                    (normalized["attachment_reference"],)).fetchone()
    digest = hashlib.sha256(content).hexdigest()
    if staged:
        if staged["owner_user_id"] != principal["user_id"] or staged["status"] != "available" or staged["sha256"] != digest:
            raise AuthorizationError("The staged attachment cannot be reused by this identity.")
    else:
        register_openclaw_upload(
            database,
            spool_root,
            upload_ref=normalized["attachment_reference"],
            submitted_filename=submitted_filename,
            attachment_reader=lambda requested: content if requested == normalized["attachment_reference"] else b"",
            owner_user_id=principal["user_id"],
        )
    result = submit_customer_request(
        database,
        private_jobs_root,
        customer_principal,
        upload_ref=normalized["attachment_reference"],
        request_summary=request_summary,
        analyzer_script=analyzer_script,
        quantity=quantity,
        intake_id=intake_id,
    )
    return {
        "job_id": result["job_id"],
        "safe_filename": result["safe_filename"],
        "status": result["status"],
        "customer_created": created,
        "quote_issued": False,
        "quantity": quantity,
    }


def submit_public_whatsapp_request(
    database: Path,
    spool_root: Path,
    private_jobs_root: Path,
    *,
    signing_key: bytes,
    audience: str,
    external_account_id: str,
    external_sender_id: str,
    conversation_context: str | None,
    attachment_reference: str,
    submitted_filename: str,
    attachment_reader: Callable[[str], bytes],
    request_summary: str,
    analyzer_script: Path,
    quantity: int = 1,
    is_group: bool,
) -> dict:
    """Compatibility wrapper for the original WhatsApp-only test contract."""
    return submit_public_channel_request(
        database, spool_root, private_jobs_root,
        signing_key=signing_key, audience=audience, channel="whatsapp",
        external_account_id=external_account_id, external_sender_id=external_sender_id,
        conversation_context=conversation_context, attachment_reference=attachment_reference,
        submitted_filename=submitted_filename, attachment_reader=attachment_reader,
        request_summary=request_summary, analyzer_script=analyzer_script,
        quantity=quantity, is_group=is_group,
    )


def _read_signing_key(path: Path) -> bytes:
    """Read the deployment key without following links or accepting broad modes."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise PermissionError("The identity signing key must be a private regular file.")
        key = os.read(fd, 4097)
    finally:
        os.close(fd)
    if len(key) < 32 or len(key) > 4096:
        raise ValueError("The identity signing key has an invalid length.")
    return key


def claim_intake_reply(database: Path, *, channel: str, account_id: str,
                       sender_id: str, conversation_id: str, intake_id: str,
                       job_id: str, run_id: str) -> dict:
    """Atomically authorize one success confirmation for a completed owned intake."""
    fields = (channel, account_id, sender_id, conversation_id, intake_id, job_id, run_id)
    if any(not isinstance(value, str) or not value.strip() or len(value) > 1024 for value in fields):
        raise ValueError("A verified completed intake and run context are required.")
    external_identity = f"openclaw-channel:{channel}:{account_id}|{sender_id}"
    reply_key = f"intake_success_reply:{intake_id}"
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        mapped = connection.execute(
            "SELECT user_id FROM farm_user_identities WHERE external_identity=?",
            (external_identity,),
        ).fetchone()
        if mapped is None:
            raise AuthorizationError("The sender is not linked to a farm user.")
        request = connection.execute("""SELECT r.intake_id FROM pending_intake_requests r
            WHERE r.intake_id=? AND r.channel=? AND r.account_id=? AND r.sender_id=?
              AND r.conversation_id=? AND r.status='completed'""",
            (intake_id, channel, account_id, sender_id, conversation_id)).fetchone()
        job = connection.execute("""SELECT j.job_id,o.customer_user_id,f.safe_filename,f.sha256 AS job_digest,
                a.sha256 AS attachment_digest
            FROM orders o JOIN print_jobs j ON j.order_id=o.order_id
            JOIN pending_intake_requests r ON r.intake_id=o.intake_id
            JOIN pending_intake_attachments a ON a.attachment_id=r.matched_attachment_id
            LEFT JOIN job_files f ON f.job_id=j.job_id
            WHERE o.intake_id=? AND j.job_id=?""", (intake_id, job_id)).fetchone()
        stored_job = connection.execute("SELECT value FROM workflow_settings WHERE key=?",
                                        (f"intake_job:{intake_id}",)).fetchone()
        if (request is None or job is None or not job["job_digest"]
                or job["job_digest"] != job["attachment_digest"]
                or stored_job is None or stored_job["value"] != job_id
                or job["customer_user_id"] != mapped["user_id"]):
            raise AuthorizationError("The completed intake is not accessible to this sender.")
        existing = connection.execute("SELECT value FROM workflow_settings WHERE key=?", (reply_key,)).fetchone()
        previous = json.loads(existing["value"]) if existing is not None else None
        if previous is not None and previous.get("status") not in {"not_sent"}:
            return {"status": "duplicate", "send": False}
        record = json.dumps({"status": "claimed", "job_id": job_id,
                             "attachment_digest": job["attachment_digest"], "run_id": run_id,
                             "claimed_at": now(),
                             "attempt": int(previous.get("attempt", 0)) + 1 if previous else 1,
                             "previous_not_sent_at": previous.get("not_sent_at") if previous else None}, separators=(",", ":"))
        connection.execute("INSERT INTO workflow_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (reply_key, record))
        return {"status": "claimed", "send": True, "job_id": job_id,
                "safe_filename": job["safe_filename"] or "model.stl"}


def mark_intake_reply_sent(database: Path, *, intake_id: str, run_id: str,
                           platform_message_id: str) -> dict:
    """Mark the already-claimed one-shot reply; this never reopens a claim."""
    reply_key = f"intake_success_reply:{intake_id}"
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT value FROM workflow_settings WHERE key=?", (reply_key,)).fetchone()
        if row is None:
            return {"status": "not_claimed"}
        record = json.loads(row["value"])
        if record.get("run_id") != run_id:
            return {"status": "not_claim_owner"}
        if not isinstance(platform_message_id, str) or not platform_message_id.strip() or len(platform_message_id) > 1024:
            return {"status": "missing_platform_receipt"}
        if record.get("status") != "sent":
            record["status"] = "sent"
            record["sent_at"] = now()
            record["platform_message_id"] = platform_message_id
            connection.execute("UPDATE workflow_settings SET value=? WHERE key=?",
                               (json.dumps(record, separators=(",", ":")), reply_key))
        return {"status": "sent"}


def mark_intake_reply_not_sent(database: Path, *, intake_id: str, run_id: str) -> dict:
    """Release a claim only when the channel adapter explicitly guarantees no send."""
    reply_key = f"intake_success_reply:{intake_id}"
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT value FROM workflow_settings WHERE key=?", (reply_key,)).fetchone()
        if row is None:
            return {"status": "not_claimed"}
        record = json.loads(row["value"])
        if record.get("run_id") != run_id:
            return {"status": "not_claim_owner"}
        if record.get("status") != "claimed" or record.get("sent_at"):
            return {"status": "not_releasable"}
        record["status"] = "not_sent"
        record["not_sent_at"] = now()
        connection.execute("UPDATE workflow_settings SET value=? WHERE key=?",
                           (json.dumps(record, separators=(",", ":")), reply_key))
        return {"status": "not_sent"}


def mark_intake_reply_uncertain(database: Path, *, intake_id: str, run_id: str) -> dict:
    """Persist unknown provider outcome without making it retryable."""
    reply_key = f"intake_success_reply:{intake_id}"
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT value FROM workflow_settings WHERE key=?", (reply_key,)).fetchone()
        if row is None:
            return {"status": "not_claimed"}
        record = json.loads(row["value"])
        if record.get("run_id") != run_id:
            return {"status": "not_claim_owner"}
        if record.get("status") == "claimed":
            record["status"] = "delivery_uncertain"
            record["uncertain_at"] = now()
            connection.execute("UPDATE workflow_settings SET value=? WHERE key=?",
                               (json.dumps(record, separators=(",", ":")), reply_key))
        return {"status": record.get("status")}


def reconcile_intake_reply_absent(database: Path, *, intake_id: str, run_id: str) -> dict:
    """Operator-reviewed recovery after the customer conversation proves no reply appeared."""
    reply_key = f"intake_success_reply:{intake_id}"
    with connect_database(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT value FROM workflow_settings WHERE key=?", (reply_key,)).fetchone()
        if row is None:
            return {"status": "not_claimed"}
        record = json.loads(row["value"])
        if record.get("run_id") != run_id:
            return {"status": "not_claim_owner"}
        if record.get("status") != "delivery_uncertain":
            return {"status": "not_reconcilable"}
        if record.get("sent_at") or record.get("platform_message_id"):
            return {"status": "already_sent"}
        completed = connection.execute(
            "SELECT 1 FROM pending_intake_requests WHERE intake_id=? AND status='completed'",
            (intake_id,),
        ).fetchone()
        if completed is None:
            return {"status": "not_completed"}
        record["status"] = "not_sent"
        record["reconciled_at"] = now()
        record["reconciliation"] = "operator_verified_absent_from_customer_conversation"
        connection.execute("UPDATE workflow_settings SET value=? WHERE key=?",
                           (json.dumps(record, separators=(",", ":")), reply_key))
        return {"status": "not_sent"}


def list_unreplied_completed_intakes(database: Path) -> dict:
    """Return completed jobs with no reply claim so the trusted plugin can drain them."""
    with connect_database(database) as connection:
        rows = connection.execute("""SELECT r.intake_id,r.channel,r.account_id,r.sender_id,
                r.conversation_id,j.job_id
            FROM pending_intake_requests r
            JOIN orders o ON o.intake_id=r.intake_id
            JOIN print_jobs j ON j.order_id=o.order_id
            LEFT JOIN workflow_settings w ON w.key=('intake_success_reply:' || r.intake_id)
            WHERE r.status='completed' AND (w.key IS NULL OR json_extract(w.value,'$.status')='not_sent')
            ORDER BY r.created_at,r.rowid""").fetchall()
    return {"status": "ok", "items": [dict(row) for row in rows]}


def _finish_matched_intake(payload: dict, signing_key: bytes, intake_id: str) -> dict:
    database = Path(payload["database"])
    pending_root = Path(payload["pending_intake_root"])
    pair = matched_pair(database, intake_id)
    if pair is None:
        raise ValueError("The matched intake is unavailable.")
    if pair["status"] == "completed":
        with connect_database(database) as connection:
            stored = connection.execute("SELECT value FROM workflow_settings WHERE key=?", (f"intake_job:{intake_id}",)).fetchone()
        return {"status": "created", "job_id": stored[0] if stored else None,
                "intake_id": intake_id, "idempotent_replay": True}
    content = read_pending_attachment(pending_root, pair)
    result = submit_public_channel_request(
        database, Path(payload["spool_root"]), Path(payload["private_jobs_root"]),
        signing_key=signing_key, audience=payload["audience"], channel=pair["channel"],
        external_account_id=pair["account_id"], external_sender_id=pair["sender_id"],
        conversation_context=pair["conversation_id"], attachment_reference=pair["reference"],
        submitted_filename=pair["submitted_filename"],
        attachment_reader=lambda reference: content if reference == pair["reference"] else b"",
        request_summary=pair["request_text"], analyzer_script=Path(payload["analyzer_script"]),
        quantity=pair["quantity"], is_group=False, intake_id=intake_id,
    )
    mark_intake_completed(database, pending_root, intake_id, result["job_id"])
    return {"status": "created", "job_id": result["job_id"],
            "safe_filename": result["safe_filename"], "quantity": result["quantity"],
            "quote_issued": False, "intake_id": intake_id}


def _normalized_event(payload: dict) -> dict:
    attachments = payload.get("attachments", [])
    if not isinstance(attachments, list):
        raise ValueError("Inbound attachments must be a list.")
    import base64
    decoded = []
    total = 0
    for attachment in attachments:
        if not isinstance(attachment, dict) or not isinstance(attachment.get("data_b64"), str):
            raise ValueError("Inbound attachment content is invalid.")
        content = base64.b64decode(attachment["data_b64"], validate=True)
        total += len(content)
        if total > 25 * 1024 * 1024:
            raise ValueError("Inbound event attachments exceed the 25 MiB total limit.")
        decoded.append({"filename": attachment.get("filename"), "data": content})
    return {"channel": payload.get("channel"), "account_id": payload.get("account_id"),
            "sender_id": payload.get("sender_id"), "conversation_id": payload.get("conversation_id"),
            "message_id": payload.get("message_id"), "text": payload.get("text"),
            "timestamp": payload.get("timestamp"), "attachments": decoded}


def dispatch_plugin_request(payload: dict) -> dict:
    """Private stdin protocol used by the trusted plugin, never model arguments."""
    required = {"operation", "database", "spool_root", "private_jobs_root", "key_file",
                "audience", "account_id", "sender_id"}
    if not isinstance(payload, dict) or not required.issubset(payload):
        raise ValueError("The plugin request is incomplete.")
    key = _read_signing_key(Path(payload["key_file"]))
    context = {
        "signing_key": key,
        "audience": payload["audience"],
        "external_account_id": payload["account_id"],
        "external_sender_id": payload["sender_id"],
    }
    database = Path(payload["database"])
    pending_root = Path(payload.get("pending_intake_root", Path(payload["spool_root"]).parent / "pending-intakes"))
    if payload["operation"] == "ingest_event":
        if payload.get("is_group") is True:
            raise AuthorizationError("Customer intake accepts direct conversations only.")
        cleanup_expired_intakes(database, pending_root)
        event = _normalized_event(payload)
        result = ingest_inbound_event(database, pending_root, event)
        if result.get("status") == "matched":
            return _finish_matched_intake({**payload, "pending_intake_root": str(pending_root)}, key, result["intake_id"])
        if result.get("status") == "ambiguous":
            return result
        return result
    if payload["operation"] == "intake_context":
        recovered = []
        with connect_database(database) as connection:
            pending = connection.execute("SELECT intake_id FROM pending_intake_requests WHERE channel=? AND account_id=? AND sender_id=? AND conversation_id=? AND status='matched' ORDER BY created_at",
                (payload.get("channel"), payload["account_id"], payload["sender_id"], payload.get("conversation_id"))).fetchall()
        config = {**payload, "pending_intake_root": str(pending_root)}
        for row in pending:
            try:
                recovered.append(_finish_matched_intake(config, key, row["intake_id"]))
            except Exception:
                recovered.append({"status": "processing"})
        state = get_pending_context(database, channel=payload.get("channel", ""),
            account_id=payload["account_id"], sender_id=payload["sender_id"],
            conversation_id=payload.get("conversation_id", ""))
        if recovered:
            state["recovered"] = recovered
        return state
    if payload["operation"] == "recover_intakes":
        cleanup_expired_intakes(database, pending_root)
        with connect_database(database) as connection:
            pending = connection.execute("SELECT intake_id FROM pending_intake_requests WHERE status='matched' ORDER BY created_at").fetchall()
        results = []
        for row in pending:
            try:
                results.append(_finish_matched_intake({**payload, "pending_intake_root": str(pending_root)}, key, row["intake_id"]))
            except Exception:
                results.append({"status": "processing"})
        return {"recovered_count": sum(item.get("status") == "created" for item in results),
                "pending_count": sum(item.get("status") == "processing" for item in results)}
    if payload["operation"] == "resolve_intake":
        resolved = resolve_ambiguous_pair(database, channel=payload.get("channel", ""),
            account_id=payload["account_id"], sender_id=payload["sender_id"],
            conversation_id=payload.get("conversation_id", ""),
            request_id=payload.get("request_id", ""), attachment_id=payload.get("attachment_id", ""))
        result = _finish_matched_intake({**payload, "pending_intake_root": str(pending_root)}, key, resolved["intake_id"])
        if resolved.get("additional_intake_id"):
            result["additional_job"] = _finish_matched_intake(
                {**payload, "pending_intake_root": str(pending_root)}, key,
                resolved["additional_intake_id"])
        result["remaining_status"] = resolved.get("remaining_status")
        return result
    if payload["operation"] == "claim_intake_reply":
        return claim_intake_reply(database, channel=payload.get("channel", ""),
            account_id=payload["account_id"], sender_id=payload["sender_id"],
            conversation_id=payload.get("conversation_id", ""),
            intake_id=payload.get("intake_id", ""), job_id=payload.get("job_id", ""),
            run_id=payload.get("run_id", ""))
    if payload["operation"] == "mark_intake_reply_sent":
        return mark_intake_reply_sent(database, intake_id=payload.get("intake_id", ""),
            run_id=payload.get("run_id", ""), platform_message_id=payload.get("platform_message_id", ""))
    if payload["operation"] == "mark_intake_reply_not_sent":
        return mark_intake_reply_not_sent(database, intake_id=payload.get("intake_id", ""),
            run_id=payload.get("run_id", ""))
    if payload["operation"] == "mark_intake_reply_uncertain":
        return mark_intake_reply_uncertain(database, intake_id=payload.get("intake_id", ""),
            run_id=payload.get("run_id", ""))
    if payload["operation"] == "reconcile_intake_reply_absent":
        return reconcile_intake_reply_absent(database, intake_id=payload.get("intake_id", ""),
            run_id=payload.get("run_id", ""))
    if payload["operation"] == "list_unreplied_completed_intakes":
        return list_unreplied_completed_intakes(database)
    if payload["operation"] == "cleanup_intakes":
        return cleanup_expired_intakes(database, pending_root)
    if payload["operation"] == "submit":
        attachment_ref = payload.get("attachment_ref")
        encoded = payload.get("attachment_b64")
        if not isinstance(attachment_ref, str) or not isinstance(encoded, str):
            raise ValueError("A managed attachment is required.")
        content = base64.b64decode(encoded, validate=True)
        if not content or len(content) > 25 * 1024 * 1024:
            raise ValueError("The managed attachment exceeds the intake limit.")
        return submit_public_whatsapp_request(
            database, Path(payload["spool_root"]), Path(payload["private_jobs_root"]),
            **context, conversation_context=payload.get("conversation_context"), attachment_reference=attachment_ref,
            submitted_filename=payload.get("filename", "customer-upload.stl"),
            attachment_reader=lambda reference: content if reference == attachment_ref else b"",
            request_summary=payload.get("request_summary", ""),
            analyzer_script=Path(payload["analyzer_script"]), is_group=False,
            quantity=payload.get("quantity", 1),
        )
    if payload["operation"] == "read_own_job":
        normalized = normalize_channel_context(
            channel=payload.get("channel", "whatsapp"), external_account_id=payload["account_id"],
            external_sender_id=payload["sender_id"],
        )
        issuer = f"openclaw-channel:{normalized['channel']}:{normalized['external_account_id']}"
        assertion = issue_channel_assertion(key, normalized, payload["audience"])
        principal = resolve_identity(database, assertion, secret=key, issuer=issuer,
                                     audience=payload["audience"])
        return inspect_job(database, customer_scoped_principal(principal), payload.get("job_id", ""))
    raise ValueError("Unsupported plugin operation.")


if __name__ == "__main__":
    try:
        raw = sys.stdin.buffer.read(40 * 1024 * 1024 + 1)
        if len(raw) > 40 * 1024 * 1024:
            raise ValueError("The plugin request is too large.")
        print(json.dumps(dispatch_plugin_request(json.loads(raw)), sort_keys=True))
    except Exception as error:
        print(json.dumps({"error": str(error)[:300]}))
        raise SystemExit(1)
