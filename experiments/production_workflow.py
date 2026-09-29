#!/usr/bin/env python3
"""Persisted, identity-authorized manual production workflow.

The adapter boundary records operator-confirmed actions. It does not control
printer hardware; adapters can be added later without changing job state rules.
"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from experiments.farm_domain import connect_database, now
from experiments.identity_workflow import AuthorizationError, require_capability


class PrinterAdapter(ABC):
    """Adapter contract for printer assignment and production confirmations."""

    adapter_id: str

    @abstractmethod
    def assignment_details(self) -> dict[str, str]:
        """Describe how the assignment was confirmed."""

    @abstractmethod
    def action_details(self, action: str) -> dict[str, str]:
        """Describe the adapter's handling of a human-confirmed action."""


class ManualPrinterAdapter(PrinterAdapter):
    """A human physically operates the printer and confirms each action."""

    adapter_id = "manual"

    def assignment_details(self) -> dict[str, str]:
        return {"adapter_id": self.adapter_id, "execution": "operator_confirmed"}

    def action_details(self, action: str) -> dict[str, str]:
        if action not in {"start", "completed", "failed"}:
            raise ValueError("Unsupported manual printer action.")
        return {"adapter_id": self.adapter_id, "execution": "operator_confirmed", "action": action}


ADAPTERS: dict[str, PrinterAdapter] = {"manual": ManualPrinterAdapter()}
STATUS_TO_DB = {
    "READY_FOR_PRODUCTION": "queued",
    "IN_PROGRESS": "in_progress",
    "COMPLETED": "completed",
    "FAILED": "failed",
}
DB_TO_STATUS = {value: key for key, value in STATUS_TO_DB.items()}


@contextmanager
def _connection(db: Path):
    connection = connect_database(db)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _require_staff(principal: dict, capability: str = "job.read_any") -> None:
    require_capability(principal, capability)


def _job(connection: sqlite3.Connection, job_id: str) -> sqlite3.Row:
    row = connection.execute("""SELECT j.job_id,j.status,j.printer_id,j.order_id,
        o.status AS order_status,o.customer_user_id,o.quantity,o.request_summary,
        q.status AS quote_status,p.name AS printer_name,p.adapter_id
        FROM print_jobs j JOIN orders o ON o.order_id=j.order_id
        JOIN quotes q ON q.quote_id=o.quote_id
        LEFT JOIN printers p ON p.printer_id=j.printer_id
        WHERE j.job_id=?""", (job_id,)).fetchone()
    if row is None:
        raise ValueError("The job does not exist.")
    return row


def _ready(row: sqlite3.Row) -> bool:
    return (row["status"] == "queued" and row["order_status"] in {"approved", "queued"}
            and row["quote_status"] == "approved")


def _event(connection: sqlite3.Connection, job_id: str, principal: dict,
           event_type: str, details: dict[str, Any]) -> None:
    connection.execute("""INSERT INTO job_events
        (event_id,job_id,actor_user_id,event_type,details_json,created_at)
        VALUES (?,?,?,?,?,?)""",
        (str(uuid.uuid4()), job_id, principal["user_id"], event_type,
         json.dumps(details, sort_keys=True), now()))


def _job_view(row: sqlite3.Row) -> dict:
    return {"job_id": row["job_id"], "status": DB_TO_STATUS.get(row["status"], row["status"].upper()),
            "printer_id": row["printer_id"], "printer_name": row["printer_name"],
            "adapter_id": row["adapter_id"], "order_id": row["order_id"],
            "customer_user_id": row["customer_user_id"], "quantity": row["quantity"],
            "request_summary": row["request_summary"], "quote_status": row["quote_status"]}


def list_ready_jobs(db: Path, principal: dict) -> list[dict]:
    """List only jobs that have passed persisted quote/order approval."""
    _require_staff(principal)
    with _connection(db) as connection:
        rows = connection.execute("""SELECT j.job_id,j.status,j.printer_id,j.order_id,
            o.status AS order_status,o.customer_user_id,o.quantity,o.request_summary,
            q.status AS quote_status,p.name AS printer_name,p.adapter_id
            FROM print_jobs j JOIN orders o ON o.order_id=j.order_id
            JOIN quotes q ON q.quote_id=o.quote_id
            LEFT JOIN printers p ON p.printer_id=j.printer_id
            WHERE j.status='queued' AND o.status IN ('approved','queued') AND q.status='approved'
            ORDER BY j.created_at,j.job_id""").fetchall()
        return [_job_view(row) for row in rows]


def mark_ready_for_production(db: Path, principal: dict, job_id: str) -> dict:
    """Queue an already approved order without altering quote approval."""
    _require_staff(principal, "job.status.update")
    with _connection(db) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = _job(connection, job_id)
        if row["quote_status"] != "approved" or row["order_status"] not in {"approved", "queued"}:
            raise ValueError("A job requires an approved quote and approved order before production readiness.")
        if row["status"] == "queued" and row["order_status"] == "queued":
            return _job_view(row)
        if row["status"] != "created":
            raise ValueError("Only a newly created approved job may become READY_FOR_PRODUCTION.")
        connection.execute("UPDATE print_jobs SET status='queued' WHERE job_id=?", (job_id,))
        connection.execute("UPDATE orders SET status='queued' WHERE order_id=?", (row["order_id"],))
        _event(connection, job_id, principal, "ready_for_production", {
            "quote_status": row["quote_status"], "order_status": row["order_status"]})
        return _job_view(_job(connection, job_id))


def inspect_production_job(db: Path, principal: dict, job_id: str) -> dict:
    """Read persisted state; customers can read only their own job."""
    with _connection(db) as connection:
        row = _job(connection, job_id)
        if "job.read_any" not in principal.get("capabilities", ()):
            if ("request.read_own" not in principal.get("capabilities", ())
                    or row["customer_user_id"] != principal.get("user_id")):
                raise AuthorizationError("The user is not authorized to inspect this job.")
        return _job_view(row)


def assign_printer(db: Path, principal: dict, job_id: str, printer_id: str) -> dict:
    """Assign an active configured printer to an approved ready job."""
    _require_staff(principal, "job.status.update")
    with _connection(db) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = _job(connection, job_id)
        if not _ready(row):
            raise ValueError("Only a READY_FOR_PRODUCTION job may be assigned.")
        printer = connection.execute(
            "SELECT printer_id,name,adapter_id FROM printers WHERE printer_id=? AND active=1",
            (printer_id,)).fetchone()
        if printer is None:
            raise ValueError("The configured printer does not exist or is inactive.")
        adapter = ADAPTERS.get(printer["adapter_id"])
        if adapter is None:
            raise ValueError("The configured printer adapter is not available.")
        if row["printer_id"] == printer_id:
            return _job_view(_job(connection, job_id))
        connection.execute("UPDATE print_jobs SET printer_id=? WHERE job_id=?", (printer_id, job_id))
        _event(connection, job_id, principal, "printer_assigned", {
            "printer_id": printer_id, "printer_name": printer["name"],
            **adapter.assignment_details()})
        return _job_view(_job(connection, job_id))


def start_job(db: Path, principal: dict, job_id: str) -> dict:
    """Record the operator's confirmation that the physical print has started."""
    _require_staff(principal, "job.status.update")
    with _connection(db) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = _job(connection, job_id)
        if not _ready(row):
            raise ValueError("Only a READY_FOR_PRODUCTION job may start.")
        if not row["printer_id"]:
            raise ValueError("Assign a configured printer before starting the job.")
        adapter = ADAPTERS.get(row["adapter_id"])
        if adapter is None:
            raise ValueError("The configured printer adapter is not available.")
        details = {"printer_id": row["printer_id"], "printer_name": row["printer_name"],
                   **adapter.action_details("start")}
        connection.execute("UPDATE print_jobs SET status='in_progress' WHERE job_id=?", (job_id,))
        connection.execute("UPDATE orders SET status='in_progress' WHERE order_id=?", (row["order_id"],))
        _event(connection, job_id, principal, "production_started", details)
        return _job_view(_job(connection, job_id))


def finish_job(db: Path, principal: dict, job_id: str, outcome: str, details: str = "") -> dict:
    """Record a completed or failed operator-confirmed print."""
    _require_staff(principal, "job.status.update")
    normalized = outcome.strip().upper() if isinstance(outcome, str) else ""
    if normalized not in {"COMPLETED", "FAILED"}:
        raise ValueError("The production outcome must be COMPLETED or FAILED.")
    with _connection(db) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = _job(connection, job_id)
        if row["status"] != "in_progress":
            raise ValueError("Only an IN_PROGRESS job may be completed or failed.")
        adapter = ADAPTERS.get(row["adapter_id"])
        if adapter is None:
            raise ValueError("The configured printer adapter is not available.")
        status = STATUS_TO_DB[normalized]
        connection.execute("UPDATE print_jobs SET status=? WHERE job_id=?", (status, job_id))
        connection.execute("UPDATE orders SET status=? WHERE order_id=?", (status, row["order_id"]))
        event_type = "production_completed" if normalized == "COMPLETED" else "production_failed"
        _event(connection, job_id, principal, event_type,
               {**adapter.action_details(normalized.lower()), "details": str(details)[:500]})
        return _job_view(_job(connection, job_id))
