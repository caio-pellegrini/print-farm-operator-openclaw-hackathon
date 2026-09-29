#!/usr/bin/env python3
"""Run the checked-in CuraEngine container on deterministic STL samples."""
import json
import math
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "experiments/stl-analysis/samples"
OUT = ROOT / "experiments/slicing"
IMAGE = "print-farm-cura-poc"
PROFILE = ROOT / "experiments/slicing/pla_profile.json"
PRINTER_DEF = "/opt/cura/definitions/creality_ender3.def.json"


def run_one(stl):
    stl = stl.resolve()
    stl.relative_to(ROOT)
    gcode = OUT / ("cura-" + stl.stem + ".gcode")
    command = ["docker", "run", "--rm", "-v", f"{ROOT}:/work", "--entrypoint", "CuraEngine", IMAGE,
               "slice", "-v", "-j", PRINTER_DEF, "-j", f"/work/{PROFILE.relative_to(ROOT)}",
               "-e0", "-s", "material_diameter=1.75", "-l", f"/work/{stl.relative_to(ROOT)}",
               "-o", f"/work/{gcode.relative_to(ROOT)}"]
    start = time.perf_counter()
    proc = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    elapsed = time.perf_counter() - start
    log = proc.stdout
    seconds = re.search(r"Gcode header after slicing:\s*.*?;TIME:(\d+)", log, re.S)
    filament = re.search(r"Gcode header after slicing:\s*.*?;Filament used:\s*([\d.]+)m", log, re.S)
    volume = re.search(r"Filament \(mm\^3\):\s*([\d.]+)", log)
    warning_lines = [line[:240] for line in log.splitlines() if "[WARNING]" in line]
    radius_cm = 1.75 / 20
    length_m = float(filament.group(1)) if filament else None
    weight_g = (length_m * 100 * math.pi * radius_cm**2 * 1.24) if length_m is not None else None
    return {"sample": str(stl.relative_to(ROOT)), "printer_definition": "Creality Ender-3 (generic 2018 preset; not V3 SE)",
            "material": "PLA; 1.75 mm explicitly set on extruder 0", "exit_code": proc.returncode,
            "wall_seconds": round(elapsed, 3), "slicer_estimate_seconds": int(seconds.group(1)) if seconds else None,
            "filament_length_m": length_m, "estimated_weight_g": round(weight_g, 2) if weight_g is not None else None,
            "filament_volume_mm3": float(volume.group(1)) if volume else None,
            "gcode_size_bytes": gcode.stat().st_size if gcode.exists() else 0,
            "warning_count": len(warning_lines), "warnings_sample": warning_lines[:5],
            "gcode_file_header_is_reliable": False,
            "log_tail": "\n".join(log.splitlines()[-7:])}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results = [run_one(path) for path in sorted(SAMPLES.glob("*.stl"))]
    result = {"engine": "CuraEngine 5.0.0 in Debian trixie; local Docker image", "results": results}
    (OUT / "cura-results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
