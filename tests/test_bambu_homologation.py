import json
import tempfile
import unittest
from pathlib import Path

from experiments.slicing.slicer_adapters import BAMBU_PROFILE_ID, BambuStudioAdapter


class BambuHomologationTests(unittest.TestCase):
    def setUp(self):
        self.adapter = BambuStudioAdapter()

    def test_bundle_is_complete_digest_pinned_and_targeted(self):
        profile = self.adapter.validate_profile(BAMBU_PROFILE_ID)
        self.assertEqual(profile["profile_source_digest"], "ed6ace8e94e869076168f560f4cbaa971a09eeefb40d2b40adca1753a6bff242")
        for name in ("machine.json", "process.json", "filament.json"):
            data = json.loads((self.adapter.profile_dir / name).read_text())
            self.assertTrue(data["inherits"])
            self.assertEqual(data["from"], "system")
            self.assertTrue(data["setting_id"])

    def test_gcode_metrics_reconcile_independently(self):
        metadata = {
            "return_code": 0,
            "error_string": "Success.",
            "sliced_plates": [{
                "warning_message": "",
                "total_predication": 1271.9,
                "triangle_count": 12,
                "filament_change_times": 0,
                "objects": [{"bbox": {"width": 20, "depth": 20, "height": 12}}],
                "filaments": [{"id": 1}],
            }],
        }
        gcode = """; total filament volume [cm^3] : 43487.39
; total filament weight [g] : 54.79
; filament_density: 1.26
; total filament length [mm] : 18079.96
; total layer number: 60
"""
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "plate_1.gcode"
            artifact.write_text(gcode)
            (Path(directory) / "result.json").write_text(json.dumps(metadata))
            result = self.adapter.inspect_result("", artifact)
        self.assertEqual(result["material_consumption"]["volume_mm3"], 43487.39)
        self.assertLessEqual(result["gcode_reconciliation"]["volume_error_fraction"], 0.01)
        self.assertEqual(result["slice_summary"]["object_dimensions_mm"], {"x": 20, "y": 20, "z": 12})
        self.assertEqual(result["slice_summary"]["layer_count"], 60)


if __name__ == "__main__":
    unittest.main()
