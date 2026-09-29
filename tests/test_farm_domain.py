import sqlite3
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from importlib.util import module_from_spec, spec_from_file_location

from experiments.farm_domain import (
    active_quote_configuration,
    add_business_configuration,
    add_material,
    connect_database,
    import_profile,
    record_profile_execution,
    register_stage3,
    set_profile_material,
    validate_profile,
    digest_files,
)
from experiments.slicing.openclaw_production_estimate import PROFILE_MAP

quote_spec = spec_from_file_location("quote_engine", Path(__file__).resolve().parents[1] / "experiments/quote-engine/quote.py")
quote_engine = module_from_spec(quote_spec)
quote_spec.loader.exec_module(quote_engine)


class FarmDomainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "farm.sqlite"

    def tearDown(self):
        self.temp.cleanup()

    def test_additive_migration_preserves_existing_records(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE TABLE stl_analyses (analysis_id TEXT PRIMARY KEY, filename TEXT NOT NULL, dimensions_json TEXT NOT NULL, analysis_json TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL)")
            connection.execute("CREATE TABLE production_estimates (estimate_id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL, filename TEXT NOT NULL, profile TEXT NOT NULL, slicing_json TEXT NOT NULL, estimated_material_g REAL NOT NULL, estimated_print_time_seconds INTEGER NOT NULL, quote_json TEXT NOT NULL, business_config_json TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL)")
            connection.execute("INSERT INTO stl_analyses VALUES ('a1','part.stl','{}','{}','2026-01-01','completed')")
            connection.execute("INSERT INTO production_estimates VALUES ('e1','a1','part.stl','cura','{}',1,60,'{}','{}','2026-01-01','completed')")
        connection = connect_database(self.database)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 10)
        self.assertEqual(connection.execute("SELECT analysis_id FROM stl_analyses").fetchone()[0], "a1")
        self.assertEqual(tuple(connection.execute("SELECT estimate_id,quote_readiness_status FROM production_estimates").fetchone()), ("e1", "legacy_unverified"))
        connection.close()
        connection = connect_database(self.database)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 10)
        connection.close()

    def test_v9_printer_rows_migrate_to_manual_adapter(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute("""CREATE TABLE printers (
                printer_id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,manufacturer TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',build_x_mm REAL,build_y_mm REAL,build_z_mm REAL,
                nozzle_diameter_mm REAL,notes TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL)""")
            connection.execute("INSERT INTO printers(printer_id,name,created_at) VALUES ('printer-old','Old printer','2026-01-01')")
            connection.execute("PRAGMA user_version = 9")
        connection = connect_database(self.database)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 10)
        self.assertEqual(tuple(connection.execute(
            "SELECT printer_id,adapter_id FROM printers WHERE printer_id='printer-old'").fetchone()),
            ("printer-old", "manual"))
        connection.close()

    def test_stage3_profiles_keep_execution_separate_from_quote_trust(self):
        registered = register_stage3(self.database)
        repeated = register_stage3(self.database)
        by_id = {item["profile_id"]: item for item in registered["profiles"]}
        repeated_by_id = {item["profile_id"]: item for item in repeated["profiles"]}
        self.assertEqual(by_id["cura-ultimaker2plus-generic-pla-normal"]["execution_status"], "available")
        self.assertEqual(by_id["cura-ultimaker2plus-generic-pla-normal"]["quote_status"], "needs_review")
        self.assertEqual(by_id["bambu-a1-0.4"]["execution_status"], "available")
        self.assertEqual(by_id["bambu-a1-0.4"]["quote_status"], "blocked")
        self.assertEqual(len(by_id["bambu-a1-0.4"]["source_digest"]), 64)
        manifest = json.loads((Path(__file__).resolve().parents[1] /
            "experiments/slicing/profiles/bambu-a1-0.4/manifest.json").read_text())
        self.assertEqual(by_id["bambu-a1-0.4"]["source_digest"], manifest["bundle_sha256"])
        bambu_quote = active_quote_configuration(self.database, "bambu-a1-0.4", manifest["bundle_sha256"])
        self.assertFalse(bambu_quote["ready"])
        self.assertEqual(bambu_quote["quote_status"], "blocked")
        cura_digest = digest_files({"cura-profile-map.json": json.dumps(
            PROFILE_MAP["cura-ultimaker2plus-generic-pla-normal"],
            sort_keys=True, separators=(",", ":")).encode("utf-8")})
        self.assertEqual(by_id["cura-ultimaker2plus-generic-pla-normal"]["source_digest"], cura_digest)
        self.assertEqual(by_id["bambu-a1-0.4"]["version"], repeated_by_id["bambu-a1-0.4"]["version"])
        connection = connect_database(self.database)
        bambu_versions = connection.execute("SELECT version,execution_status,quote_status FROM slicer_profiles WHERE profile_id='bambu-a1-0.4' ORDER BY version").fetchall()
        self.assertEqual([tuple(row) for row in bambu_versions], [(1,"available","blocked"),(2,"available","blocked")])
        connection.close()
        self.assertFalse(active_quote_configuration(self.database, "cura-ultimaker2plus-generic-pla-normal")["ready"])

    def test_material_and_business_versions_are_persisted(self):
        first = add_material(self.database, name="PLA", density=1.24,
            density_source="Supplier datasheet", density_source_ref="invoice-1",
            cost_per_kg=90, selling_price_per_kg=160, currency="BRL", material_id="pla")
        second = add_material(self.database, name="PLA", density=1.25,
            density_source="Supplier datasheet revision", density_source_ref="invoice-2",
            cost_per_kg=95, selling_price_per_kg=170, currency="BRL", material_id="pla")
        self.assertEqual((first["version"], second["version"]), (1, 2))
        one = add_business_configuration(self.database, currency="BRL", machine_hour_cost=2,
            energy_kwh_per_hour=.12, energy_cost_per_kwh=.95,
            minimum_margin_fraction=.35, minimum_job_fee=10, setup_fee=0, source="owner")
        two = add_business_configuration(self.database, currency="BRL", machine_hour_cost=2.5,
            energy_kwh_per_hour=.12, energy_cost_per_kwh=.95,
            minimum_margin_fraction=.4, minimum_job_fee=12, setup_fee=0, source="owner update")
        self.assertEqual((one["version"], two["version"]), (1, 2))
        connection = connect_database(self.database)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM materials").fetchone()[0], 2)
        self.assertEqual(connection.execute("SELECT version FROM business_configurations WHERE active=1").fetchone()[0], 2)
        connection.close()

    def test_quote_approval_requires_execution_and_material(self):
        register_stage3(self.database)
        with self.assertRaises(ValueError):
            validate_profile(self.database, "bambu-a1-0.4", "approved", [], "new-evidence")
        result = validate_profile(self.database, "cura-ultimaker2plus-generic-pla-normal",
                                  "needs_review", ["parity unresolved"], "stage3-report")
        self.assertEqual(result["quote_status"], "needs_review")

    def test_imported_profile_needs_execution_material_and_quote_validation(self):
        preset = Path(self.temp.name) / "preset.json"
        preset.write_text('{"preset":"machine-a"}', encoding="utf-8")
        material = add_material(self.database, name="PLA black", density=1.23,
            density_source="Supplier document", density_source_ref="supplier.pdf",
            cost_per_kg=100, selling_price_per_kg=180, currency="BRL", material_id="pla-black")
        imported = import_profile(self.database, profile_id="farm-profile", display_name="Farm profile",
            slicer_id="orcaslicer", slicer_version="2.4.2", source="operator import",
            machine_preset_id="machine-a", process_preset_id="process-b", material_preset_id="filament-c",
            source_files=[preset])
        self.assertEqual(imported["execution_status"], "unverified")
        self.assertEqual(imported["quote_status"], "needs_review")
        self.assertEqual(len(imported["source_digest"]), 64)
        record_profile_execution(self.database, "farm-profile", "available", "slice-log-1", [])
        revision = set_profile_material(self.database, "farm-profile", material["material_id"], material["version"], "supplier.pdf")
        self.assertEqual(revision["version"], 2)
        add_business_configuration(self.database, currency="BRL", machine_hour_cost=2,
            energy_kwh_per_hour=.1, energy_cost_per_kwh=1,
            minimum_margin_fraction=.35, minimum_job_fee=10, setup_fee=0, source="owner")
        validate_profile(self.database, "farm-profile", "approved", [], "validated-fixture-report")
        configuration = active_quote_configuration(self.database, "farm-profile", imported["source_digest"])
        self.assertTrue(configuration["ready"])
        self.assertEqual(configuration["material"]["density_value"], 1.23)
        self.assertEqual(configuration["business"]["version"], 1)
        self.assertFalse(active_quote_configuration(self.database,"farm-profile","wrong-digest")["ready"])

    def test_bambu_import_digest_matches_executed_bundle_and_stays_blocked(self):
        root = Path(__file__).resolve().parents[1]
        profile_dir = root / "experiments/slicing/profiles/bambu-a1-0.4"
        manifest = json.loads((profile_dir / "manifest.json").read_text())
        imported = import_profile(self.database, profile_id="bambu-a1-0.4",
            display_name="Bambu Studio / A1 0.4 / Standard / PLA Basic",
            slicer_id="bambustudio", slicer_version="02.08.02.61",
            source="experiments/slicing/profiles/bambu-a1-0.4",
            machine_preset_id="Bambu Lab A1 0.4 nozzle",
            process_preset_id="0.20mm Standard @BBL A1",
            material_preset_id="Bambu PLA Basic @BBL A1",
            source_files=[profile_dir / name for name in ("machine.json","process.json","filament.json")],
            nozzle=0.4)
        self.assertEqual(imported["source_digest"], manifest["bundle_sha256"])
        record_profile_execution(self.database, "bambu-a1-0.4", "available", "cli-evidence", [])
        validate_profile(self.database, "bambu-a1-0.4", "blocked",
            ["GUI-to-CLI parity pending"], "gui-parity-pending")
        readiness = active_quote_configuration(self.database, "bambu-a1-0.4", manifest["bundle_sha256"])
        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["quote_status"], "blocked")

    def test_bambu_quote_approval_requires_matching_gui_and_physical_evidence(self):
        root = Path(__file__).resolve().parents[1]
        profile_dir = root / "experiments/slicing/profiles/bambu-a1-0.4"
        manifest = json.loads((profile_dir / "manifest.json").read_text())
        imported = import_profile(self.database, profile_id="bambu-a1-0.4",
            display_name="Bambu Studio / A1 0.4 / Standard / PLA Basic",
            slicer_id="bambustudio", slicer_version="02.08.02.61",
            source="experiments/slicing/profiles/bambu-a1-0.4",
            machine_preset_id="Bambu Lab A1 0.4 nozzle",
            process_preset_id="0.20mm Standard @BBL A1",
            material_preset_id="Bambu PLA Basic @BBL A1",
            source_files=[profile_dir / name for name in ("machine.json","process.json","filament.json")],
            nozzle=0.4)
        record_profile_execution(self.database, "bambu-a1-0.4", "available", "cli-evidence", [])
        material = add_material(self.database, name="PLA", density=1.24,
            density_source="Farm material record", density_source_ref="material-doc",
            cost_per_kg=90, selling_price_per_kg=160, currency="BRL", material_id="pla")
        set_profile_material(self.database, "bambu-a1-0.4", material["material_id"], material["version"], "material-doc")
        add_business_configuration(self.database, currency="BRL", machine_hour_cost=2,
            energy_kwh_per_hour=.1, energy_cost_per_kwh=1,
            minimum_margin_fraction=.35, minimum_job_fee=10, setup_fee=0, source="owner")
        evidence_path = Path(self.temp.name) / "homologation.json"
        evidence_path.write_text(json.dumps({
            "profile_bundle_sha256": manifest["bundle_sha256"],
            "gui_parity_status": "passed",
            "physical_print_validation": {"status": "passed"},
            "quote_safe": True,
        }))
        with self.assertRaises(ValueError):
            validate_profile(self.database, "bambu-a1-0.4", "approved", [], "missing-evidence.json")
        approved = validate_profile(self.database, "bambu-a1-0.4", "approved", [], str(evidence_path))
        self.assertEqual(approved["quote_status"], "approved")
        quote_config = active_quote_configuration(self.database, "bambu-a1-0.4", imported["source_digest"])
        self.assertTrue(quote_config["ready"])

    def test_unquoted_slice_run_is_persisted_outside_quote_estimates(self):
        connection = connect_database(self.database)
        connection.execute("INSERT INTO stl_analyses(analysis_id,filename,dimensions_json,analysis_json,created_at,status) VALUES ('analysis-1','part.stl','{}','{}','2026-01-01','completed')")
        connection.commit()
        connection.close()
        record = {"analysis_id":"analysis-1","filename":"part.stl","profile":"cura-profile",
            "profile_version":1,"slicing":{"print_time_seconds":120,"material_consumption":{"volume_mm3":100},
                "profile_source_digest":"a"*64,"execution_status":"unverified",
                "quote_readiness":{"status":"needs_review","state":"UNDER_VALIDATION","findings":["validation pending"]},
                "quote_material_provenance":{"material_id":"pla","material_version":2,
                    "density_value":1.24,"density_source":"supplier datasheet"}},
            "quote_readiness_status":"needs_review"}
        run_file = Path(self.temp.name) / "run.json"
        run_file.write_text(json.dumps(record), encoding="utf-8")
        persistence = Path(__file__).resolve().parents[1] / "experiments/stl-analysis/persistence.py"
        result = subprocess.run([sys.executable,str(persistence),"store-slicer-run",
            "--database",str(self.database),"--run-file",str(run_file)],check=True,capture_output=True,text=True)
        run = json.loads(result.stdout)
        self.assertEqual(run["execution_status"], "completed")
        connection = connect_database(self.database)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM production_estimates").fetchone()[0], 0)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM slicer_runs").fetchone()[0], 1)
        connection.close()
        latest = subprocess.run([sys.executable,str(persistence),"latest-slicer-run","--database",
            str(self.database)],check=True,capture_output=True,text=True)
        snapshot = json.loads(latest.stdout)
        self.assertEqual(snapshot["slicing"]["profile_source_digest"], "a"*64)
        self.assertEqual(snapshot["slicing"]["quote_readiness"]["state"], "UNDER_VALIDATION")
        self.assertEqual(snapshot["slicing"]["quote_material_provenance"]["density_source"], "supplier datasheet")

    def test_quote_snapshot_persists_profile_material_and_business_versions(self):
        connection = connect_database(self.database)
        connection.execute("INSERT INTO stl_analyses(analysis_id,filename,dimensions_json,analysis_json,created_at,status) VALUES ('analysis-2','quote.stl','{}','{}','2026-01-02','completed')")
        connection.commit()
        connection.close()
        record = {"analysis_id":"analysis-2","filename":"quote.stl","profile":"farm-cura",
            "slicing":{"print_time_seconds":90,"material_consumption":{"volume_mm3":1234}},
            "estimated_material_g":1.52,"estimated_print_time_seconds":90,
            "quote":{"estimated_order_price_brl":12.5},
            "business_config":{"version":3,"material":{"id":"pla","version":2,"density_source":"supplier"}},
            "request_summary":"versioned quote snapshot","quote_readiness_status":"approved",
            "profile_version":4,"material_id":"pla","material_version":2,"business_config_version":3}
        quote_file = Path(self.temp.name) / "quote.json"
        quote_file.write_text(json.dumps(record),encoding="utf-8")
        persistence = Path(__file__).resolve().parents[1] / "experiments/stl-analysis/persistence.py"
        stored = subprocess.run([sys.executable,str(persistence),"store-estimate","--database",
            str(self.database),"--estimate-file",str(quote_file)],check=True,capture_output=True,text=True)
        self.assertTrue(json.loads(stored.stdout)["estimate_id"])
        latest = subprocess.run([sys.executable,str(persistence),"latest-estimate","--database",
            str(self.database)],check=True,capture_output=True,text=True)
        snapshot = json.loads(latest.stdout)
        self.assertEqual((snapshot["quote_readiness_status"],snapshot["profile_version"],
            snapshot["material_id"],snapshot["material_version"],snapshot["business_config_version"]),
            ("approved",4,"pla",2,3))
        self.assertEqual(snapshot["business_config"]["material"]["density_source"],"supplier")

    def test_quote_uses_minimum_order_fee_and_configured_currency(self):
        quote = quote_engine.quote(weight_g=10, hours=1, material_brl_kg=100,
            machine_brl_h=2, energy_kwh=0, energy_brl_kwh=0, margin=.35,
            quantity=1, minimum_order_fee=25, currency="USD")
        self.assertEqual(quote["suggested_order_price_brl"], 25)
        self.assertEqual(quote["currency"], "USD")


if __name__ == "__main__":
    unittest.main()
