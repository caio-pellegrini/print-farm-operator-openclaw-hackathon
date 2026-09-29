#!/usr/bin/env python3
"""Store and retrieve small, explicit records for OpenClaw STL analyses."""

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from farm_domain import connect_database


def connect(database: Path) -> sqlite3.Connection:
    return connect_database(database)


def store(database: Path, filename: str, result_file: Path) -> dict:
    result = json.loads(result_file.read_text(encoding="utf-8"))
    analysis_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    dimensions = result.get("dimensions_mm")
    if not isinstance(dimensions, dict):
        raise ValueError("Analyzer result does not contain dimensions_mm")

    with connect(database) as connection:
        connection.execute(
            """INSERT INTO stl_analyses
               (analysis_id, filename, dimensions_json, analysis_json, created_at, status)
               VALUES (?, ?, ?, ?, ?, 'completed')""",
            (analysis_id, filename, json.dumps(dimensions), json.dumps(result), created_at),
        )

    return {
        "analysis_id": analysis_id,
        "filename": filename,
        "dimensions_mm": dimensions,
        "created_at": created_at,
        "status": "completed",
        "analysis": result,
    }


def latest(database: Path) -> dict:
    with connect(database) as connection:
        row = connection.execute(
            """SELECT analysis_id, filename, dimensions_json, analysis_json, created_at, status
               FROM stl_analyses ORDER BY created_at DESC, rowid DESC LIMIT 1"""
        ).fetchone()
    if row is None:
        return {"found": False, "message": "No STL analysis has been stored yet."}

    return {
        "found": True,
        "analysis_id": row[0],
        "filename": row[1],
        "dimensions_mm": json.loads(row[2]),
        "analysis": json.loads(row[3]),
        "created_at": row[4],
        "status": row[5],
    }


def get_analysis(database: Path, analysis_id: str, filename: str) -> dict:
    with connect(database) as connection:
        row = connection.execute(
            """SELECT analysis_id, filename, dimensions_json, analysis_json, created_at, status
               FROM stl_analyses WHERE analysis_id = ? AND filename = ?""",
            (analysis_id, filename),
        ).fetchone()
    if row is None:
        return {"found": False, "message": "No saved STL analysis matches this ID and filename."}
    return {
        "found": True,
        "analysis_id": row[0],
        "filename": row[1],
        "dimensions_mm": json.loads(row[2]),
        "analysis": json.loads(row[3]),
        "created_at": row[4],
        "status": row[5],
    }


def store_estimate(database: Path, record: dict) -> dict:
    required = {
        "analysis_id", "filename", "profile", "slicing", "estimated_material_g",
        "estimated_print_time_seconds", "quote", "business_config", "request_summary",
    }
    if required - record.keys():
        raise ValueError("Production estimate is missing required fields")
    estimate_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    with connect(database) as connection:
        analysis = connection.execute(
            "SELECT filename FROM stl_analyses WHERE analysis_id = ?",
            (record["analysis_id"],),
        ).fetchone()
        if analysis is None or analysis[0] != record["filename"]:
            raise ValueError("The linked STL analysis does not exist for this filename")
        connection.execute(
            """INSERT INTO production_estimates
               (estimate_id, analysis_id, filename, profile, slicing_json,
                estimated_material_g, estimated_print_time_seconds, quote_json,
                business_config_json, request_summary, created_at, status,
                quote_readiness_status, profile_version, material_id, material_version,
                business_config_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?)""",
            (
                estimate_id,
                record["analysis_id"],
                record["filename"],
                record["profile"],
                json.dumps(record["slicing"]),
                float(record["estimated_material_g"]),
                int(record["estimated_print_time_seconds"]),
                json.dumps(record["quote"]),
                json.dumps(record["business_config"]),
                record["request_summary"],
                created_at,
                record.get("quote_readiness_status", "legacy_unverified"),
                record.get("profile_version"),
                record.get("material_id"),
                record.get("material_version"),
                record.get("business_config_version"),
            ),
        )
    return {"estimate_id": estimate_id, "created_at": created_at, "status": "completed"}


def latest_estimate(database: Path) -> dict:
    with connect(database) as connection:
        row = connection.execute(
            """SELECT estimate_id, analysis_id, filename, profile, slicing_json,
                      estimated_material_g, estimated_print_time_seconds,
                      quote_json, business_config_json, request_summary, created_at, status,
                      quote_readiness_status, profile_version, material_id, material_version,
                      business_config_version
               FROM production_estimates ORDER BY created_at DESC, rowid DESC LIMIT 1"""
        ).fetchone()
    if row is None:
        return {"found": False, "message": "No production estimate has been stored yet."}
    return {
        "found": True,
        "estimate_id": row[0],
        "analysis_id": row[1],
        "filename": row[2],
        "profile": row[3],
        "slicing": json.loads(row[4]),
        "estimated_material_g": row[5],
        "estimated_print_time_seconds": row[6],
        "quote": json.loads(row[7]),
        "business_config": json.loads(row[8]),
        "request_summary": row[9],
        "created_at": row[10],
        "status": row[11],
        "quote_readiness_status": row[12],
        "profile_version": row[13],
        "material_id": row[14],
        "material_version": row[15],
        "business_config_version": row[16],
    }


def store_slicer_run(database: Path, record: dict) -> dict:
    required = {"analysis_id", "filename", "profile", "slicing", "quote_readiness_status"}
    if required - record.keys():
        raise ValueError("Slicer run is missing required fields")
    run_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    with connect(database) as connection:
        analysis = connection.execute("SELECT filename FROM stl_analyses WHERE analysis_id=?", (record["analysis_id"],)).fetchone()
        if analysis is None or analysis[0] != record["filename"]:
            raise ValueError("The linked STL analysis does not exist for this filename")
        connection.execute("""INSERT INTO slicer_runs
          (run_id,analysis_id,filename,profile_id,profile_version,slicer_result_json,
           execution_status,quote_readiness_status,created_at)
          VALUES (?,?,?,?,?,?,'completed',?,?)""",
          (run_id,record["analysis_id"],record["filename"],record["profile"],
           record.get("profile_version"),json.dumps(record["slicing"]),
           record["quote_readiness_status"],created_at))
    return {"run_id":run_id,"created_at":created_at,"execution_status":"completed"}


def latest_slicer_run(database: Path) -> dict:
    with connect(database) as connection:
        row = connection.execute("""SELECT run_id,analysis_id,filename,profile_id,profile_version,
          slicer_result_json,execution_status,quote_readiness_status,created_at
          FROM slicer_runs ORDER BY created_at DESC,rowid DESC LIMIT 1""").fetchone()
    if row is None:
        return {"found":False,"message":"No slicer run has been stored yet."}
    return {"found":True,"run_id":row[0],"analysis_id":row[1],"filename":row[2],
        "profile":row[3],"profile_version":row[4],"slicing":json.loads(row[5]),
        "execution_status":row[6],"quote_readiness_status":row[7],"created_at":row[8]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("store", "latest", "get", "store-estimate", "latest-estimate", "store-slicer-run", "latest-slicer-run"))
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--filename")
    parser.add_argument("--result-file", type=Path)
    parser.add_argument("--estimate-file", type=Path)
    parser.add_argument("--run-file", type=Path)
    parser.add_argument("--analysis-id")
    args = parser.parse_args()

    if args.action == "store":
        if not args.filename or not args.result_file:
            parser.error("store requires --filename and --result-file")
        result = store(args.database, args.filename, args.result_file)
    elif args.action == "latest":
        result = latest(args.database)
    elif args.action == "get":
        if not args.analysis_id or not args.filename:
            parser.error("get requires --analysis-id and --filename")
        result = get_analysis(args.database, args.analysis_id, args.filename)
    elif args.action == "store-estimate":
        if not args.estimate_file:
            parser.error("store-estimate requires --estimate-file")
        result = store_estimate(args.database, json.loads(args.estimate_file.read_text(encoding="utf-8")))
    elif args.action == "store-slicer-run":
        if not args.run_file:
            parser.error("store-slicer-run requires --run-file")
        result = store_slicer_run(args.database,json.loads(args.run_file.read_text(encoding="utf-8")))
    elif args.action == "latest-slicer-run":
        result = latest_slicer_run(args.database)
    else:
        result = latest_estimate(args.database)
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        raise SystemExit(1)
