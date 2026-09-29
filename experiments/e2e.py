#!/usr/bin/env python3
"""Demonstration pipeline: STL inspection -> Cura -> quote -> mock printer choice."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "experiments/stl-analysis"))
sys.path.insert(0, str(ROOT / "experiments/slicing"))
sys.path.insert(0, str(ROOT / "experiments/quote-engine"))
sys.path.insert(0, str(ROOT / "experiments/farm-manager"))
from analyze_stl import inspect
from run_cura import run_one
from quote import quote
from farm import recommend


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stl", type=Path)
    parser.add_argument("--quantity", type=int, default=1)
    args = parser.parse_args()
    path = args.stl.resolve()
    if not path.exists():
        parser.error(f"file does not exist: {path}")
    if args.quantity < 1:
        parser.error("quantity must be positive")
    mesh = inspect(path)
    slicing = run_one(path)
    if slicing["exit_code"] != 0 or slicing["filament_volume_mm3"] is None:
        raise SystemExit("Cura slice/metrics unavailable; refusing to create a quote")
    weight = slicing["filament_volume_mm3"] / 1000 * 1.24
    hours = slicing["slicer_estimate_seconds"] / 3600
    pricing = quote(weight, hours, quantity=args.quantity)
    # Simplest scheduling assumption: print copies sequentially; a real slicer
    # may place multiple copies in one build and therefore use less than N*x time.
    allocation = recommend({"id":path.stem,"dimensions_mm":list(mesh["dimensions_mm"].values()),
                            "material":"PLA","hours":hours * args.quantity})
    output = {"classification":"POC estimates; printer manager is mock",
              "file":str(path),"quantity":args.quantity,"mesh_analysis":mesh,
              "slicing":slicing,"estimated_quote":pricing,
              "printer_recommendation":allocation}
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
