#!/usr/bin/env python3
"""Persistent farm configuration and schema migrations.

This module owns configuration records. Slicer execution remains in the adapter
module and printer records here are metadata only.
"""

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 10
ROOT = Path(__file__).resolve().parents[1]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _migrate_1(connection: sqlite3.Connection) -> None:
    # Adopt databases created by Stage 1/2's inline CREATE TABLE statements.
    connection.execute("""CREATE TABLE IF NOT EXISTS stl_analyses (
        analysis_id TEXT PRIMARY KEY, filename TEXT NOT NULL,
        dimensions_json TEXT NOT NULL, analysis_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status = 'completed'))""")
    connection.execute("""CREATE TABLE IF NOT EXISTS production_estimates (
        estimate_id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL,
        filename TEXT NOT NULL, profile TEXT NOT NULL, slicing_json TEXT NOT NULL,
        estimated_material_g REAL NOT NULL,
        estimated_print_time_seconds INTEGER NOT NULL, quote_json TEXT NOT NULL,
        business_config_json TEXT NOT NULL, created_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status = 'completed'))""")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(production_estimates)")}
    if "request_summary" not in columns:
        connection.execute(
            "ALTER TABLE production_estimates ADD COLUMN request_summary TEXT NOT NULL DEFAULT ''"
        )


def _migrate_2(connection: sqlite3.Connection) -> None:
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS materials (
        material_id TEXT NOT NULL,
        version INTEGER NOT NULL CHECK (version > 0),
        name TEXT NOT NULL,
        density_value REAL NOT NULL CHECK (density_value > 0),
        density_unit TEXT NOT NULL CHECK (density_unit = 'g/cm3'),
        density_source TEXT NOT NULL,
        density_source_ref TEXT NOT NULL DEFAULT '',
        cost_per_kg REAL NOT NULL CHECK (cost_per_kg >= 0),
        selling_price_per_kg REAL CHECK (selling_price_per_kg IS NULL OR selling_price_per_kg >= 0),
        currency TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
        created_at TEXT NOT NULL,
        PRIMARY KEY (material_id, version)
    );
    CREATE TABLE IF NOT EXISTS printers (
        printer_id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        manufacturer TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        build_x_mm REAL CHECK (build_x_mm IS NULL OR build_x_mm > 0),
        build_y_mm REAL CHECK (build_y_mm IS NULL OR build_y_mm > 0),
        build_z_mm REAL CHECK (build_z_mm IS NULL OR build_z_mm > 0),
        nozzle_diameter_mm REAL CHECK (nozzle_diameter_mm IS NULL OR nozzle_diameter_mm > 0),
        notes TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS slicer_installations (
        installation_id TEXT PRIMARY KEY,
        slicer_id TEXT NOT NULL,
        version TEXT NOT NULL,
        executable TEXT NOT NULL DEFAULT '',
        execution_status TEXT NOT NULL CHECK (execution_status IN ('unverified', 'available', 'unavailable')),
        source TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (slicer_id, version, executable)
    );
    CREATE TABLE IF NOT EXISTS slicer_profiles (
        profile_id TEXT NOT NULL,
        version INTEGER NOT NULL CHECK (version > 0),
        display_name TEXT NOT NULL,
        installation_id TEXT NOT NULL REFERENCES slicer_installations(installation_id),
        printer_id TEXT REFERENCES printers(printer_id),
        source TEXT NOT NULL,
        source_digest TEXT NOT NULL,
        source_files_json TEXT NOT NULL,
        machine_preset_id TEXT NOT NULL,
        process_preset_id TEXT NOT NULL,
        material_preset_id TEXT NOT NULL,
        nozzle_diameter_mm REAL CHECK (nozzle_diameter_mm IS NULL OR nozzle_diameter_mm > 0),
        execution_status TEXT NOT NULL CHECK (execution_status IN ('unverified', 'available', 'blocked')),
        quote_status TEXT NOT NULL CHECK (quote_status IN ('blocked', 'needs_review', 'approved')),
        validation_findings_json TEXT NOT NULL,
        material_id TEXT,
        material_version INTEGER,
        created_at TEXT NOT NULL,
        PRIMARY KEY (profile_id, version),
        FOREIGN KEY (material_id, material_version) REFERENCES materials(material_id, version)
    );
    CREATE TABLE IF NOT EXISTS slicer_profile_validations (
        validation_id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        profile_version INTEGER NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('blocked', 'needs_review', 'approved')),
        findings_json TEXT NOT NULL,
        evidence_ref TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY (profile_id, profile_version) REFERENCES slicer_profiles(profile_id, version)
    );
    CREATE TABLE IF NOT EXISTS business_configurations (
        version INTEGER PRIMARY KEY CHECK (version > 0),
        currency TEXT NOT NULL,
        machine_hour_cost REAL NOT NULL CHECK (machine_hour_cost >= 0),
        energy_kwh_per_hour REAL NOT NULL CHECK (energy_kwh_per_hour >= 0),
        energy_cost_per_kwh REAL NOT NULL CHECK (energy_cost_per_kwh >= 0),
        minimum_margin_fraction REAL NOT NULL CHECK (minimum_margin_fraction >= 0 AND minimum_margin_fraction < 1),
        minimum_job_fee REAL NOT NULL CHECK (minimum_job_fee >= 0),
        setup_fee REAL NOT NULL DEFAULT 0 CHECK (setup_fee >= 0),
        source TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
        created_at TEXT NOT NULL
    );
    CREATE UNIQUE INDEX IF NOT EXISTS one_active_business_configuration
        ON business_configurations(active) WHERE active = 1;
    """)


def _migrate_3(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(production_estimates)")}
    additions = {
        "quote_readiness_status": "TEXT NOT NULL DEFAULT 'legacy_unverified'",
        "profile_version": "INTEGER",
        "material_id": "TEXT",
        "material_version": "INTEGER",
        "business_config_version": "INTEGER",
    }
    for name, declaration in additions.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE production_estimates ADD COLUMN {name} {declaration}")


def _migrate_4(connection: sqlite3.Connection) -> None:
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS farm_users (
        user_id TEXT PRIMARY KEY,
        external_identity TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS user_roles (
        user_id TEXT NOT NULL REFERENCES farm_users(user_id),
        role TEXT NOT NULL CHECK (role IN ('CUSTOMER','OPERATOR','OWNER')),
        created_at TEXT NOT NULL,
        PRIMARY KEY (user_id, role)
    );
    CREATE TABLE IF NOT EXISTS slicer_execution_checks (
        check_id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        profile_version INTEGER NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('available','blocked')),
        evidence_ref TEXT NOT NULL,
        findings_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (profile_id,profile_version) REFERENCES slicer_profiles(profile_id,version)
    );
    CREATE TABLE IF NOT EXISTS printer_profiles (
        printer_profile_id TEXT NOT NULL,
        version INTEGER NOT NULL CHECK (version > 0),
        printer_id TEXT NOT NULL REFERENCES printers(printer_id),
        slicer_profile_id TEXT NOT NULL,
        slicer_profile_version INTEGER NOT NULL,
        material_id TEXT NOT NULL,
        material_version INTEGER NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        PRIMARY KEY (printer_profile_id,version),
        FOREIGN KEY (slicer_profile_id,slicer_profile_version) REFERENCES slicer_profiles(profile_id,version),
        FOREIGN KEY (material_id,material_version) REFERENCES materials(material_id,version)
    );
    CREATE TABLE IF NOT EXISTS quotes (
        quote_id TEXT PRIMARY KEY,
        analysis_id TEXT NOT NULL REFERENCES stl_analyses(analysis_id),
        estimate_id TEXT REFERENCES production_estimates(estimate_id),
        customer_user_id TEXT REFERENCES farm_users(user_id),
        status TEXT NOT NULL CHECK (status IN ('draft','pending','approved','rejected','expired')),
        quote_snapshot_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY,
        quote_id TEXT NOT NULL REFERENCES quotes(quote_id),
        customer_user_id TEXT REFERENCES farm_users(user_id),
        quantity INTEGER NOT NULL CHECK (quantity > 0),
        status TEXT NOT NULL CHECK (status IN ('pending','approved','queued','in_progress','completed','failed','cancelled')),
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS print_jobs (
        job_id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL REFERENCES orders(order_id),
        printer_id TEXT REFERENCES printers(printer_id),
        printer_profile_id TEXT,
        printer_profile_version INTEGER,
        status TEXT NOT NULL CHECK (status IN ('created','queued','in_progress','completed','failed','cancelled')),
        created_at TEXT NOT NULL,
        FOREIGN KEY (printer_profile_id,printer_profile_version) REFERENCES printer_profiles(printer_profile_id,version)
    );
    CREATE TABLE IF NOT EXISTS job_events (
        event_id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL REFERENCES print_jobs(job_id),
        actor_user_id TEXT REFERENCES farm_users(user_id),
        event_type TEXT NOT NULL,
        details_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)


def _migrate_5(connection: sqlite3.Connection) -> None:
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS slicer_runs (
        run_id TEXT PRIMARY KEY,
        analysis_id TEXT NOT NULL REFERENCES stl_analyses(analysis_id),
        filename TEXT NOT NULL,
        profile_id TEXT NOT NULL,
        profile_version INTEGER,
        slicer_result_json TEXT NOT NULL,
        execution_status TEXT NOT NULL CHECK (execution_status IN ('completed','failed')),
        quote_readiness_status TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)


def _migrate_6(connection: sqlite3.Connection) -> None:
    # Stage 5 adds identity ownership and the trusted upload-reference handoff.
    columns = {row[1] for row in connection.execute("PRAGMA table_info(stl_analyses)")}
    if "owner_user_id" not in columns:
        connection.execute("ALTER TABLE stl_analyses ADD COLUMN owner_user_id TEXT REFERENCES farm_users(user_id)")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(orders)")}
    if "request_summary" not in columns:
        connection.execute("ALTER TABLE orders ADD COLUMN request_summary TEXT NOT NULL DEFAULT ''")
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS trusted_uploads (
        upload_ref TEXT PRIMARY KEY,
        submitted_filename TEXT NOT NULL,
        spool_name TEXT NOT NULL UNIQUE,
        size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
        sha256 TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('available','consumed','expired')),
        expires_at TEXT NOT NULL,
        job_id TEXT REFERENCES print_jobs(job_id),
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS job_files (
        job_id TEXT PRIMARY KEY REFERENCES print_jobs(job_id),
        owner_user_id TEXT NOT NULL REFERENCES farm_users(user_id),
        safe_filename TEXT NOT NULL,
        relative_path TEXT NOT NULL UNIQUE,
        size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
        sha256 TEXT NOT NULL,
        retention_until TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS workflow_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS orders_customer_user_idx ON orders(customer_user_id);
    CREATE INDEX IF NOT EXISTS job_events_actor_idx ON job_events(actor_user_id);
    """)


def _migrate_7(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(trusted_uploads)")}
    if "owner_user_id" not in columns:
        connection.execute("ALTER TABLE trusted_uploads ADD COLUMN owner_user_id TEXT REFERENCES farm_users(user_id)")


def _migrate_8(connection: sqlite3.Connection) -> None:
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS farm_user_identities (
        external_identity TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES farm_users(user_id) ON DELETE CASCADE,
        linked_by_user_id TEXT REFERENCES farm_users(user_id),
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_farm_user_identities_user
        ON farm_user_identities(user_id);
    INSERT OR IGNORE INTO farm_user_identities(external_identity,user_id,linked_by_user_id,created_at)
        SELECT external_identity,user_id,NULL,created_at FROM farm_users;
    """)


def _migrate_9(connection: sqlite3.Connection) -> None:
    """Persist channel-neutral intake events and a generic model-source link."""
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS model_sources (
        source_id TEXT PRIMARY KEY,
        source_type TEXT NOT NULL CHECK (source_type IN ('ATTACHMENT','URL')),
        reference TEXT NOT NULL,
        original_filename TEXT,
        source_url TEXT,
        digest TEXT,
        resolution_status TEXT NOT NULL CHECK (resolution_status IN ('pending','resolved','failed')),
        created_at TEXT NOT NULL,
        CHECK ((source_type='ATTACHMENT' AND source_url IS NULL) OR
               (source_type='URL' AND source_url IS NOT NULL))
    );
    CREATE TABLE IF NOT EXISTS pending_intake_requests (
        request_id TEXT PRIMARY KEY,
        channel TEXT NOT NULL,
        account_id TEXT NOT NULL,
        sender_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        request_text TEXT NOT NULL,
        quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 20),
        status TEXT NOT NULL CHECK (status IN ('pending','ambiguous','matched','completed','expired')),
        matched_attachment_id TEXT,
        intake_id TEXT UNIQUE,
        event_timestamp TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS pending_request_correlation_idx
        ON pending_intake_requests(channel,account_id,sender_id,conversation_id,status,created_at);
    CREATE TABLE IF NOT EXISTS pending_intake_attachments (
        attachment_id TEXT PRIMARY KEY,
        channel TEXT NOT NULL,
        account_id TEXT NOT NULL,
        sender_id TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        reference TEXT NOT NULL UNIQUE,
        submitted_filename TEXT NOT NULL,
        spool_name TEXT NOT NULL UNIQUE,
        size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
        sha256 TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending','ambiguous','matched','consumed','expired')),
        matched_request_id TEXT,
        event_timestamp TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS pending_attachment_correlation_idx
        ON pending_intake_attachments(channel,account_id,sender_id,conversation_id,status,created_at);
    CREATE TABLE IF NOT EXISTS inbound_event_receipts (
        channel TEXT NOT NULL,
        account_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(channel,account_id,message_id)
    );
    CREATE INDEX IF NOT EXISTS inbound_event_receipts_created_idx ON inbound_event_receipts(created_at);
    """)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(orders)")}
    if "model_source_id" not in columns:
        connection.execute("ALTER TABLE orders ADD COLUMN model_source_id TEXT REFERENCES model_sources(source_id)")
    if "intake_id" not in columns:
        connection.execute("ALTER TABLE orders ADD COLUMN intake_id TEXT")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS orders_intake_id_idx ON orders(intake_id) WHERE intake_id IS NOT NULL")


def _migrate_10(connection: sqlite3.Connection) -> None:
    """Identify the adapter that represents each configured physical printer."""
    columns = {row[1] for row in connection.execute("PRAGMA table_info(printers)")}
    if "adapter_id" not in columns:
        # Existing printers remain usable as manually operated printers.
        connection.execute("ALTER TABLE printers ADD COLUMN adapter_id TEXT NOT NULL DEFAULT 'manual'")


def connect_database(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"Database schema {version} is newer than supported {SCHEMA_VERSION}.")
        if version < 1:
            _migrate_1(connection)
            connection.execute("PRAGMA user_version = 1")
            version = 1
        if version < 2:
            _migrate_2(connection)
            connection.execute("PRAGMA user_version = 2")
            version = 2
        if version < 3:
            _migrate_3(connection)
            connection.execute("PRAGMA user_version = 3")
            version = 3
        if version < 4:
            _migrate_4(connection)
            connection.execute("PRAGMA user_version = 4")
            version = 4
        if version < 5:
            _migrate_5(connection)
            connection.execute("PRAGMA user_version = 5")
            version = 5
        if version < 6:
            _migrate_6(connection)
            connection.execute("PRAGMA user_version = 6")
            version = 6
        if version < 7:
            _migrate_7(connection)
            connection.execute("PRAGMA user_version = 7")
            version = 7
        if version < 8:
            _migrate_8(connection)
            connection.execute("PRAGMA user_version = 8")
            version = 8
        if version < 9:
            _migrate_9(connection)
            connection.execute("PRAGMA user_version = 9")
            version = 9
        if version < 10:
            _migrate_10(connection)
            connection.execute("PRAGMA user_version = 10")
        connection.commit()
    except Exception:
        connection.rollback()
        connection.close()
        raise
    return connection


def digest_files(files: dict[str, bytes]) -> str:
    """Hash a stable filename/content manifest, including names and separators."""
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def add_material(db: Path, *, name: str, density: float, density_source: str,
                 density_source_ref: str, cost_per_kg: float, selling_price_per_kg: float | None,
                 currency: str, material_id: str | None = None) -> dict:
    if density <= 0 or cost_per_kg < 0 or (selling_price_per_kg is not None and selling_price_per_kg < 0):
        raise ValueError("Density must be positive and material prices cannot be negative.")
    if not name.strip() or not density_source.strip() or not currency.strip():
        raise ValueError("Material name, density source, and currency are required.")
    material_id = material_id or str(uuid.uuid4())
    with connect_database(db) as connection:
        version = connection.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM materials WHERE material_id=?", (material_id,)
        ).fetchone()[0]
        connection.execute("UPDATE materials SET active=0 WHERE material_id=?", (material_id,))
        connection.execute("""INSERT INTO materials
          (material_id, version, name, density_value, density_unit, density_source,
           density_source_ref, cost_per_kg, selling_price_per_kg, currency, created_at)
          VALUES (?, ?, ?, ?, 'g/cm3', ?, ?, ?, ?, ?, ?)""",
          (material_id, version, name.strip(), density, density_source.strip(), density_source_ref,
           cost_per_kg, selling_price_per_kg, currency.upper(), now()))
    return {"material_id": material_id, "version": version}


def add_printer(db: Path, *, name: str, manufacturer: str = "", model: str = "",
                build: tuple[float | None, float | None, float | None] = (None, None, None),
                nozzle: float | None = None, notes: str = "", adapter_id: str = "manual") -> dict:
    if not name.strip() or any(v is not None and v <= 0 for v in (*build, nozzle)):
        raise ValueError("Printer name is required and dimensions must be positive when provided.")
    printer_id = str(uuid.uuid4())
    with connect_database(db) as connection:
        connection.execute("""INSERT INTO printers
          (printer_id,name,manufacturer,model,build_x_mm,build_y_mm,build_z_mm,
           nozzle_diameter_mm,notes,created_at,adapter_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
          (printer_id, name.strip(), manufacturer, model, *build, nozzle, notes, now(), adapter_id))
    return {"printer_id": printer_id}


def add_business_configuration(db: Path, *, currency: str, machine_hour_cost: float,
                               energy_kwh_per_hour: float, energy_cost_per_kwh: float,
                               minimum_margin_fraction: float, minimum_job_fee: float,
                               setup_fee: float, source: str) -> dict:
    if min(machine_hour_cost, energy_kwh_per_hour, energy_cost_per_kwh, minimum_job_fee, setup_fee) < 0:
        raise ValueError("Business cost inputs cannot be negative.")
    if not 0 <= minimum_margin_fraction < 1:
        raise ValueError("Minimum margin must be in [0, 1).")
    with connect_database(db) as connection:
        version = connection.execute("SELECT COALESCE(MAX(version),0)+1 FROM business_configurations").fetchone()[0]
        connection.execute("UPDATE business_configurations SET active=0 WHERE active=1")
        connection.execute("""INSERT INTO business_configurations
          (version,currency,machine_hour_cost,energy_kwh_per_hour,energy_cost_per_kwh,
           minimum_margin_fraction,minimum_job_fee,setup_fee,source,created_at)
          VALUES (?,?,?,?,?,?,?,?,?,?)""",
          (version,currency.upper(),machine_hour_cost,energy_kwh_per_hour,energy_cost_per_kwh,
           minimum_margin_fraction,minimum_job_fee,setup_fee,source,now()))
    return {"version": version}


def import_profile(db: Path, *, profile_id: str, display_name: str, slicer_id: str,
                   slicer_version: str, source: str, machine_preset_id: str,
                   process_preset_id: str, material_preset_id: str,
                   source_files: list[Path], nozzle: float | None = None,
                   printer_id: str | None = None, material_id: str | None = None,
                   material_version: int | None = None) -> dict:
    if not all(value.strip() for value in (profile_id, display_name, slicer_id, slicer_version, source,
            machine_preset_id, process_preset_id, material_preset_id)):
        raise ValueError("Profile identity, source, and all three preset identifiers are required.")
    if not source_files or any(not path.is_file() for path in source_files):
        raise ValueError("At least one existing source file is required for a profile digest.")
    if slicer_id == "bambustudio":
        bambu_names = {path.name for path in source_files}
        if bambu_names != {"machine.json", "process.json", "filament.json"} or len(source_files) != 3:
            raise ValueError("A Bambu profile digest requires exactly machine.json, process.json, and filament.json.")
        # Keep the registry digest identical to BambuStudioAdapter's executed
        # bundle digest so the later active/executed digest gate can succeed.
        files = {path.name: path.read_bytes() for path in source_files}
    else:
        files = {str(path.resolve()): path.read_bytes() for path in source_files}
    source_digest = digest_files(files)
    with connect_database(db) as connection:
        installation = connection.execute("SELECT installation_id FROM slicer_installations WHERE slicer_id=? AND version=? ORDER BY created_at DESC LIMIT 1", (slicer_id,slicer_version)).fetchone()
        installation_id = installation["installation_id"] if installation else str(uuid.uuid4())
        if not installation:
            connection.execute("""INSERT INTO slicer_installations
              (installation_id,slicer_id,version,executable,execution_status,source,created_at)
              VALUES (?,?,?,'', 'unverified', ?,?)""",
              (installation_id,slicer_id,slicer_version,source,now()))
        if material_id and (material_version is None or not connection.execute(
                "SELECT 1 FROM materials WHERE material_id=? AND version=? AND active=1",
                (material_id,material_version)).fetchone()):
            raise ValueError("The associated material version must exist and be active.")
        version = connection.execute("SELECT COALESCE(MAX(version),0)+1 FROM slicer_profiles WHERE profile_id=?", (profile_id,)).fetchone()[0]
        connection.execute("""INSERT INTO slicer_profiles
          (profile_id,version,display_name,installation_id,printer_id,source,source_digest,source_files_json,
           machine_preset_id,process_preset_id,material_preset_id,nozzle_diameter_mm,execution_status,
           quote_status,validation_findings_json,material_id,material_version,created_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'unverified','needs_review',?,?,?,?)""",
          (profile_id,version,display_name,installation_id,printer_id,source,source_digest,
           json.dumps([str(path.resolve()) for path in source_files]),machine_preset_id,process_preset_id,
           material_preset_id,nozzle,json.dumps(["Profile imported; execution and quote safety have not been validated."]),
           material_id,material_version,now()))
        findings = ["Profile imported; execution and quote safety have not been validated."]
        connection.execute("""INSERT INTO slicer_profile_validations
          (validation_id,profile_id,profile_version,status,findings_json,evidence_ref,created_at)
          VALUES (?,?,?,'needs_review',?,?,?)""",
          (str(uuid.uuid4()),profile_id,version,json.dumps(findings),source,now()))
    return {"profile_id":profile_id,"version":version,"installation_id":installation_id,
            "source_digest":source_digest,"execution_status":"unverified","quote_status":"needs_review"}


def active_quote_configuration(db: Path, profile_id: str, expected_source_digest: str | None = None) -> dict | None:
    connection = connect_database(db)
    try:
        profile = connection.execute("""SELECT p.profile_id,p.version,p.quote_status,p.execution_status,p.source_digest,p.validation_findings_json,
          p.material_id,p.material_version,m.name,m.density_value,m.density_unit,m.density_source,m.active AS material_active,
          m.density_source_ref,m.cost_per_kg,m.currency AS material_currency
          FROM slicer_profiles p LEFT JOIN materials m
          ON m.material_id=p.material_id AND m.version=p.material_version
          WHERE p.profile_id=? ORDER BY p.version DESC LIMIT 1""", (profile_id,)).fetchone()
        if profile is None:
            return None
        material_provenance = None
        if profile["material_id"] is not None:
            material_provenance = {
                "material_id": profile["material_id"],
                "material_version": profile["material_version"],
                "name": profile["name"],
                "density_value": profile["density_value"],
                "density_unit": profile["density_unit"],
                "density_source": profile["density_source"],
                "density_source_ref": profile["density_source_ref"],
                "active": bool(profile["material_active"]),
            }
        if expected_source_digest and profile["source_digest"] != expected_source_digest:
            return {"ready": False, "quote_status": "blocked",
                    "execution_status": profile["execution_status"],
                    "source_digest": profile["source_digest"],
                    "material_provenance": material_provenance,
                    "findings": ["The registered profile digest does not match the exact fixed profile files used for this execution."],
                    "profile_id": profile["profile_id"], "profile_version": profile["version"]}
        if profile["execution_status"] != "available":
            return {"ready": False, "quote_status": "blocked",
                    "execution_status": profile["execution_status"],
                    "source_digest": profile["source_digest"],
                    "material_provenance": material_provenance,
                    "findings": ["The registered slicer profile has no available execution validation."],
                    "profile_id": profile["profile_id"], "profile_version": profile["version"]}
        if profile["quote_status"] != "approved":
            return {"ready": False, "quote_status": profile["quote_status"],
                    "execution_status": profile["execution_status"],
                    "source_digest": profile["source_digest"],
                    "material_provenance": material_provenance,
                    "findings": json.loads(profile["validation_findings_json"]),
                    "profile_id": profile["profile_id"], "profile_version": profile["version"]}
        business = connection.execute("SELECT * FROM business_configurations WHERE active=1").fetchone()
        if business is None or profile["material_id"] is None or not profile["material_active"]:
            return {"ready": False, "quote_status": "blocked",
                    "findings": ["The profile must reference an active farm material and an active business configuration before quoting."],
                    "profile_id": profile["profile_id"], "profile_version": profile["version"]}
        if profile["material_currency"] != business["currency"]:
            return {"ready": False, "quote_status": "blocked",
                    "findings": ["Material and business configuration currencies must match before quoting."],
                    "profile_id": profile["profile_id"], "profile_version": profile["version"]}
        return {"ready": True, "quote_status": "approved", "profile_id": profile["profile_id"],
            "execution_status": profile["execution_status"], "profile_version": profile["version"],
            "source_digest": profile["source_digest"], "material": dict(profile),
            "material_provenance": material_provenance,
            "business": dict(business)}
    finally:
        connection.close()


def set_profile_material(db: Path, profile_id: str, material_id: str, material_version: int,
                        evidence_ref: str) -> dict:
    with connect_database(db) as connection:
        profile = connection.execute("SELECT * FROM slicer_profiles WHERE profile_id=? ORDER BY version DESC LIMIT 1", (profile_id,)).fetchone()
        material = connection.execute("SELECT 1 FROM materials WHERE material_id=? AND version=? AND active=1", (material_id,material_version)).fetchone()
        if profile is None or material is None:
            raise ValueError("The profile or active versioned material record was not found.")
        version = profile["version"] + 1
        connection.execute("""INSERT INTO slicer_profiles
          (profile_id,version,display_name,installation_id,printer_id,source,source_digest,source_files_json,
           machine_preset_id,process_preset_id,material_preset_id,nozzle_diameter_mm,execution_status,
           quote_status,validation_findings_json,material_id,material_version,created_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'needs_review',?,?,?,?)""",
          (profile["profile_id"],version,profile["display_name"],profile["installation_id"],profile["printer_id"],
           profile["source"],profile["source_digest"],profile["source_files_json"],profile["machine_preset_id"],
           profile["process_preset_id"],profile["material_preset_id"],profile["nozzle_diameter_mm"],
           profile["execution_status"],json.dumps(["Material association changed; profile quote trust requires revalidation."]),
           material_id,material_version,now()))
        findings = ["Material association changed; profile quote trust requires revalidation."]
        connection.execute("""INSERT INTO slicer_profile_validations
          (validation_id,profile_id,profile_version,status,findings_json,evidence_ref,created_at)
          VALUES (?,?,?,'needs_review',?,?,?)""",
          (str(uuid.uuid4()),profile_id,version,json.dumps(findings),evidence_ref,now()))
        return {"profile_id":profile_id,"version":version,"quote_status":"needs_review"}


def record_profile_execution(db: Path, profile_id: str, status: str, evidence_ref: str,
                             findings: list[str]) -> dict:
    if status not in {"available", "blocked"} or not evidence_ref.strip():
        raise ValueError("Execution status must be available or blocked and include evidence.")
    with connect_database(db) as connection:
        profile = connection.execute("SELECT * FROM slicer_profiles WHERE profile_id=? ORDER BY version DESC LIMIT 1", (profile_id,)).fetchone()
        if profile is None:
            raise ValueError("The profile was not found.")
        connection.execute("UPDATE slicer_profiles SET execution_status=? WHERE profile_id=? AND version=?",
                           (status,profile_id,profile["version"]))
        if status == "available":
            connection.execute("UPDATE slicer_installations SET execution_status='available' WHERE installation_id=?",
                               (profile["installation_id"],))
        check_id = str(uuid.uuid4())
        connection.execute("""INSERT INTO slicer_execution_checks
          (check_id,profile_id,profile_version,status,evidence_ref,findings_json,created_at)
          VALUES (?,?,?,?,?,?,?)""",
          (check_id,profile_id,profile["version"],status,evidence_ref,json.dumps(findings),now()))
        if status == "blocked":
            connection.execute("UPDATE slicer_profiles SET quote_status='blocked' WHERE profile_id=? AND version=?",
                               (profile_id,profile["version"]))
        return {"check_id":check_id,"profile_id":profile_id,"version":profile["version"],"execution_status":status}


def validate_profile(db: Path, profile_id: str, status: str, findings: list[str], evidence_ref: str) -> dict:
    if status not in {"blocked", "needs_review", "approved"}:
        raise ValueError("Profile status must be blocked, needs_review, or approved.")
    if not evidence_ref.strip():
        raise ValueError("Validation evidence reference is required.")
    with connect_database(db) as connection:
        profile = connection.execute("SELECT * FROM slicer_profiles WHERE profile_id=? ORDER BY version DESC LIMIT 1", (profile_id,)).fetchone()
        if profile is None:
            raise ValueError("The profile was not found.")
        if status == "approved" and profile_id == "bambu-a1-0.4":
            evidence_path = Path(evidence_ref)
            if not evidence_path.is_absolute():
                evidence_path = ROOT / evidence_path
            try:
                evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError("Bambu quote approval requires a readable, structured homologation evidence artifact.") from error
            if (profile["execution_status"] != "available"
                    or evidence.get("profile_bundle_sha256") != profile["source_digest"]
                    or evidence.get("gui_parity_status") != "passed"
                    or evidence.get("physical_print_validation", {}).get("status") != "passed"
                    or evidence.get("quote_safe") is not True):
                raise ValueError(
                    "Bambu quote approval requires matching profile-digest evidence with GUI parity and physical print validation passed."
                )
        associated_material = connection.execute(
            "SELECT 1 FROM materials WHERE material_id=? AND version=? AND active=1",
            (profile["material_id"],profile["material_version"]),
        ).fetchone() if profile["material_id"] else None
        if status == "approved" and (profile["execution_status"] != "available" or not associated_material):
            raise ValueError("A profile needs proven execution and an associated material before quote approval.")
        connection.execute("UPDATE slicer_profiles SET quote_status=?,validation_findings_json=? WHERE profile_id=? AND version=?",
            (status,json.dumps(findings),profile_id,profile["version"]))
        validation_id = str(uuid.uuid4())
        connection.execute("""INSERT INTO slicer_profile_validations
          (validation_id,profile_id,profile_version,status,findings_json,evidence_ref,created_at)
          VALUES (?,?,?,?,?,?,?)""",
          (validation_id,profile_id,profile["version"],status,json.dumps(findings),evidence_ref,now()))
        return {"validation_id":validation_id,"profile_id":profile_id,
                "version":profile["version"],"quote_status":status,"findings":findings}


def register_stage3(db: Path) -> dict:
    """Persist proof configuration with quote trust reflecting known evidence."""
    connection = connect_database(db)
    created_profiles = []
    existing_material = connection.execute(
        "SELECT material_id,version FROM materials WHERE material_id='stage3-proof-pla' ORDER BY version DESC LIMIT 1"
    ).fetchone()
    connection.close()
    materials = dict(existing_material) if existing_material else add_material(db,
        name="PLA (Stage 3 proof only)", density=1.24,
        density_source="Stage 3 proof quote input; not farm supplied",
        density_source_ref="docs/stage-3-multi-slicer-proof-report.md", cost_per_kg=90,
        selling_price_per_kg=None, currency="BRL", material_id="stage3-proof-pla")
    connection = connect_database(db)
    # Preserve the original executed Stage 3 Bambu profile as a separate,
    # permanently quote-blocked version before registering the resolved
    # homologation bundle. This also keeps fresh databases from losing history.
    if not connection.execute("SELECT 1 FROM slicer_profiles WHERE profile_id='bambu-a1-0.4' LIMIT 1").fetchone():
        legacy_dir = ROOT / "experiments/slicing/evidence/bambu-stage3-profile"
        legacy_paths = [legacy_dir / name for name in ("machine.json", "process.json", "filament.json")]
        legacy_files = {name: (legacy_dir / name).read_bytes()
                        for name in ("machine.json", "process.json", "filament.json")}
        legacy_digest = digest_files(legacy_files)
        installation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "proof:bambustudio:02.08.02.61"))
        connection.execute("""INSERT OR IGNORE INTO slicer_installations
          (installation_id,slicer_id,version,executable,execution_status,source,created_at)
          VALUES (?,?,?,'proof-container','available','Stage 3 execution evidence',?)""",
          (installation_id,"bambustudio","02.08.02.61",now()))
        findings = ["Historical Stage 3 proof profile retained for provenance; anomalous material volume blocks quoting."]
        connection.execute("""INSERT INTO slicer_profiles
          (profile_id,version,display_name,installation_id,source,source_digest,source_files_json,
           machine_preset_id,process_preset_id,material_preset_id,nozzle_diameter_mm,
           execution_status,quote_status,validation_findings_json,material_id,material_version,created_at)
          VALUES ('bambu-a1-0.4',1,'Bambu Studio Stage 3 proof / A1 0.4 / Standard / PLA Basic',?,
           'Stage 3 proof profile snapshot',?,?,'Bambu Lab A1 0.4 nozzle','0.20mm Standard @BBL A1',
           'Bambu PLA Basic @BBL A1',0.4,'available','blocked',?,?,?,?)""",
          (installation_id,legacy_digest,json.dumps([str(path.relative_to(ROOT)) for path in legacy_paths]),json.dumps(findings),
           materials["material_id"],materials["version"],now()))
        for table, status in (("slicer_profile_validations","blocked"),):
            connection.execute(f"""INSERT INTO {table}
              (validation_id,profile_id,profile_version,status,findings_json,evidence_ref,created_at)
              VALUES (?,?,?,?,?,?,?)""",
              (str(uuid.uuid4()),"bambu-a1-0.4",1,status,json.dumps(findings),
               "docs/stage-3-multi-slicer-proof-report.md",now()))
        connection.execute("""INSERT INTO slicer_execution_checks
          (check_id,profile_id,profile_version,status,evidence_ref,findings_json,created_at)
          VALUES (?,?,1,'available',?,?,?)""",
          (str(uuid.uuid4()),"bambu-a1-0.4","docs/stage-3-multi-slicer-proof-report.md","[]",now()))
    definitions = [
        ("curaengine", "5.13.0", "cura-ultimaker2plus-generic-pla-normal", "CuraEngine / Ultimaker 2+ / Generic PLA / Normal", "proof-profile-map", "Ultimaker 2+", "Normal 0.1 mm", "Generic PLA", 0.4, "available", "needs_review", ["Cura simplified settings map has unresolved filament-volume parity with GUI/full resolved settings."]),
        ("orcaslicer", "2.4.2", "orca-ender3-v3se-0.4-cli", "Orca / Ender-3 V3 SE 0.4 / Standard / Generic PLA", "experiments/slicing/profiles/orca-ender3-v3se-0.4-cli.json", "Creality Ender-3 V3 SE 0.4", "0.20 mm Standard", "Generic PLA @System", 0.4, "available", "needs_review", ["Fixed absolute-E compatibility setting requires farm firmware/profile review.", "Orca reported zero native density and mass; farm material density is required."]),
        ("bambustudio", "02.08.02.61", "bambu-a1-0.4", "Bambu Studio / A1 0.4 / Standard / PLA Basic", "experiments/slicing/profiles/bambu-a1-0.4", "Bambu Lab A1 0.4 nozzle", "0.20mm Standard @BBL A1", "Bambu PLA Basic @BBL A1", 0.4, "available", "blocked", ["Resolved system-preset identities pass CLI compatibility and repeated slicing; GUI parity and the historical material anomaly remain unresolved."]),
        ("crealityprint", "7.2.1.5476", "creality-ender3-v3se-0.4", "Creality Print / Ender-3 V3 SE 0.4 / Standard / CR-PLA", "experiments/slicing/profiles/creality-ender3-v3se-0.4", "Creality Ender-3 V3 SE 0.4", "0.20mm Standard", "CR-PLA", 0.4, "available", "needs_review", ["Prototype execution is evidenced; farm-specific profile and metric validation is pending."]),
    ]
    try:
        for slicer, slicer_version, key, label, source, machine, process, material_name, nozzle, execution, quote, findings in definitions:
            files: dict[str, bytes] = {}
            source_path = ROOT / source
            if slicer == "curaengine":
                sys.path.insert(0, str(ROOT))
                from experiments.slicing.openclaw_production_estimate import PROFILE_MAP
                files["cura-profile-map.json"] = json.dumps(
                    PROFILE_MAP[key], sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            elif source_path.is_file():
                files[source_path.name] = source_path.read_bytes()
            elif source_path.is_dir():
                if slicer == "bambustudio":
                    # Match the profile digest emitted by the Bambu adapter and
                    # its bundle manifest. The manifest is provenance metadata;
                    # the digest covers exactly the three CLI input files.
                    for name in ("machine.json", "process.json", "filament.json"):
                        path = source_path / name
                        files[name] = path.read_bytes()
                else:
                    for path in sorted(source_path.glob("*.json")):
                        files[str(path.relative_to(ROOT))] = path.read_bytes()
            else:
                files["profile-map.json"] = json.dumps({"profile": key, "source": source}, sort_keys=True).encode()
            source_digest = digest_files(files)
            installation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"proof:{slicer}:{slicer_version}"))
            connection.execute("""INSERT OR IGNORE INTO slicer_installations
              (installation_id,slicer_id,version,executable,execution_status,source,created_at)
              VALUES (?,?,?,?,?,?,?)""",
              (installation_id,slicer,slicer_version,"proof-container", "available", "Stage 3 execution evidence", now()))
            latest = connection.execute(
                "SELECT version,source_digest,quote_status,execution_status FROM slicer_profiles WHERE profile_id=? ORDER BY version DESC LIMIT 1", (key,)
            ).fetchone()
            if latest and latest["source_digest"] == source_digest:
                created_profiles.append({"profile_id":key,"version":latest["version"],
                    "execution_status":latest["execution_status"],"quote_status":latest["quote_status"],
                    "source_digest":source_digest,"unchanged":True})
                continue
            profile_version = (latest["version"] + 1) if latest else 1
            connection.execute("""INSERT INTO slicer_profiles
              (profile_id,version,display_name,installation_id,source,source_digest,source_files_json,
               machine_preset_id,process_preset_id,material_preset_id,nozzle_diameter_mm,
               execution_status,quote_status,validation_findings_json,material_id,material_version,created_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (key,profile_version,label,installation_id,source,source_digest,
               json.dumps([str((source_path / name).relative_to(ROOT)) for name in files]
                          if slicer == "bambustudio" else sorted(files)),
               machine,process,material_name,nozzle,execution,quote,json.dumps(findings),materials["material_id"],materials["version"],now()))
            connection.execute("""INSERT INTO slicer_profile_validations
              (validation_id,profile_id,profile_version,status,findings_json,evidence_ref,created_at)
              VALUES (?,?,?,?,?,?,?)""",
              (str(uuid.uuid4()),key,profile_version,quote,json.dumps(findings),
               "experiments/slicing/evidence/bambu-resolved-cli-failure.log" if slicer == "bambustudio"
               else "docs/stage-3-multi-slicer-proof-report.md",now()))
            if execution in {"available", "blocked"}:
                connection.execute("""INSERT INTO slicer_execution_checks
                  (check_id,profile_id,profile_version,status,evidence_ref,findings_json,created_at)
                  VALUES (?,?,?,?,?,?,?)""",
                  (str(uuid.uuid4()),key,profile_version,execution,
                   "experiments/slicing/evidence/bambu-resolved-cli-failure.log" if slicer == "bambustudio"
                   else "docs/stage-3-multi-slicer-proof-report.md",json.dumps(findings),now()))
            created_profiles.append({"profile_id":key,"version":profile_version,"execution_status":execution,"quote_status":quote,"source_digest":source_digest})
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {"material":materials,"profiles":created_profiles}


def inspect(db: Path) -> dict:
    connection = connect_database(db)
    try:
        result = {"schema_version": connection.execute("PRAGMA user_version").fetchone()[0]}
        for table in ("materials", "printers", "slicer_installations", "slicer_profiles",
                      "slicer_profile_validations", "slicer_execution_checks", "printer_profiles",
                      "business_configurations", "farm_users", "user_roles", "quotes", "orders",
                      "print_jobs", "job_events", "slicer_runs"):
            result[table] = [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1, 2")]
        return result
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Apply additive schema migrations.")
    sub.add_parser("register-stage3", help="Register proof profiles with current trust findings.")
    sub.add_parser("show", help="Show persisted farm configuration.")
    material = sub.add_parser("add-material", help="Create a sourced, versioned material record.")
    material.add_argument("--name", required=True)
    material.add_argument("--density-g-cm3", required=True, type=float)
    material.add_argument("--density-source", required=True)
    material.add_argument("--density-source-ref", default="")
    material.add_argument("--cost-per-kg", required=True, type=float)
    material.add_argument("--selling-price-per-kg", type=float)
    material.add_argument("--currency", default="BRL")
    material.add_argument("--material-id")
    printer = sub.add_parser("add-printer", help="Create basic printer metadata; no connectivity is configured.")
    printer.add_argument("--name", required=True)
    printer.add_argument("--manufacturer", default="")
    printer.add_argument("--model", default="")
    printer.add_argument("--build-x-mm", type=float)
    printer.add_argument("--build-y-mm", type=float)
    printer.add_argument("--build-z-mm", type=float)
    printer.add_argument("--nozzle-mm", type=float)
    printer.add_argument("--notes", default="")
    business = sub.add_parser("set-business", help="Save a new active business-cost/margin version.")
    business.add_argument("--currency", default="BRL")
    business.add_argument("--machine-hour-cost", required=True, type=float)
    business.add_argument("--energy-kwh-per-hour", required=True, type=float)
    business.add_argument("--energy-cost-per-kwh", required=True, type=float)
    business.add_argument("--minimum-margin", required=True, type=float)
    business.add_argument("--minimum-job-fee", required=True, type=float)
    business.add_argument("--setup-fee", default=0, type=float)
    business.add_argument("--source", default="Farm owner configuration")
    association = sub.add_parser("set-profile-material", help="Associate a profile with an explicit farm material version.")
    association.add_argument("--profile-id", required=True)
    association.add_argument("--material-id", required=True)
    association.add_argument("--material-version", required=True, type=int)
    association.add_argument("--evidence-ref", required=True)
    validation = sub.add_parser("validate-profile", help="Record a quote-trust decision and evidence.")
    validation.add_argument("--profile-id", required=True)
    validation.add_argument("--status", choices=("blocked","needs_review","approved"), required=True)
    validation.add_argument("--finding", action="append", default=[])
    validation.add_argument("--evidence-ref", required=True)
    execution = sub.add_parser("record-execution", help="Record separate evidence that a profile can or cannot execute.")
    execution.add_argument("--profile-id", required=True)
    execution.add_argument("--status", choices=("available","blocked"), required=True)
    execution.add_argument("--finding", action="append", default=[])
    execution.add_argument("--evidence-ref", required=True)
    profile = sub.add_parser("import-profile", help="Import local preset files as a digest-pinned, unvalidated profile version.")
    profile.add_argument("--profile-id", required=True)
    profile.add_argument("--display-name", required=True)
    profile.add_argument("--slicer-id", required=True)
    profile.add_argument("--slicer-version", required=True)
    profile.add_argument("--source", required=True)
    profile.add_argument("--machine-preset-id", required=True)
    profile.add_argument("--process-preset-id", required=True)
    profile.add_argument("--material-preset-id", required=True)
    profile.add_argument("--profile-file", required=True, action="append", type=Path)
    profile.add_argument("--nozzle-mm", type=float)
    profile.add_argument("--printer-id")
    profile.add_argument("--material-id")
    profile.add_argument("--material-version", type=int)
    args = parser.parse_args()
    if args.command == "init":
        connection = connect_database(args.database)
        result = {"schema_version": connection.execute("PRAGMA user_version").fetchone()[0]}
        connection.close()
    elif args.command == "register-stage3":
        connect_database(args.database).close()
        result = register_stage3(args.database)
    elif args.command == "add-material":
        result = add_material(args.database, name=args.name, density=args.density_g_cm3,
            density_source=args.density_source, density_source_ref=args.density_source_ref,
            cost_per_kg=args.cost_per_kg, selling_price_per_kg=args.selling_price_per_kg,
            currency=args.currency, material_id=args.material_id)
    elif args.command == "add-printer":
        result = add_printer(args.database, name=args.name, manufacturer=args.manufacturer,
            model=args.model, build=(args.build_x_mm,args.build_y_mm,args.build_z_mm),
            nozzle=args.nozzle_mm, notes=args.notes)
    elif args.command == "set-business":
        result = add_business_configuration(args.database, currency=args.currency,
            machine_hour_cost=args.machine_hour_cost, energy_kwh_per_hour=args.energy_kwh_per_hour,
            energy_cost_per_kwh=args.energy_cost_per_kwh,
            minimum_margin_fraction=args.minimum_margin, minimum_job_fee=args.minimum_job_fee,
            setup_fee=args.setup_fee, source=args.source)
    elif args.command == "set-profile-material":
        result = set_profile_material(args.database,args.profile_id,args.material_id,
            args.material_version,args.evidence_ref)
    elif args.command == "validate-profile":
        result = validate_profile(args.database,args.profile_id,args.status,args.finding,args.evidence_ref)
    elif args.command == "record-execution":
        result = record_profile_execution(args.database,args.profile_id,args.status,args.evidence_ref,args.finding)
    elif args.command == "import-profile":
        result = import_profile(args.database, profile_id=args.profile_id, display_name=args.display_name,
            slicer_id=args.slicer_id, slicer_version=args.slicer_version, source=args.source,
            machine_preset_id=args.machine_preset_id, process_preset_id=args.process_preset_id,
            material_preset_id=args.material_preset_id, source_files=args.profile_file,
            nozzle=args.nozzle_mm, printer_id=args.printer_id, material_id=args.material_id,
            material_version=args.material_version)
    else:
        result = inspect(args.database)
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        raise SystemExit(1)
