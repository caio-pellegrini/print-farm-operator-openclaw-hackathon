#!/usr/bin/env python3
"""Repeat the fixed Bambu A1 baseline slice and record its trust findings."""
from __future__ import annotations

import hashlib
import json
import shutil
import statistics
import tempfile
from pathlib import Path

from slicer_adapters import BAMBU_PROFILE_ID, ROOT, slice_with_adapter


EXPECTED_FIXTURE_SHA256 = "4ca3d19d01e11ee608d81218670db3547d320cbc7bef4e36b64339cf4285ad70"
EXPECTED_BOUNDS_MM = {"x": 20.0, "y": 20.0, "z": 12.0}
MODELED_SOLID_VOLUME_MM3 = 4800.0


def main() -> int:
    fixture = ROOT / "experiments/stl-analysis/samples/small-box-20mm.stl"
    fixture_hash = hashlib.sha256(fixture.read_bytes()).hexdigest()
    if fixture_hash != EXPECTED_FIXTURE_SHA256:
        raise SystemExit(f"Fixture SHA-256 changed: {fixture_hash}")

    results = []
    runtime_failures = []
    for _ in range(3):
        with tempfile.TemporaryDirectory(prefix="bambu-homologation-") as directory:
            staged = Path(directory) / "input.stl"
            shutil.copyfile(fixture, staged)
            try:
                results.append(slice_with_adapter("bambustudio", staged, BAMBU_PROFILE_ID))
            except Exception as error:
                runtime_failures.append(str(error))
                break

    volumes = [float(item["material_consumption"]["volume_mm3"]) for item in results]
    times = [float(item["print_time_seconds"]) for item in results]
    volume_range = (max(volumes) - min(volumes)) / statistics.mean(volumes) if volumes else None
    time_range = (max(times) - min(times)) / statistics.mean(times) if times else None
    summary = results[0].get("slice_summary", {}) if results else {}
    findings = ["Bambu Studio CLI rejected the resolved profile bundle."] if runtime_failures else []
    if results:
        if summary.get("object_count") != 1 or summary.get("triangle_count") != 12:
            findings.append("Fixture slice did not report exactly one 12-triangle object.")
        if summary.get("filament_count") != 1 or summary.get("filament_changes") != 0:
            findings.append("Fixture slice reported unexpected filament or filament-change activity.")
        if summary.get("object_dimensions_mm") != EXPECTED_BOUNDS_MM:
            findings.append("Fixture slice bounding dimensions do not match 20 x 20 x 12 mm.")
        if summary.get("layer_count") != 60:
            findings.append("Fixture slice did not report the expected 60 layers.")
    if volume_range is not None and volume_range > 0.005:
        findings.append("Repeated material-volume variation exceeds 0.5%.")
    if time_range is not None and time_range > 0.01:
        findings.append("Repeated print-time variation exceeds 1%.")
    material_ratio = statistics.mean(volumes) / MODELED_SOLID_VOLUME_MM3 if volumes else None
    if material_ratio is not None and material_ratio > 2:
        findings.append(
            f"Material volume is {material_ratio:.2f}x the fixture modeled solid volume; investigate extrusion/toolpath before quote approval."
        )
    findings.append("GUI-to-CLI parity has not been established; business quoting remains blocked.")

    evidence = {
        "fixture": "experiments/stl-analysis/samples/small-box-20mm.stl",
        "fixture_sha256": fixture_hash,
        "target": "tester-aligned machine: Bambu Lab A1 / 0.4 mm; single-filament PLA material/process remain investigation choices",
        "run_count": len(results),
        "repeated_volume_range_fraction": round(volume_range, 6) if volume_range is not None else None,
        "repeated_time_range_fraction": round(time_range, 6) if time_range is not None else None,
        "mean_volume_mm3": round(statistics.mean(volumes), 2) if volumes else None,
        "mean_time_seconds": round(statistics.mean(times), 2) if times else None,
        "material_to_modeled_solid_volume_ratio": round(material_ratio, 4) if material_ratio is not None else None,
        "profile_source_digest": json.loads((ROOT / "experiments/slicing/profiles/bambu-a1-0.4/manifest.json").read_text())["bundle_sha256"],
        "gui_parity_status": "pending",
        "profile": results[0]["profile"] if results else None,
        "runs": results,
        "runtime_failures": runtime_failures,
        "execution_status": "available" if not runtime_failures and not any("Repeated" in item for item in findings) else "blocked",
        "quote_status": "blocked",
        "quote_readiness_state": "BLOCKED",
        "findings": findings,
        "limitations": [
            "CLI repeatability is not GUI parity or physical print validation.",
            "A successful slice does not approve a business quote.",
        ],
    }
    output = ROOT / "experiments/slicing/evidence/bambu-homologation-results.json"
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: evidence[key] for key in (
        "fixture_sha256", "run_count", "repeated_volume_range_fraction",
        "repeated_time_range_fraction", "mean_volume_mm3", "mean_time_seconds",
        "profile_source_digest", "execution_status", "quote_status", "findings")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
