#!/usr/bin/env python3
"""Small local OpenClaw adapter for persistent setup and manual production tools."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.onboarding import advance_onboarding, get_farm_configuration, local_staff_principal
from experiments.farm_domain import connect_database
from experiments.production_workflow import (
    assign_printer, finish_job, inspect_production_job, list_ready_jobs, start_job,
)


def dispatch(payload: dict) -> dict:
    db = Path(payload["database"])
    operation = payload.get("operation")
    if operation == "farm_onboarding":
        return advance_onboarding(db, payload.get("answer"))
    if operation == "get_farm_configuration":
        return get_farm_configuration(db)
    # Trust for this local-only adapter is granted by the plugin factory after
    # OpenClaw marks the active WebChat sender as an authenticated owner.
    principal = local_staff_principal(db)
    if operation == "list_ready_jobs":
        with connect_database(db) as connection:
            printers = [dict(row) for row in connection.execute(
                "SELECT printer_id,name,model,nozzle_diameter_mm,adapter_id FROM printers WHERE active=1 ORDER BY name")]
        return {"jobs": list_ready_jobs(db, principal), "printers": printers}
    if operation == "inspect_production_job":
        return inspect_production_job(db, principal, payload["job_id"])
    if operation == "assign_printer":
        return assign_printer(db, principal, payload["job_id"], payload["printer_id"])
    if operation == "start_job":
        return start_job(db, principal, payload["job_id"])
    if operation == "finish_job":
        return finish_job(db, principal, payload["job_id"], payload["outcome"], payload.get("details", ""))
    raise ValueError("Unsupported local farm operation.")


if __name__ == "__main__":
    try:
        data = json.loads(sys.stdin.buffer.read(64 * 1024 + 1))
        print(json.dumps(dispatch(data), sort_keys=True))
    except Exception as error:
        print(json.dumps({"error": str(error)[:300]}))
        raise SystemExit(1)
