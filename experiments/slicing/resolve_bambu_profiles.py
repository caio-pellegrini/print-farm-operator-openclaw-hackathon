#!/usr/bin/env python3
"""Resolve complete A1 presets while retaining Bambu system-preset identity.

The source preset tree is expected to come from the pinned Bambu Studio
02.08.02.61 Ubuntu 24.04 AppImage. Values are merged parent to child for the
CLI's full-config input contract. Bambu's own ``from``, ``setting_id``,
``name``, and ``inherits`` metadata is retained because its compatibility
checks use preset identity and ancestry even after values have been resolved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


VERSION = "02.08.02.61"
APPIMAGE_SHA256 = "d501b103fac5424513ec0e8d6bc145fb30719de2c7d94d7320d723740c81a7fd"
PRESETS = {
    "machine": ("machine", "Bambu Lab A1 0.4 nozzle"),
    "process": ("process", "0.20mm Standard @BBL A1"),
    "filament": ("filament", "Bambu PLA Basic @BBL A1"),
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_files(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def find_preset(root: Path, kind: str, name: str) -> Path:
    matches: list[Path] = []
    for path in root.rglob("*.json"):
        if kind not in path.parts:
            continue
        try:
            preset = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if preset.get("name") == name and preset.get("type") == kind:
            matches.append(path)
    if len(matches) != 1:
        raise ValueError(f"Expected one {kind} preset named {name!r}, found {len(matches)}")
    return matches[0]


def resolve(root: Path, kind: str, name: str, stack: tuple[str, ...] = ()) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if name in stack:
        raise ValueError("Preset inheritance cycle: " + " -> ".join((*stack, name)))
    path = find_preset(root, kind, name)
    leaf = json.loads(path.read_text(encoding="utf-8"))
    parent = leaf.get("inherits")
    if parent:
        merged, ancestry = resolve(root, kind, parent, (*stack, name))
    else:
        merged, ancestry = {}, []
    merged.update(leaf)
    source_hash = sha256(path.read_bytes())
    ancestry.append({
        "preset_id": name,
        "source_path": path.relative_to(root).as_posix(),
        "source_sha256": source_hash,
    })
    return merged, ancestry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles-root", required=True, type=Path,
                        help="Path to resources/profiles from the pinned Bambu Studio AppImage")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.profiles_root.is_dir():
        parser.error("--profiles-root must be a directory")

    args.output.mkdir(parents=True, exist_ok=True)
    resolved: dict[str, bytes] = {}
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "slicer": "Bambu Studio",
        "slicer_version": VERSION,
        "appimage_sha256": APPIMAGE_SHA256,
        "target": {
            "printer_model": "Bambu Lab A1",
            "nozzle_diameter_mm": 0.4,
            "process": "0.20mm Standard @BBL A1",
            "filament": "Bambu PLA Basic @BBL A1",
            "material_family": "PLA",
            "filament_density_g_cm3": 1.26,
        },
        "cli_overlays": {},
        "resolution_strategy": (
            "Bambu Studio bundled system presets; values merged parent-to-child; "
            "system identity and source inheritance metadata retained for CLI compatibility"
        ),
        "profiles": {},
    }
    for output_name, (kind, preset_id) in PRESETS.items():
        data, ancestry = resolve(args.profiles_root, kind, preset_id)
        source_compatible_printers = list(data.get("compatible_printers", []))
        source_from = data.get("from")
        source_setting_id = data.get("setting_id")
        source_inherits = data.get("inherits")
        if source_from != "system" or not source_setting_id or not source_inherits:
            raise ValueError(f"Source {kind} preset lacks Bambu system identity/ancestry metadata")
        # The CLI needs complete effective values but compatibility checks also
        # inspect system preset identity and ancestry. Keep those exact fields.
        data["from"] = source_from
        overlay: dict[str, Any] = {
            "from": source_from,
            "setting_id": source_setting_id,
            "inherits": source_inherits,
        }
        # Bambu's CLI needs this machine key although the bundled machine preset
        # does not include it in every release.
        if kind == "machine":
            data.setdefault("nozzle_volume_type", ["standard"])
            if not data.get("printer_settings_id"):
                data["printer_settings_id"] = preset_id
                overlay["printer_settings_id"] = preset_id
        elif kind == "process" and not data.get("print_settings_id"):
            data["print_settings_id"] = preset_id
            overlay["print_settings_id"] = preset_id
        elif kind == "filament" and data.get("filament_settings_id") in (None, "", [""]):
            data["filament_settings_id"] = preset_id
            overlay["filament_settings_id"] = preset_id
        if kind in {"process", "filament"}:
            if "Bambu Lab A1 0.4 nozzle" not in source_compatible_printers:
                raise ValueError(f"Source {kind} preset is not compatible with the A1 0.4 machine")
        manifest["cli_overlays"][output_name] = overlay
        raw = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")
        (args.output / f"{output_name}.json").write_bytes(raw)
        resolved[f"{output_name}.json"] = raw
        manifest["profiles"][output_name] = {
            "preset_id": preset_id,
            "type": kind,
            "source_from": source_from,
            "source_setting_id": source_setting_id,
            "source_inherits": source_inherits,
            "source_compatible_printers": source_compatible_printers,
            "ancestry_parent_to_child": ancestry,
            "resolved_sha256": sha256(raw),
        }

    machine = json.loads(resolved["machine.json"])
    process = json.loads(resolved["process.json"])
    filament = json.loads(resolved["filament.json"])
    if machine.get("printer_model") != "Bambu Lab A1" or machine.get("nozzle_diameter") != ["0.4"]:
        raise ValueError("Resolved machine does not match the requested A1 0.4 mm target")
    if "Bambu Lab A1 0.4 nozzle" not in manifest["profiles"]["process"]["source_compatible_printers"]:
        raise ValueError("Resolved process does not target the A1 0.4 mm machine")
    if "Bambu Lab A1 0.4 nozzle" not in manifest["profiles"]["filament"]["source_compatible_printers"]:
        raise ValueError("Resolved filament does not target the A1 0.4 mm machine")
    if filament.get("filament_type") != ["PLA"] or filament.get("filament_density") != ["1.26"]:
        raise ValueError("Resolved filament identity/density differs from the pinned target")
    if any(not json.loads(raw).get("inherits") for raw in resolved.values()):
        raise ValueError("Resolved CLI profiles must retain Bambu compatibility ancestry metadata")
    manifest["bundle_sha256"] = digest_files(resolved)
    manifest["files"] = {
        name: {"sha256": sha256(raw), "size_bytes": len(raw)}
        for name, raw in sorted(resolved.items())
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"bundle_sha256": manifest["bundle_sha256"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
