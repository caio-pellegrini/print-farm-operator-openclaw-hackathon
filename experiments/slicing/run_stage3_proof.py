#!/usr/bin/env python3
"""Slice one staged fixture through each executed Stage 3 adapter."""
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments/slicing"))
sys.path.insert(0, str(ROOT / "experiments/quote-engine"))

from quote import quote_slicer_result
from slicer_adapters import (
    CURA_PROFILE_ID,
    ORCA_PROFILE_ID,
    BAMBU_PROFILE_ID,
    CREALITY_PROFILE_ID,
    slice_with_adapter,
)


BUSINESS_CONFIG = {
    "material_brl_kg": 90.0,
    "material_density_g_cm3": 1.24,
    "machine_brl_h": 2.0,
    "energy_kwh": 0.12,
    "energy_brl_kwh": 0.95,
    "margin": 0.40,
    "quantity": 1,
    "setup_brl": 0.0,
}


def main():
    source = ROOT / "experiments/stl-analysis/samples/small-box-20mm.stl"
    if not source.is_file():
        raise SystemExit("Generate the checked-in 20 mm box STL fixture first.")
    with tempfile.TemporaryDirectory(prefix="stage3-staged-") as directory:
        staged = Path(directory) / "input.stl"
        shutil.copyfile(source, staged)
        os.chmod(staged, 0o600)
        fixture_hash = hashlib.sha256(staged.read_bytes()).hexdigest()
        results = {}
        for slicer_id, profile_id in (
            ("curaengine", CURA_PROFILE_ID),
            ("orcaslicer", ORCA_PROFILE_ID),
            ("bambustudio", BAMBU_PROFILE_ID),
            ("crealityprint", CREALITY_PROFILE_ID),
        ):
            normalized = slice_with_adapter(slicer_id, staged, profile_id)
            normalized["input_sha256"] = fixture_hash
            normalized["quote"] = quote_slicer_result(normalized, **BUSINESS_CONFIG)
            results[slicer_id] = normalized

    evidence = {
        "fixture": "experiments/stl-analysis/samples/small-box-20mm.stl",
        "fixture_sha256": fixture_hash,
        "quote_engine": "experiments/quote-engine/quote.py:quote_slicer_result",
        "business_config": BUSINESS_CONFIG,
        "results": results,
    }
    output = ROOT / "experiments/slicing/stage3-proof-results.json"
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
