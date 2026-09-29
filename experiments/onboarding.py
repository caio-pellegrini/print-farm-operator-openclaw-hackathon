"""Persistent, one-question-at-a-time first-run setup for a local farm."""

import json
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from experiments.farm_domain import connect_database, now
from experiments.identity_workflow import CAPABILITIES

IDENTITY = "farm-installation|owner"
QUESTIONS = [
    ("printer_count", "How many printers do you operate? (1–50)"),
    ("printer_models", "List each printer model, separated by commas."),
    ("nozzle_sizes", "List the nozzle size for each printer in order, in mm. For multiple printers, separate sizes with semicolons. Use ? when unknown."),
    ("staff_mode", "Do you operate alone, or do you have other operators? (alone/operators)"),
    ("primary_slicer", "Which slicer do you use most?"),
    ("primary_material", "Which material do you print most? (for example PLA)"),
    ("customer_messaging_later", "Would you like customer messaging integration later? (yes/no)"),
]


def _get(connection: sqlite3.Connection, key: str, default=None):
    row = connection.execute("SELECT value FROM workflow_settings WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _set(connection: sqlite3.Connection, key: str, value) -> None:
    connection.execute("INSERT INTO workflow_settings(key,value) VALUES(?,?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, json.dumps(value, sort_keys=True)))


def _parse_answer(step: str, answer: str, data: dict) -> object:
    answer = answer.strip()
    if step == "printer_count":
        if not answer.isdigit() or not 1 <= int(answer) <= 50:
            raise ValueError("Enter a whole number from 1 to 50.")
        return int(answer)
    if step in {"printer_models", "nozzle_sizes"}:
        if step == "nozzle_sizes" and data["printer_count"] == 1:
            values = [answer]
        elif step == "nozzle_sizes" and ";" in answer:
            values = [part.strip() for part in answer.split(";")]
        else:
            values = [part.strip() for part in answer.split(",")]
        if len(values) != data["printer_count"] or any(not value for value in values):
            raise ValueError(f"Enter exactly {data['printer_count']} comma-separated values.")
        if step == "nozzle_sizes":
            parsed = []
            for value in values:
                match = re.fullmatch(r"(?:0?[.,]\d+|\d+(?:[.,]\d+)?)(?:\s*mm)?", value, re.IGNORECASE)
                if match:
                    size = float(re.sub(r"\s*mm\s*$", "", value, flags=re.IGNORECASE).replace(",", "."))
                    if size <= 0:
                        raise ValueError("Nozzle sizes must be positive, or ? when unknown.")
                    parsed.append(size)
                elif value.strip().lower() in {"?", "unknown", "not known", "não sei", "nao sei"}:
                    parsed.append(None)
                else:
                    raise ValueError("Enter a nozzle size in mm, or ? when unknown.")
            return parsed
        return values
    if step == "staff_mode":
        normalized = answer.lower()
        if normalized not in {"alone", "operators"}:
            raise ValueError("Reply alone or operators.")
        return normalized
    if step == "customer_messaging_later":
        normalized = answer.lower()
        if normalized not in {"yes", "no"}:
            raise ValueError("Reply yes or no.")
        return normalized == "yes"
    if not answer or len(answer) > 120:
        raise ValueError("Enter a value of 1 to 120 characters.")
    return answer


def advance_onboarding(db: Path, answer: str | None = None) -> dict:
    """Return the next question, saving each answer immediately in SQLite."""
    with closing(connect_database(db)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        config = _get(connection, "onboarding_draft", {})
        index = int(_get(connection, "onboarding_step", 0))
        if _get(connection, "onboarding_complete", False):
            return {"complete": True, "configuration": _get(connection, "farm_onboarding", {}),
                    "printers": [dict(row) for row in connection.execute(
                        "SELECT printer_id,name,model,nozzle_diameter_mm,adapter_id FROM printers WHERE active=1 ORDER BY name")]} 
        if answer is not None:
            if index >= len(QUESTIONS):
                raise ValueError("Onboarding is already complete.")
            key, _ = QUESTIONS[index]
            config[key] = _parse_answer(key, answer, config)
            _set(connection, "onboarding_draft", config)
            index += 1
            _set(connection, "onboarding_step", index)
        if index < len(QUESTIONS):
            key, question = QUESTIONS[index]
            return {"complete": False, "step": index + 1, "total_steps": len(QUESTIONS),
                    "field": key, "question": question}

        models = config["printer_models"]
        nozzles = config["nozzle_sizes"]
        for index, (model, nozzle) in enumerate(zip(models, nozzles), start=1):
            # Stable names make retries idempotent and preserve existing jobs.
            name = f"Printer {index}"
            existing = connection.execute("SELECT printer_id FROM printers WHERE name=?", (name,)).fetchone()
            if not existing:
                connection.execute("""INSERT INTO printers
                    (printer_id,name,manufacturer,model,nozzle_diameter_mm,notes,created_at,adapter_id)
                    VALUES(?,?,?, ?, ?, ?, ?, 'manual')""",
                    (str(uuid.uuid4()), name, "", model, nozzle,
                     "Added by first-run onboarding.", now()))
        roles = ["OWNER"] + (["OPERATOR"] if config["staff_mode"] == "alone" else [])
        user = connection.execute("SELECT user_id FROM farm_users WHERE external_identity=?", (IDENTITY,)).fetchone()
        user_id = user[0] if user else str(uuid.uuid4())
        if not user:
            connection.execute("INSERT INTO farm_users(user_id,external_identity,display_name,created_at) "
                               "VALUES(?,?,?,?)", (user_id, IDENTITY, "Farm owner", now()))
            connection.execute("INSERT INTO farm_user_identities(external_identity,user_id,linked_by_user_id,created_at) "
                               "VALUES(?,?,NULL,?)", (IDENTITY, user_id, now()))
        connection.execute("DELETE FROM user_roles WHERE user_id=?", (user_id,))
        connection.executemany("INSERT INTO user_roles(user_id,role,created_at) VALUES(?,?,?)",
                               [(user_id, role, now()) for role in roles])
        final = {**config, "roles": roles}
        _set(connection, "farm_onboarding", final)
        _set(connection, "primary_slicer", config["primary_slicer"])
        _set(connection, "primary_material", config["primary_material"])
        _set(connection, "customer_messaging_later", config["customer_messaging_later"])
        _set(connection, "onboarding_complete", True)
        return {"complete": True, "configuration": final, "user_id": user_id,
                "printers": [dict(row) for row in connection.execute(
                    "SELECT printer_id,name,model,nozzle_diameter_mm,adapter_id FROM printers WHERE active=1 ORDER BY name")]}


def get_farm_configuration(db: Path) -> dict:
    """Read the canonical persisted onboarding state and the farm's active printers."""
    with closing(connect_database(db)) as connection:
        complete = bool(_get(connection, "onboarding_complete", False))
        config = _get(connection, "farm_onboarding", {}) or {}
        draft = _get(connection, "onboarding_draft", {}) or {}
        step = int(_get(connection, "onboarding_step", 0))
        user = connection.execute(
            "SELECT user_id FROM farm_users WHERE external_identity=?", (IDENTITY,)
        ).fetchone()
        roles = []
        if user:
            roles = [row[0] for row in connection.execute(
                "SELECT role FROM user_roles WHERE user_id=? ORDER BY role", (user["user_id"],)
            )]
        printers = [dict(row) for row in connection.execute(
            "SELECT printer_id,name,manufacturer,model,nozzle_diameter_mm,adapter_id "
            "FROM printers WHERE active=1 ORDER BY name"
        )]

    status = "complete" if complete else "in_progress" if draft or step else "not_started"
    return {
        "onboarding": {
            "status": status,
            "complete": complete,
            "step": step,
            "total_steps": len(QUESTIONS),
            "mode": config.get("staff_mode", draft.get("staff_mode")),
        },
        "printer_count": config.get("printer_count", len(printers)),
        "printers": printers,
        "primary_slicer": config.get("primary_slicer"),
        "primary_material": config.get("primary_material"),
        "customer_messaging_later": config.get("customer_messaging_later"),
        "roles": roles,
    }


def local_staff_principal(db: Path) -> dict:
    """Resolve the provisioned local authenticated owner, never caller-supplied roles."""
    with closing(connect_database(db)) as connection:
        user = connection.execute("SELECT user_id,display_name FROM farm_users WHERE external_identity=?",
                                  (IDENTITY,)).fetchone()
        if not user:
            raise PermissionError("Complete farm onboarding before using production tools.")
        roles = [row[0] for row in connection.execute(
            "SELECT role FROM user_roles WHERE user_id=? ORDER BY role", (user["user_id"],))]
    capabilities = sorted(set().union(*(CAPABILITIES[role] for role in roles))) if roles else []
    return {"user_id": user["user_id"], "display_name": user["display_name"],
            "external_identity": IDENTITY, "roles": roles, "capabilities": capabilities}
