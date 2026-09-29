#!/usr/bin/env python3
"""Fixed-path bridge from Plow's OpenClaw exec tool to farm domain functions.

The OpenClaw/Plow transport is outside the farm domain. The only principal this
variant provisions automatically is its authenticated Plow owner; domain
capabilities and workflow checks remain in the existing Python modules.
"""

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if not (ROOT / "experiments").is_dir():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from experiments.openclaw_local_tools import dispatch

DATABASE = Path(os.environ.get(
    "PRINT_FARM_DATABASE_PATH", "/var/lib/plow/print-farm/farm.sqlite"
))
JOBS = Path(os.environ.get(
    "PRINT_FARM_JOBS_PATH", "/var/lib/plow/print-farm/jobs"
))
MAX_STL_BYTES = 25 * 1024 * 1024
FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}[.]stl", re.IGNORECASE)


def analyze_stl(filename: str) -> dict:
    if not isinstance(filename, str) or not FILENAME.fullmatch(filename):
        raise ValueError("Pass an STL filename only; paths are not accepted.")
    JOBS.mkdir(parents=True, exist_ok=True)
    candidate = JOBS / filename
    if candidate.is_symlink():
        raise ValueError("Symbolic links are not accepted as STL inputs.")
    descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0 or metadata.st_size > MAX_STL_BYTES:
            raise ValueError("The STL must be a nonempty regular file smaller than 25 MiB.")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            data = source.read(MAX_STL_BYTES + 1)
        if len(data) != metadata.st_size:
            raise ValueError("The STL changed while it was being read.")
    finally:
        os.close(descriptor)

    analyzer = ROOT / "experiments" / "stl-analysis" / "analyze_stl.py"
    persistence = ROOT / "experiments" / "stl-analysis" / "persistence.py"
    with tempfile.TemporaryDirectory(prefix="print-farm-stl-", dir=JOBS) as tmp:
        source = Path(tmp) / filename
        source.write_bytes(data)
        result = subprocess.run(
            [sys.executable, str(analyzer), str(source)],
            check=True, capture_output=True, text=True, timeout=10,
        )
        analysis = json.loads(result.stdout)
        analysis.pop("file", None)
        result_file = Path(tmp) / "analysis.json"
        result_file.write_text(json.dumps(analysis), encoding="utf-8")
        stored = subprocess.run(
            [sys.executable, str(persistence), "store", "--database", str(DATABASE),
             "--filename", filename, "--result-file", str(result_file)],
            check=True, capture_output=True, text=True, timeout=10,
        )
        return {"success": True, **json.loads(stored.stdout), "analysis": analysis}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read(64 * 1024 + 1))
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object.")
        operation = payload.get("operation")
        if operation == "analyze_stl":
            result = analyze_stl(payload.get("filename"))
        else:
            # Ignore any model-supplied database path. All state stays on the
            # persistent Plow volume for this installation.
            payload["database"] = str(DATABASE)
            result = dispatch(payload)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({"success": False, "error": str(error)[:300]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
