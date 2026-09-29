#!/usr/bin/env python3
"""Small, allowlisted slicer adapters for the Stage 3 proof.

Callers must stage the STL first. This module accepts no slicer arguments or
settings from callers; each profile ID maps to fixed image, resource, and
settings choices below.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Protocol


ROOT = Path(__file__).resolve().parents[2]
CURA_IMAGE = "print-farm-cura-5.13.0:baseline"
ORCA_IMAGE = "print-farm-orca-2.4.2:baseline"
BAMBU_IMAGE = "print-farm-bambu-studio-2.8.2.61:baseline"
CREALITY_IMAGE = "print-farm-creality-print-7.2.1:baseline"
MAX_OUTPUT_BYTES = 1_048_576
TIMEOUT_SECONDS = 30

CURA_PROFILE_ID = "cura-ultimaker2plus-generic-pla-normal"
ORCA_PROFILE_ID = "orca-ender3-v3se-04-generic-pla-standard"
ORCA_MACHINE_PROFILE = ROOT / "experiments/slicing/profiles/orca-ender3-v3se-0.4-cli.json"
BAMBU_PROFILE_DIR = ROOT / "experiments/slicing/profiles/bambu-a1-0.4"
BAMBU_PROFILE_ID = "bambu-a1-04-bambu-pla-basic-020-standard"
CREALITY_PROFILE_ID = "creality-ender3-v3se-04-cr-pla-020-standard"


def _digest_profile_files(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


class SlicerAdapter(Protocol):
    slicer_id: str

    def validate_profile(self, profile_id: str) -> dict: ...

    def slice(self, staged_stl: Path, profile_id: str) -> dict: ...

    def inspect_result(self, raw_output: str, artifact_path: Path | None = None) -> dict: ...


def _validate_staged_stl(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if not resolved.is_file() or info.st_size <= 0 or info.st_size > 25 * 1024 * 1024:
        raise ValueError("The staged STL must be a nonempty regular file within 25 MiB.")
    return resolved


def _run(command: list[str], timeout: int = TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    raw = result.stdout + result.stderr
    if len(raw.encode("utf-8", errors="replace")) > MAX_OUTPUT_BYTES:
        raise ValueError("Slicer output exceeded the 1 MiB limit.")
    return result


def _diagnostics(result: subprocess.CompletedProcess[str]) -> tuple[list[str], list[str], str]:
    raw = result.stdout + result.stderr
    warnings = [line.strip()[:240] for line in raw.splitlines()
                if "[warning]" in line.lower() or "warning:" in line.lower()]
    errors = [line.strip()[:240] for line in raw.splitlines()
              if "[error]" in line.lower() or "error:" in line.lower()]
    return warnings, errors, raw


class CuraEngineAdapter:
    slicer_id = "curaengine"
    profile = {
        "id": CURA_PROFILE_ID,
        "printer": "Ultimaker 2+ (official Cura 5.13.0 machine/extruder definitions)",
        "process": "Normal 0.1 mm simplified project CLI map",
        "material": "Generic PLA; 2.85 mm; density 1.24 g/cm3",
        "definition": "/opt/cura/resources/definitions/ultimaker2_plus.def.json",
        "extruder_definition": "/opt/cura/resources/extruders/ultimaker2_plus_extruder_0.def.json",
        "settings": {
            "layer_height": "0.1", "infill_sparse_density": "20",
            "top_bottom_thickness": "0.8", "cool_min_layer_time": "5",
            "cool_min_speed": "10", "speed_print": "50", "speed_layer_0": "30",
            "speed_topbottom": "20", "material_diameter": "2.85",
            "material_print_temperature": "200", "material_print_temperature_layer_0": "200",
            "material_bed_temperature": "60", "material_bed_temperature_layer_0": "60",
            "cool_fan_speed": "100", "roofing_layer_count": "0", "flooring_layer_count": "0",
        },
    }

    def validate_profile(self, profile_id: str) -> dict:
        if profile_id != self.profile["id"]:
            raise ValueError("The selected Cura profile is not approved.")
        return self.profile

    def inspect_result(self, raw_output: str, artifact_path: Path | None = None) -> dict:
        time_match = re.search(r"Print time \(s\):\s*(\d+)", raw_output)
        volume_match = re.search(r"Filament \(mm\^3\):\s*([\d.]+)", raw_output)
        if not time_match or not volume_match:
            raise RuntimeError("CuraEngine did not return both print time and filament volume.")
        seconds, volume = int(time_match.group(1)), float(volume_match.group(1))
        if seconds <= 0 or not math.isfinite(volume) or volume <= 0:
            raise RuntimeError("CuraEngine returned invalid time or filament volume.")
        warnings = [line.strip()[:240] for line in raw_output.splitlines() if "[warning]" in line.lower()]
        errors = [line.strip()[:240] for line in raw_output.splitlines() if "[error]" in line.lower()]
        if errors:
            raise RuntimeError("CuraEngine emitted error diagnostics: " + " ".join(errors[:2]))
        return {
            "schema_version": 1,
            "slicer_id": self.slicer_id,
            "slicer_version": "5.13.0",
            "profile": {k: self.profile[k] for k in ("id", "printer", "process", "material")},
            "print_time_seconds": seconds,
            "material_consumption": {
                "volume_mm3": volume,
            },
            "warnings": warnings[:5] + ([f"{len(warnings)-5} additional warnings omitted."] if len(warnings) > 5 else []),
            "errors": [],
        }

    def slice(self, staged_stl: Path, profile_id: str) -> dict:
        profile = self.validate_profile(profile_id)
        staged_stl = _validate_staged_stl(staged_stl)
        with tempfile.TemporaryDirectory(prefix="slicer-cura-") as private:
            root = Path(private)
            os.chmod(root, 0o700)
            staged_copy = root / "input.stl"
            shutil.copyfile(staged_stl, staged_copy)
            os.chmod(staged_copy, 0o600)
            cmd = ["docker", "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
                   "--env", "HOME=/tmp", "--env", "CURA_ENGINE_SEARCH_PATH=/opt/cura/resources/definitions:/opt/cura/resources/extruders",
                   "--memory=1g", "--cpus=2", "--pids-limit=64", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
                   "--mount", f"type=bind,src={root},dst=/input,readonly", "--entrypoint", "/opt/cura/CuraEngine",
                   CURA_IMAGE, "slice", "-v", "-j", profile["definition"], "-e0", "-j", profile["extruder_definition"]]
            for key, value in profile["settings"].items():
                cmd.extend(["-s", f"{key}={value}"])
            cmd.extend(["-l", "/input/input.stl", "-o", "/dev/null"])
            start = time.perf_counter()
            result = _run(cmd)
            elapsed = time.perf_counter() - start
        if result.returncode:
            warnings, errors, raw = _diagnostics(result)
            raise RuntimeError(" ".join(errors[:2]) or raw.splitlines()[-1])
        normalized = self.inspect_result(result.stdout + result.stderr)
        normalized["execution_wall_seconds"] = round(elapsed, 3)
        return normalized


class OrcaSlicerAdapter:
    slicer_id = "orcaslicer"
    resource_root = "/opt/orca/resources/profiles"
    profile = {
        "id": ORCA_PROFILE_ID,
        "printer": "Creality Ender-3 V3 SE 0.4 nozzle (bundled Orca preset)",
        "process": "0.20mm Standard @Creality Ender3V3SE 0.4",
        "material": "Generic PLA @System",
        "machine": f"{resource_root}/Creality/machine/Creality Ender-3 V3 SE 0.4 nozzle.json",
        "process_file": f"{resource_root}/Creality/process/0.20mm Standard @Creality Ender3V3SE 0.4.json",
        "filament": f"{resource_root}/OrcaFilamentLibrary/filament/Generic PLA @System.json",
    }

    def validate_profile(self, profile_id: str) -> dict:
        if profile_id != self.profile["id"]:
            raise ValueError("The selected OrcaSlicer profile is not approved.")
        machine = json.loads(ORCA_MACHINE_PROFILE.read_text(encoding="utf-8"))
        if (machine.get("printer_model") != "Creality Ender-3 V3 SE"
                or machine.get("nozzle_diameter") != ["0.4"]
                or machine.get("use_relative_e_distances") != "0"):
            raise ValueError("The approved OrcaSlicer machine profile failed validation.")
        return self.profile

    def inspect_result(self, raw_output: str, artifact_path: Path | None = None) -> dict:
        if artifact_path is None or not artifact_path.is_file():
            raise RuntimeError("OrcaSlicer did not produce the expected G-code artifact.")
        text = artifact_path.read_text(encoding="utf-8", errors="replace")
        time_match = re.search(r"^;TIME:([\d.]+)\s*$", text, re.M)
        volume_match = re.search(r"^; filament used \[cm3\] = ([\d.]+)\s*$", text, re.M | re.I)
        if not time_match or not volume_match:
            raise RuntimeError("OrcaSlicer G-code did not expose both computed time and filament volume.")
        seconds, volume = float(time_match.group(1)), float(volume_match.group(1)) * 1000
        if seconds <= 0 or not math.isfinite(volume) or volume <= 0:
            raise RuntimeError("OrcaSlicer returned invalid time or filament volume.")
        result_json_path = artifact_path.parent / "result.json"
        warnings: list[str] = []
        errors: list[str] = []
        if result_json_path.is_file():
            metadata = json.loads(result_json_path.read_text(encoding="utf-8"))
            for plate in metadata.get("sliced_plates", []):
                warning = str(plate.get("warning_message", "")).strip()
                if warning:
                    warnings.append(warning[:240])
            error = str(metadata.get("error_string", "")).strip()
            if error and error.lower() not in {"success", "success."}:
                errors.append(error[:240])
        if errors:
            raise RuntimeError("OrcaSlicer reported: " + " ".join(errors))
        return {
            "schema_version": 1,
            "slicer_id": self.slicer_id,
            "slicer_version": "2.4.2",
            "profile": {k: self.profile[k] for k in ("id", "printer", "process", "material")},
            "print_time_seconds": round(seconds),
            "material_consumption": {
                "volume_mm3": round(volume, 2),
            },
            "warnings": warnings,
            "errors": [],
            "output_artifact": {"media_type": "text/x-gcode", "size_bytes": artifact_path.stat().st_size},
        }

    def slice(self, staged_stl: Path, profile_id: str) -> dict:
        profile = self.validate_profile(profile_id)
        staged_stl = _validate_staged_stl(staged_stl)
        with tempfile.TemporaryDirectory(prefix="slicer-orca-") as private:
            root = Path(private)
            input_dir, output_dir = root / "input", root / "output"
            input_dir.mkdir(mode=0o700)
            output_dir.mkdir(mode=0o700)
            staged_copy = input_dir / "input.stl"
            shutil.copyfile(staged_stl, staged_copy)
            os.chmod(staged_copy, 0o600)
            # Orca's bundled Ender 3 V3 SE preset inherits relative E without a layer reset.
            # The CLI rejects it, so this fixed compatibility profile explicitly selects
            # absolute E mode. The model cannot change this setting.
            machine_path = root / "machine.json"
            shutil.copyfile(ORCA_MACHINE_PROFILE, machine_path)
            cmd = ["docker", "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
                   "--env", "HOME=/tmp", "--memory=2g", "--cpus=2", "--pids-limit=128", "--read-only",
                   "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m",
                   "--mount", f"type=bind,src={input_dir},dst=/input,readonly",
                   "--mount", f"type=bind,src={output_dir},dst=/output",
                   "--mount", f"type=bind,src={machine_path},dst=/machine.json,readonly",
                   "--entrypoint", "/opt/orca/bin/orca-slicer", ORCA_IMAGE,
                   "/input/input.stl", "--load-settings", f"/machine.json;{profile['process_file']}",
                   "--load-filaments", profile["filament"], "--slice", "0", "--debug", "2",
                   "--logfile", "/output/slicer.log", "--outputdir", "/output"]
            start = time.perf_counter()
            result = _run(cmd)
            elapsed = time.perf_counter() - start
            artifact = output_dir / "plate_1.gcode"
            if result.returncode:
                _, errors, raw = _diagnostics(result)
                detail = errors[:2] or raw.splitlines()[-8:]
                raise RuntimeError(" ".join(detail))
            normalized = self.inspect_result(result.stdout + result.stderr, artifact)
        normalized["execution_wall_seconds"] = round(elapsed, 3)
        return normalized


class BambuStudioAdapter:
    slicer_id = "bambustudio"
    profile_dir = BAMBU_PROFILE_DIR
    profile = {
        "id": BAMBU_PROFILE_ID,
        "printer": "Bambu Lab A1 0.4 nozzle (bundled Bambu preset)",
        "process": "0.20mm Standard @BBL A1 (bundled Bambu preset)",
        "material": "Bambu PLA Basic @BBL A1 (bundled Bambu preset)",
    }

    def validate_profile(self, profile_id: str) -> dict:
        if profile_id != self.profile["id"]:
            raise ValueError("The selected Bambu Studio profile is not approved.")
        manifest_path = BAMBU_PROFILE_DIR / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError("The resolved Bambu Studio profile manifest is missing.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (manifest.get("slicer_version") != "02.08.02.61"
                or manifest.get("appimage_sha256") != "d501b103fac5424513ec0e8d6bc145fb30719de2c7d94d7320d723740c81a7fd"):
            raise ValueError("The Bambu Studio profile bundle does not match the pinned release.")
        profile_bytes: dict[str, bytes] = {}
        profile_data: dict[str, dict] = {}
        for name in ("machine.json", "process.json", "filament.json"):
            path = BAMBU_PROFILE_DIR / name
            raw = path.read_bytes()
            declared = manifest.get("files", {}).get(name, {}).get("sha256")
            if not declared or hashlib.sha256(raw).hexdigest() != declared:
                raise ValueError(f"Bambu Studio profile checksum mismatch: {name}")
            data = json.loads(raw)
            if (data.get("from") != "system" or not data.get("inherits")
                    or not data.get("setting_id")):
                raise ValueError(
                    "Bambu CLI profiles must retain their bundled system preset identity and ancestry."
                )
            profile_bytes[name] = raw
            profile_data[name.removesuffix(".json")] = data
        if _digest_profile_files(profile_bytes) != manifest.get("bundle_sha256"):
            raise ValueError("The resolved Bambu Studio profile bundle digest does not match its manifest.")
        machine = profile_data["machine"]
        process = profile_data["process"]
        filament = profile_data["filament"]
        manifest_profiles = manifest.get("profiles", {})
        process_source_compat = manifest_profiles.get("process", {}).get("source_compatible_printers", [])
        filament_source_compat = manifest_profiles.get("filament", {}).get("source_compatible_printers", [])
        if (machine.get("printer_model") != "Bambu Lab A1"
                or machine.get("nozzle_diameter") != ["0.4"]
                or machine.get("printer_settings_id") != "Bambu Lab A1 0.4 nozzle"
                or machine.get("setting_id") != "GM030"
                or machine.get("inherits") != "fdm_bbl_3dp_001_common"
                or process.get("print_settings_id") != "0.20mm Standard @BBL A1"
                or process.get("setting_id") != "GP079"
                or process.get("inherits") != "fdm_process_single_0.20"
                or filament.get("filament_settings_id") != "Bambu PLA Basic @BBL A1"
                or filament.get("setting_id") != "GFSA00_04"
                or filament.get("inherits") != "Bambu PLA Basic @base"
                or manifest_profiles.get("machine", {}).get("preset_id") != "Bambu Lab A1 0.4 nozzle"
                or manifest_profiles.get("process", {}).get("preset_id") != "0.20mm Standard @BBL A1"
                or manifest_profiles.get("filament", {}).get("preset_id") != "Bambu PLA Basic @BBL A1"
                or process.get("compatible_printers") != process_source_compat
                or "Bambu Lab A1 0.4 nozzle" not in filament.get("compatible_printers", [])
                or "Bambu Lab A1 0.4 nozzle" not in process_source_compat
                or "Bambu Lab A1 0.4 nozzle" not in filament_source_compat
                or filament.get("filament_type") != ["PLA"]
                or filament.get("filament_diameter") != ["1.75"]):
            raise ValueError("The resolved Bambu Studio profile compatibility checks failed.")
        if (filament.get("filament_density") != ["1.26"]
                or manifest.get("target", {}).get("filament_density_g_cm3") != 1.26):
            raise ValueError("The resolved Bambu Studio filament density metadata failed validation.")
        return {**self.profile, "profile_source_digest": manifest["bundle_sha256"],
                "profile_manifest": manifest, "filament_profile": filament}

    def inspect_result(self, raw_output: str, artifact_path: Path | None = None) -> dict:
        if artifact_path is None or not artifact_path.is_file():
            raise RuntimeError("Bambu Studio did not produce the expected G-code artifact.")
        result_path = artifact_path.parent / "result.json"
        if not result_path.is_file():
            raise RuntimeError("Bambu Studio did not produce result.json.")
        metadata = json.loads(result_path.read_text(encoding="utf-8"))
        if metadata.get("return_code") != 0 or metadata.get("error_string") != "Success.":
            raise RuntimeError("Bambu Studio reported: " + str(metadata.get("error_string", "unknown error")))
        plates = metadata.get("sliced_plates", [])
        if not plates or plates[0].get("warning_message", "").strip():
            raise RuntimeError("Bambu Studio returned no successful plate or reported a plate warning.")
        plate = plates[0]
        time_seconds = float(plate.get("total_predication", 0))
        gcode = artifact_path.read_text(encoding="utf-8", errors="replace")
        # The current BambuStudio comment labels this value cm^3 but emits mm^3.
        # Cross-check with the reported grams and filament density before using it.
        volume_match = re.search(r"^; total filament volume \[cm\^3\] :\s*([\d.]+)\s*$", gcode, re.M)
        weight_match = re.search(r"^; total filament weight \[g\] :\s*([\d.]+)\s*$", gcode, re.M)
        density_match = re.search(r"^; filament_density:\s*([\d.]+)\s*$", gcode, re.M)
        if not volume_match or not weight_match or not density_match:
            raise RuntimeError("Bambu Studio G-code omitted filament volume/weight/density metadata.")
        volume = float(volume_match.group(1))
        weight = float(weight_match.group(1))
        density = float(density_match.group(1))
        if abs((volume / 1000.0) * density - weight) > max(0.2, weight * 0.03):
            raise RuntimeError("Bambu Studio filament volume failed the G-code mass/density cross-check.")
        length_match = re.search(r"^; total filament length \[mm\] :\s*([\d.]+)\s*$", gcode, re.M)
        if not length_match:
            length_match = re.search(r"^; filament used \[mm\] =\s*([\d.]+)\s*$", gcode, re.M)
        if not length_match:
            raise RuntimeError("Bambu Studio G-code omitted filament length needed for volume reconciliation.")
        length_mm = float(length_match.group(1))
        filament = json.loads((BAMBU_PROFILE_DIR / "filament.json").read_text(encoding="utf-8"))
        diameter_mm = float(filament["filament_diameter"][0])
        length_volume = math.pi * (diameter_mm / 2) ** 2 * length_mm
        if length_mm <= 0 or abs(length_volume - volume) / volume > 0.01:
            raise RuntimeError("Bambu Studio filament volume failed the length/diameter cross-check.")
        if time_seconds <= 0 or volume <= 0:
            raise RuntimeError("Bambu Studio returned invalid time or filament volume.")
        manifest = json.loads((BAMBU_PROFILE_DIR / "manifest.json").read_text(encoding="utf-8"))
        objects = plate.get("objects", [])
        bbox = objects[0].get("bbox", {}) if len(objects) == 1 else {}
        layer_match = re.search(r"^; total layer number:\s*(\d+)\s*$", gcode, re.M | re.I)
        return {
            "schema_version": 1,
            "slicer_id": self.slicer_id,
            "slicer_version": "02.08.02.61",
            "profile": {**self.profile,
                "profile_source_digest": manifest["bundle_sha256"],
            "profile_source": "Bambu Studio 02.08.02.61 bundled BBL system presets with resolved values and identity metadata",
                "printer_model": "Bambu Lab A1", "nozzle_diameter_mm": 0.4,
                "process_preset_id": "0.20mm Standard @BBL A1",
                "filament_preset_id": "Bambu PLA Basic @BBL A1"},
            "print_time_seconds": round(time_seconds),
            "material_consumption": {"volume_mm3": round(volume, 2)},
            "slice_summary": {
                "object_count": len(plate.get("objects", [])),
                "triangle_count": int(plate.get("triangle_count", 0)),
                "filament_count": len(plate.get("filaments", [])),
                "filament_changes": int(plate.get("filament_change_times", 0)),
                "object_dimensions_mm": {
                    "x": round(float(bbox.get("width", 0)), 3),
                    "y": round(float(bbox.get("depth", 0)), 3),
                    "z": round(float(bbox.get("height", 0)), 3),
                } if bbox else None,
                "layer_count": int(layer_match.group(1)) if layer_match else None,
            },
            "gcode_reconciliation": {
                "filament_length_mm": length_mm,
                "filament_diameter_mm": diameter_mm,
                "volume_from_length_mm3": round(length_volume, 2),
                "volume_error_fraction": round(abs(length_volume - volume) / volume, 6),
                "slicer_weight_g": weight,
                "slicer_density_g_cm3": density,
                "configured_quote_density_required": True,
            },
            "warnings": [],
            "errors": [],
            "output_artifact": {"media_type": "text/x-gcode", "size_bytes": artifact_path.stat().st_size},
        }

    def slice(self, staged_stl: Path, profile_id: str) -> dict:
        self.validate_profile(profile_id)
        staged_stl = _validate_staged_stl(staged_stl)
        with tempfile.TemporaryDirectory(prefix="slicer-bambu-") as private:
            root = Path(private)
            input_dir, output_dir = root / "input", root / "output"
            input_dir.mkdir(mode=0o700)
            output_dir.mkdir(mode=0o700)
            staged_copy = input_dir / "input.stl"
            shutil.copyfile(staged_stl, staged_copy)
            os.chmod(staged_copy, 0o600)
            cmd = ["docker", "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
                   "--env", "HOME=/tmp", "--memory=2g", "--cpus=2", "--pids-limit=128", "--read-only",
                   "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m",
                   "--mount", f"type=bind,src={input_dir},dst=/input,readonly",
                   "--mount", f"type=bind,src={output_dir},dst=/output",
                   "--entrypoint", "/opt/bambu/AppRun", BAMBU_IMAGE,
                   "--datadir", "/tmp/bambu-config",
                   "--load-settings", "/opt/profiles/bambu/machine.json;/opt/profiles/bambu/process.json",
                   "--load-filaments", "/opt/profiles/bambu/filament.json", "--ensure-on-bed",
                   "--orient", "1", "--arrange", "1", "--slice", "0", "--debug", "2",
                   "--outputdir", "/output", "/input/input.stl"]
            start = time.perf_counter()
            result = _run(cmd, timeout=90)
            elapsed = time.perf_counter() - start
            artifact = output_dir / "plate_1.gcode"
            if result.returncode:
                _, errors, raw = _diagnostics(result)
                raise RuntimeError(" ".join(errors[:2]) or raw.splitlines()[-8:])
            normalized = self.inspect_result(result.stdout + result.stderr, artifact)
        normalized["execution_wall_seconds"] = round(elapsed, 3)
        return normalized


class CrealityPrintAdapter:
    slicer_id = "crealityprint"
    profile = {
        "id": CREALITY_PROFILE_ID,
        "printer": "Creality Ender-3 V3 SE 0.4 nozzle (bundled Creality preset)",
        "process": "0.20mm Standard @Creality Ender-3 V3 SE 0.4 nozzle",
        "material": "CR-PLA @Creality Ender-3 V3 SE 0.4 nozzle",
    }

    def validate_profile(self, profile_id: str) -> dict:
        if profile_id != self.profile["id"]:
            raise ValueError("The selected Creality Print profile is not approved.")
        machine = json.loads((ROOT / "experiments/slicing/profiles/creality-ender3-v3se-0.4/machine.json").read_text(encoding="utf-8"))
        if machine.get("printer_model") != "Creality Ender-3 V3 SE" or machine.get("nozzle_diameter") != ["0.4"]:
            raise ValueError("The approved Creality Print machine profile failed validation.")
        return self.profile

    def inspect_result(self, raw_output: str, artifact_path: Path | None = None) -> dict:
        if artifact_path is None or not artifact_path.is_file():
            raise RuntimeError("Creality Print did not produce the expected G-code artifact.")
        text = artifact_path.read_text(encoding="utf-8", errors="replace")
        time_match = re.search(r"^;TIME:([\d.]+)\s*$", text, re.M)
        volume_match = re.search(r"^; filament used \[cm3\] = ([\d.]+)\s*$", text, re.M | re.I)
        if not time_match or not volume_match:
            raise RuntimeError("Creality Print G-code omitted computed time or filament volume.")
        seconds, volume = float(time_match.group(1)), float(volume_match.group(1)) * 1000
        if seconds <= 0 or not math.isfinite(volume) or volume <= 0:
            raise RuntimeError("Creality Print returned invalid time or filament volume.")
        result_path = artifact_path.parent / "result.json"
        warnings: list[str] = []
        errors: list[str] = []
        if result_path.is_file():
            metadata = json.loads(result_path.read_text(encoding="utf-8"))
            message = str(metadata.get("error_string", ""))
            if metadata.get("return_code") != 0 or message not in ("", "Success."):
                errors.append(message or "Creality Print returned a nonzero result code.")
            for plate in metadata.get("sliced_plates", []):
                warning = str(plate.get("warning_message", "")).strip()
                if warning:
                    warnings.append(warning[:240])
        if errors:
            raise RuntimeError("Creality Print reported: " + " ".join(errors))
        return {
            "schema_version": 1,
            "slicer_id": self.slicer_id,
            "slicer_version": "7.2.1.5476",
            "profile": self.profile,
            "print_time_seconds": round(seconds),
            "material_consumption": {"volume_mm3": round(volume, 2)},
            "warnings": warnings,
            "errors": errors,
            "output_artifact": {"media_type": "text/x-gcode", "size_bytes": artifact_path.stat().st_size},
        }

    def slice(self, staged_stl: Path, profile_id: str) -> dict:
        self.validate_profile(profile_id)
        staged_stl = _validate_staged_stl(staged_stl)
        with tempfile.TemporaryDirectory(prefix="slicer-creality-") as private:
            root = Path(private)
            input_dir, output_dir = root / "input", root / "output"
            input_dir.mkdir(mode=0o700)
            output_dir.mkdir(mode=0o700)
            staged_copy = input_dir / "input.stl"
            shutil.copyfile(staged_stl, staged_copy)
            os.chmod(staged_copy, 0o600)
            cmd = ["docker", "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
                   "--env", "HOME=/tmp", "--memory=2g", "--cpus=2", "--pids-limit=128", "--read-only",
                   "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m",
                   "--mount", f"type=bind,src={input_dir},dst=/input,readonly",
                   "--mount", f"type=bind,src={output_dir},dst=/output",
                   "--entrypoint", "/opt/creality/AppRun", CREALITY_IMAGE,
                   "--datadir", "/tmp/creality-config",
                   "--load-settings", "/opt/profiles/creality/machine.json;/opt/profiles/creality/process.json",
                   "--load-filaments", "/opt/profiles/creality/filament.json", "--ensure-on-bed",
                   "--orient", "1", "--arrange", "1", "--slice", "0", "--debug", "2",
                   "--logfile", "/output/slicer.log", "--outputdir", "/output", "/input/input.stl"]
            start = time.perf_counter()
            result = _run(cmd, timeout=90)
            elapsed = time.perf_counter() - start
            artifact = output_dir / "plate_1.gcode"
            if result.returncode:
                _, errors, raw = _diagnostics(result)
                raise RuntimeError(" ".join(errors[:2]) or raw.splitlines()[-8:])
            normalized = self.inspect_result(result.stdout + result.stderr, artifact)
        normalized["execution_wall_seconds"] = round(elapsed, 3)
        return normalized


ADAPTERS: dict[str, SlicerAdapter] = {
    "curaengine": CuraEngineAdapter(),
    "orcaslicer": OrcaSlicerAdapter(),
    "bambustudio": BambuStudioAdapter(),
    "crealityprint": CrealityPrintAdapter(),
}


def slice_with_adapter(slicer_id: str, staged_stl: Path, profile_id: str) -> dict:
    if slicer_id not in ADAPTERS:
        raise ValueError("The requested slicer is not approved.")
    return ADAPTERS[slicer_id].slice(staged_stl, profile_id)
