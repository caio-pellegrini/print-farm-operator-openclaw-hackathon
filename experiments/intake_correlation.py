#!/usr/bin/env python3
"""Persistent, channel-neutral correlation for customer request events.

This layer stores independently arriving text and validated STL attachments.
Channel transport identifiers are correlation context only; authorization is
still derived from the verified sender identity when a pair is finalized.
"""

import hashlib
import os
import re
import sqlite3
import stat
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from experiments.farm_domain import connect_database, now
from experiments.identity_workflow import MAX_UPLOAD_BYTES, UploadError, _validate_stl

DEFAULT_TTL_MINUTES = 30
MAX_TEXT_LENGTH = 2000
MAX_ATTACHMENTS_PER_EVENT = 4


class IntakeCorrelationError(ValueError):
    pass


def _private_root(path: Path, *, create: bool) -> Path:
    root = Path(path).absolute()
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    resolved = root.resolve(strict=True)
    if resolved != root:
        raise UploadError("The pending intake root must not contain symbolic links.")
    os.chmod(root, 0o700)
    return root


def _quantity(text: str) -> int:
    match = re.search(r"\b(\d{1,2})\s*(?:unidades?|peças?|pecas?|pcs?|units?)\b", text, re.IGNORECASE)
    if match:
        value = int(match.group(1))
        if 1 <= value <= 20:
            return value
    return 1


def _normalize_event(event: dict) -> dict:
    if not isinstance(event, dict):
        raise IntakeCorrelationError("A normalized inbound event is required.")
    fields = ("channel", "account_id", "sender_id", "conversation_id", "message_id")
    normalized = {}
    for field in fields:
        value = event.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 1024:
            raise IntakeCorrelationError(f"The normalized event is missing a valid {field}.")
        normalized[field] = value.strip()
    text = event.get("text")
    if text is not None and (not isinstance(text, str) or len(text) > MAX_TEXT_LENGTH):
        raise IntakeCorrelationError("Inbound request text is invalid or exceeds 2,000 characters.")
    attachments = event.get("attachments", [])
    if not isinstance(attachments, list) or len(attachments) > MAX_ATTACHMENTS_PER_EVENT:
        raise IntakeCorrelationError("The event has too many attachments.")
    timestamp = event.get("timestamp")
    if timestamp is not None:
        if not isinstance(timestamp, str) or len(timestamp) > 64:
            raise IntakeCorrelationError("The inbound timestamp is invalid.")
        try:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as error:
            raise IntakeCorrelationError("The inbound timestamp is invalid.") from error
    normalized.update({"text": text.strip() if text and text.strip() else None,
                       "attachments": attachments,
                       "event_timestamp": timestamp or now()})
    if normalized["text"] and len(normalized["text"]) > MAX_TEXT_LENGTH:
        raise IntakeCorrelationError("Inbound request text is invalid or exceeds 2,000 characters.")
    return normalized


def _identity_tuple(event: dict) -> tuple[str, str, str, str]:
    return event["channel"], event["account_id"], event["sender_id"], event["conversation_id"]


def _pending_rows(connection: sqlite3.Connection, table: str, identity: tuple[str, ...], received: str) -> list[sqlite3.Row]:
    if table not in {"pending_intake_requests", "pending_intake_attachments"}:
        raise ValueError("Invalid pending intake table.")
    return connection.execute(
        f"SELECT * FROM {table} WHERE channel=? AND account_id=? AND sender_id=? AND conversation_id=? "
        "AND status IN ('pending','ambiguous') AND expires_at>? ORDER BY created_at, rowid",
        (*identity, received),
    ).fetchall()


def _correlate(connection: sqlite3.Connection, identity: tuple[str, ...], received: str) -> dict:
    requests = _pending_rows(connection, "pending_intake_requests", identity, received)
    attachments = _pending_rows(connection, "pending_intake_attachments", identity, received)
    if not requests or not attachments:
        return {"status": "pending", "pending_requests": len(requests),
                "pending_attachments": len(attachments)}
    if len(requests) == 1 and len(attachments) == 1:
        request, attachment = requests[0], attachments[0]
        # FIFO correlation is automatic only while there is one possible pair.
        intake_id = request["intake_id"] or str(uuid.uuid4())
        connection.execute("UPDATE pending_intake_requests SET status='matched',matched_attachment_id=?,intake_id=? WHERE request_id=?",
                           (attachment["attachment_id"], intake_id, request["request_id"]))
        connection.execute("UPDATE pending_intake_attachments SET status='matched',matched_request_id=? WHERE attachment_id=?",
                           (request["request_id"], attachment["attachment_id"]))
        return {"status": "matched", "request_id": request["request_id"],
                "attachment_id": attachment["attachment_id"], "intake_id": intake_id}
    connection.executemany("UPDATE pending_intake_requests SET status='ambiguous' WHERE request_id=?",
                           [(row["request_id"],) for row in requests])
    connection.executemany("UPDATE pending_intake_attachments SET status='ambiguous' WHERE attachment_id=?",
                           [(row["attachment_id"],) for row in attachments])
    return {"status": "ambiguous", "requests": [
                {"request_id": row["request_id"], "text": row["request_text"], "quantity": row["quantity"]}
                for row in requests],
            "attachments": [
                {"attachment_id": row["attachment_id"], "filename": row["submitted_filename"]}
                for row in attachments]}


def ingest_inbound_event(db: Path, pending_root: Path, event: dict, *, ttl_minutes: int = DEFAULT_TTL_MINUTES) -> dict:
    """Persist one normalized event and correlate it with at most one unambiguous peer."""
    if not 1 <= ttl_minutes <= 1440:
        raise ValueError("Pending intake retention must be between 1 minute and 24 hours.")
    normalized = _normalize_event(event)
    identity = _identity_tuple(normalized)
    created_at = datetime.now(timezone.utc)
    created = created_at.isoformat()
    expires = (created_at + timedelta(minutes=ttl_minutes)).isoformat()
    root = _private_root(Path(pending_root), create=True)
    staged: list[dict] = []
    try:
        for attachment in normalized["attachments"]:
            if not isinstance(attachment, dict):
                raise IntakeCorrelationError("Attachment metadata is invalid.")
            filename = attachment.get("filename")
            data = attachment.get("data")
            if not isinstance(data, bytes):
                raise IntakeCorrelationError("The trusted channel bridge did not provide attachment bytes.")
            _validate_stl(filename, data)
            spool_name = f"{uuid.uuid4().hex}.pending"
            path = root / spool_name
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
            except Exception:
                path.unlink(missing_ok=True)
                raise
            staged.append({"attachment_id": str(uuid.uuid4()), "reference": f"ocw_{uuid.uuid4().hex}",
                           "filename": Path(filename).name, "spool_name": spool_name,
                           "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "path": path})

        with connect_database(db) as connection:
            connection.execute("BEGIN IMMEDIATE")
            receipt = connection.execute(
                "INSERT OR IGNORE INTO inbound_event_receipts(channel,account_id,message_id,created_at) VALUES (?,?,?,?)",
                (normalized["channel"], normalized["account_id"], normalized["message_id"], created),
            )
            if receipt.rowcount == 0:
                for item in staged:
                    item["path"].unlink(missing_ok=True)
                return {"status": "duplicate"}
            request_id = None
            ambiguous_exists = connection.execute("""SELECT 1 FROM pending_intake_requests
                WHERE channel=? AND account_id=? AND sender_id=? AND conversation_id=? AND status='ambiguous'
                UNION ALL SELECT 1 FROM pending_intake_attachments
                WHERE channel=? AND account_id=? AND sender_id=? AND conversation_id=? AND status='ambiguous'
                LIMIT 1""", (*identity, *identity)).fetchone()
            clarification_only = bool(ambiguous_exists and normalized["text"] and not staged)
            if normalized["text"] and not clarification_only:
                request_id = str(uuid.uuid4())
                connection.execute("""INSERT INTO pending_intake_requests
                    (request_id,channel,account_id,sender_id,conversation_id,message_id,request_text,quantity,
                     status,event_timestamp,created_at,expires_at)
                    VALUES (?,?,?,?,?,?,?,?,'pending',?,?,?)""",
                    (request_id, *identity, normalized["message_id"], normalized["text"],
                     _quantity(normalized["text"]), normalized["event_timestamp"], created, expires))
            attachment_ids = []
            for item in staged:
                connection.execute("""INSERT INTO pending_intake_attachments
                    (attachment_id,channel,account_id,sender_id,conversation_id,message_id,reference,
                     submitted_filename,spool_name,size_bytes,sha256,status,event_timestamp,created_at,expires_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?)""",
                    (item["attachment_id"], *identity, normalized["message_id"], item["reference"],
                     item["filename"], item["spool_name"], item["size"], item["sha256"],
                     normalized["event_timestamp"], created, expires))
                attachment_ids.append(item["attachment_id"])
            correlated = _correlate(connection, identity, created)
            if clarification_only:
                correlated["status"] = "ambiguous"
            correlated.update({"request_id": request_id or correlated.get("request_id"),
                               "attachment_ids": attachment_ids or ([correlated["attachment_id"]] if correlated.get("attachment_id") else [])})
            return correlated
    except Exception:
        for item in staged:
            item["path"].unlink(missing_ok=True)
        raise


def resolve_ambiguous_pair(db: Path, *, channel: str, account_id: str, sender_id: str,
                           conversation_id: str, request_id: str, attachment_id: str) -> dict:
    """Resolve an explicitly selected pair without allowing cross-sender matching."""
    identity = (channel, account_id, sender_id, conversation_id)
    with connect_database(db) as connection:
        connection.execute("BEGIN IMMEDIATE")
        request = connection.execute("SELECT * FROM pending_intake_requests WHERE request_id=?", (request_id,)).fetchone()
        attachment = connection.execute("SELECT * FROM pending_intake_attachments WHERE attachment_id=?", (attachment_id,)).fetchone()
        if (request is None or attachment is None or
                (request["channel"], request["account_id"], request["sender_id"], request["conversation_id"]) != identity or
                (attachment["channel"], attachment["account_id"], attachment["sender_id"], attachment["conversation_id"]) != identity or
                request["status"] != "ambiguous" or attachment["status"] != "ambiguous" or
                request["expires_at"] <= now() or attachment["expires_at"] <= now()):
            raise IntakeCorrelationError("The selected request and attachment are not a current ambiguous pair for this sender.")
        intake_id = request["intake_id"] or str(uuid.uuid4())
        connection.execute("UPDATE pending_intake_requests SET status='matched',matched_attachment_id=?,intake_id=? WHERE request_id=?",
                           (attachment_id, intake_id, request_id))
        connection.execute("UPDATE pending_intake_attachments SET status='matched',matched_request_id=? WHERE attachment_id=?",
                           (request_id, attachment_id))
        connection.execute("UPDATE pending_intake_requests SET status='pending' WHERE channel=? AND account_id=? AND sender_id=? AND conversation_id=? AND status='ambiguous'",
                           identity)
        connection.execute("UPDATE pending_intake_attachments SET status='pending' WHERE channel=? AND account_id=? AND sender_id=? AND conversation_id=? AND status='ambiguous'",
                           identity)
        remaining = _correlate(connection, identity, now())
        return {"status": "matched", "request_id": request_id,
                "attachment_id": attachment_id, "intake_id": intake_id,
                "additional_intake_id": remaining.get("intake_id") if remaining.get("status") == "matched" else None,
                "remaining_status": remaining.get("status")}


def get_pending_context(db: Path, *, channel: str, account_id: str, sender_id: str,
                        conversation_id: str) -> dict:
    identity = (channel, account_id, sender_id, conversation_id)
    with connect_database(db) as connection:
        completed = connection.execute("""SELECT request_id,intake_id,request_text,quantity,created_at
            FROM pending_intake_requests WHERE channel=? AND account_id=? AND sender_id=?
              AND conversation_id=? AND status='completed' AND created_at>=?
              AND NOT EXISTS (SELECT 1 FROM pending_intake_requests p WHERE p.channel=? AND p.account_id=?
                AND p.sender_id=? AND p.conversation_id=? AND p.status IN ('pending','ambiguous','matched') AND p.expires_at>?)
              AND NOT EXISTS (SELECT 1 FROM pending_intake_attachments a WHERE a.channel=? AND a.account_id=?
                AND a.sender_id=? AND a.conversation_id=? AND a.status IN ('pending','ambiguous','matched') AND a.expires_at>?)
            ORDER BY created_at DESC LIMIT 1""",
            (*identity, (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
             *identity, now(), *identity, now())).fetchone()
        if completed:
            job = connection.execute("SELECT value FROM workflow_settings WHERE key=?",
                                     (f"intake_job:{completed['intake_id']}",)).fetchone()
            file_row = connection.execute("""SELECT f.safe_filename FROM print_jobs j
                LEFT JOIN job_files f ON f.job_id=j.job_id WHERE j.job_id=?""", (job[0],)).fetchone() if job else None
            return {"status": "completed", "intake_id": completed["intake_id"],
                    "job_id": job[0] if job else None,
                    "filename": file_row[0] if file_row else "model.stl",
                    "request_text": completed["request_text"], "quantity": completed["quantity"]}
        requests = connection.execute("""SELECT * FROM pending_intake_requests
            WHERE channel=? AND account_id=? AND sender_id=? AND conversation_id=?
              AND status IN ('pending','ambiguous','matched') AND expires_at>?
            ORDER BY created_at,rowid""", (*identity, now())).fetchall()
        attachments = connection.execute("""SELECT * FROM pending_intake_attachments
            WHERE channel=? AND account_id=? AND sender_id=? AND conversation_id=?
              AND status IN ('pending','ambiguous','matched') AND expires_at>?
            ORDER BY created_at,rowid""", (*identity, now())).fetchall()
        matched = next((row for row in requests if row["status"] == "matched"), None)
        if matched:
            attachment = connection.execute("SELECT * FROM pending_intake_attachments WHERE attachment_id=?", (matched["matched_attachment_id"],)).fetchone()
            return {"status": "matched", "intake_id": matched["intake_id"],
                    "request_text": matched["request_text"], "quantity": matched["quantity"],
                    "filename": attachment["submitted_filename"] if attachment else None}
        ambiguous_requests = [row for row in requests if row["status"] == "ambiguous"]
        ambiguous_attachments = [row for row in attachments if row["status"] == "ambiguous"]
        if ambiguous_requests or ambiguous_attachments:
            return {"status": "ambiguous", "requests": [
                        {"request_id": row["request_id"], "text": row["request_text"], "quantity": row["quantity"]}
                        for row in ambiguous_requests],
                    "attachments": [{"attachment_id": row["attachment_id"], "filename": row["submitted_filename"]}
                                    for row in ambiguous_attachments]}
        if requests:
            return {"status": "awaiting_attachment", "request_count": len(requests)}
        if attachments:
            return {"status": "awaiting_request", "attachment_count": len(attachments)}
        return {"status": "none"}


def matched_pair(db: Path, intake_id: str) -> dict | None:
    with connect_database(db) as connection:
        row = connection.execute("""SELECT r.*,a.attachment_id,a.reference,a.submitted_filename,
                a.spool_name,a.size_bytes,a.sha256,a.status AS attachment_status
            FROM pending_intake_requests r JOIN pending_intake_attachments a
                ON a.attachment_id=r.matched_attachment_id
            WHERE r.intake_id=? AND r.status IN ('matched','completed')""", (intake_id,)).fetchone()
        return dict(row) if row else None


def read_pending_attachment(pending_root: Path, pair: dict) -> bytes:
    root = _private_root(Path(pending_root), create=False)
    path = root / pair["spool_name"]
    if path.parent.resolve(strict=True) != root or path.is_symlink():
        raise UploadError("The pending attachment failed path safety checks.")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != pair["size_bytes"] or metadata.st_size > MAX_UPLOAD_BYTES:
            raise UploadError("The pending attachment is not a bounded regular file.")
        data = os.read(fd, MAX_UPLOAD_BYTES + 1)
    finally:
        os.close(fd)
    if hashlib.sha256(data).hexdigest() != pair["sha256"]:
        raise UploadError("The pending attachment digest changed.")
    _validate_stl(pair["submitted_filename"], data)
    return data


def mark_intake_completed(db: Path, pending_root: Path, intake_id: str, job_id: str) -> None:
    with connect_database(db) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("""SELECT r.request_id,r.matched_attachment_id,a.spool_name
            FROM pending_intake_requests r JOIN pending_intake_attachments a ON a.attachment_id=r.matched_attachment_id
            WHERE r.intake_id=?""", (intake_id,)).fetchone()
        if row is None:
            raise IntakeCorrelationError("The matched intake no longer exists.")
        connection.execute("UPDATE pending_intake_requests SET status='completed' WHERE request_id=?", (row["request_id"],))
        connection.execute("UPDATE pending_intake_attachments SET status='consumed' WHERE attachment_id=?", (row["matched_attachment_id"],))
        connection.execute("INSERT OR REPLACE INTO workflow_settings(key,value) VALUES (?,?)",
                           (f"intake_job:{intake_id}", job_id))
    root = _private_root(Path(pending_root), create=False)
    path = root / row["spool_name"]
    if path.parent.resolve(strict=True) == root and not path.is_symlink():
        path.unlink(missing_ok=True)


def cleanup_expired_intakes(db: Path, pending_root: Path, *, receipt_retention_days: int = 7) -> dict:
    """Expire abandoned event pairs and unlink only regular files inside the private spool."""
    if not 1 <= receipt_retention_days <= 90:
        raise ValueError("Receipt retention must be between 1 and 90 days.")
    root = _private_root(Path(pending_root), create=True)
    expired_files: list[str] = []
    referenced_files: set[str] = set()
    with connect_database(db) as connection:
        connection.execute("BEGIN IMMEDIATE")
        instant = now()
        requests = connection.execute("SELECT request_id FROM pending_intake_requests WHERE status IN ('pending','ambiguous','matched') AND expires_at<=?", (instant,)).fetchall()
        attachments = connection.execute("SELECT attachment_id,spool_name FROM pending_intake_attachments WHERE status IN ('pending','ambiguous','matched') AND expires_at<=?", (instant,)).fetchall()
        consumed_files = connection.execute("SELECT spool_name FROM pending_intake_attachments WHERE status='consumed'").fetchall()
        referenced_files = {row[0] for row in connection.execute("SELECT spool_name FROM pending_intake_attachments WHERE status IN ('pending','ambiguous','matched')").fetchall()}
        connection.executemany("DELETE FROM pending_intake_requests WHERE request_id=?", [(row["request_id"],) for row in requests])
        connection.executemany("DELETE FROM pending_intake_attachments WHERE attachment_id=?", [(row["attachment_id"],) for row in attachments])
        expired_files = [row["spool_name"] for row in attachments] + [row["spool_name"] for row in consumed_files]
        receipt_cutoff = (datetime.now(timezone.utc) - timedelta(days=receipt_retention_days)).isoformat()
        deleted_receipts = connection.execute("DELETE FROM inbound_event_receipts WHERE created_at<?", (receipt_cutoff,)).rowcount
    removed = 0
    for spool_name in expired_files:
        path = root / spool_name
        try:
            if path.parent.resolve(strict=True) == root and not path.is_symlink() and path.is_file():
                path.unlink()
                removed += 1
        except FileNotFoundError:
            pass
    orphan_cutoff = datetime.now(timezone.utc).timestamp() - DEFAULT_TTL_MINUTES * 60
    orphan_files_removed = 0
    for path in root.glob("*.pending"):
        if path.name in referenced_files or path.is_symlink():
            continue
        try:
            metadata = path.stat(follow_symlinks=False)
            if stat.S_ISREG(metadata.st_mode) and metadata.st_mtime <= orphan_cutoff:
                path.unlink()
                orphan_files_removed += 1
        except FileNotFoundError:
            continue
    return {"expired_requests": len(requests), "expired_attachments": len(attachments),
            "removed_files": removed, "orphan_files_removed": orphan_files_removed,
            "deleted_receipts": deleted_receipts}
