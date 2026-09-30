import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from experiments.farm_domain import connect_database
from experiments.identity_workflow import AuthorizationError
from experiments.onboarding import _parse_answer, advance_onboarding, local_staff_principal
from experiments.openclaw_local_tools import dispatch
from experiments.production_workflow import list_ready_jobs


class PersistentOnboardingAndToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "farm.sqlite"

    def tearDown(self):
        self.temp.cleanup()

    def test_onboarding_resume_and_solo_owner_operator_roles_persist(self):
        first = advance_onboarding(self.db)
        self.assertEqual(first["field"], "printer_count")
        self.assertEqual(advance_onboarding(self.db, "2")["field"], "printer_models")
        self.assertEqual(advance_onboarding(self.db, "Bambu A1, Ender 3 V3 SE")["field"], "nozzle_sizes")
        self.assertEqual(advance_onboarding(self.db, "0.4, ?")["field"], "staff_mode")
        self.assertEqual(advance_onboarding(self.db, "alone")["field"], "primary_slicer")
        self.assertEqual(advance_onboarding(self.db, "Bambu Studio")["field"], "primary_material")
        self.assertEqual(advance_onboarding(self.db, "PLA")["field"], "customer_messaging_later")
        completed = advance_onboarding(self.db, "yes")
        self.assertTrue(completed["complete"])
        self.assertEqual(completed["configuration"]["roles"], ["OWNER", "OPERATOR"])
        self.assertEqual([p["model"] for p in completed["printers"]], ["Bambu A1", "Ender 3 V3 SE"])
        self.assertEqual(completed["printers"][1]["nozzle_diameter_mm"], None)
        self.assertEqual(completed["summary"]["printer_count"], 2)
        self.assertEqual(completed["summary"]["operating_mode"], "solo")
        self.assertEqual(completed["summary"]["roles"], ["Owner", "Operator"])
        self.assertIn("Upload an STL", completed["summary"]["next_action"])
        self.assertEqual(local_staff_principal(self.db)["roles"], ["OPERATOR", "OWNER"])
        # A new process reads the completed setup from the domain database.
        script = "import json,sys; from pathlib import Path; from experiments.onboarding import advance_onboarding; print(json.dumps(advance_onboarding(Path(sys.argv[1]))))"
        resumed = subprocess.run([sys.executable, "-c", script, str(self.db)],
                                 check=True, capture_output=True, text=True)
        self.assertTrue(json.loads(resumed.stdout)["complete"])

    def test_team_setup_keeps_owner_role_and_customer_cannot_use_tools(self):
        for answer in ("1", "Prusa MK4", "0.4", "operators", "PrusaSlicer", "PETG", "no"):
            result = advance_onboarding(self.db, answer)
        self.assertEqual(result["configuration"]["roles"], ["OWNER"])
        customer = {"user_id": "c", "roles": ["CUSTOMER"], "capabilities": ["request.submit", "request.read_own"]}
        with self.assertRaises(AuthorizationError):
            list_ready_jobs(self.db, customer)

    def test_exact_farm_state_questions_read_the_persisted_onboarding_after_restart(self):
        for answer in ("1", "Bambu Lab A1", "0.4", "alone", "Bambu Studio", "PLA", "no"):
            advance_onboarding(self.db, answer)

        # These are the exact natural-language questions from the live smoke test.
        expected = {
            "quais impressoras estão configuradas na minha farm?":
                ("printers", [{"model": "Bambu Lab A1", "nozzle_diameter_mm": 0.4}]),
            "qual meu slicer principal?": ("primary_slicer", "Bambu Studio"),
            "qual meu material principal?": ("primary_material", "PLA"),
            "What are my roles?": ("roles", ["OPERATOR", "OWNER"]),
        }
        self.assertEqual(_parse_answer("nozzle_sizes", "0,4 mm", {"printer_count": 1}), [0.4])
        self.assertEqual(_parse_answer("nozzle_sizes", "0,4 mm; 0,6 mm", {"printer_count": 2}), [0.4, 0.6])
        state = dispatch({"database": str(self.db), "operation": "get_farm_configuration"})
        self.assertEqual(state["onboarding"], {
            "status": "complete", "complete": True, "step": 7,
            "total_steps": 7, "mode": "alone",
        })
        self.assertEqual(state["printer_count"], 1)
        self.assertEqual(state["primary_material"], "PLA")
        for question, (field, value) in expected.items():
            with self.subTest(question=question):
                if field == "printers":
                    self.assertEqual([{key: printer[key] for key in ("model", "nozzle_diameter_mm")}
                                      for printer in state[field]], value)
                else:
                    self.assertEqual(state[field], value)

        script = (
            "import json,sys; from pathlib import Path; "
            "from experiments.openclaw_local_tools import dispatch; "
            "print(json.dumps(dispatch({'database':sys.argv[1],"
            "'operation':'get_farm_configuration'})))"
        )
        restarted = subprocess.run([sys.executable, "-c", script, str(self.db)],
                                   check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(restarted.stdout), state)

    def test_production_tools_use_existing_workflow_and_persist_completion(self):
        for answer in ("1", "Bambu A1", "0.4", "alone", "Bambu Studio", "PLA", "no"):
            advance_onboarding(self.db, answer)
        printer = advance_onboarding(self.db)["printers"][0]
        with connect_database(self.db) as connection:
            connection.execute("INSERT INTO stl_analyses VALUES ('a','part.stl','{}','{}','2026-09-28','completed',?)", (local_staff_principal(self.db)["user_id"],))
            connection.execute("INSERT INTO quotes VALUES ('q','a',NULL,?,'approved','{}','2026-09-28')", (local_staff_principal(self.db)["user_id"],))
            connection.execute("INSERT INTO orders(order_id,quote_id,customer_user_id,quantity,status,created_at,request_summary) VALUES ('o','q',?,1,'approved','2026-09-28','Test')", (local_staff_principal(self.db)["user_id"],))
            connection.execute("INSERT INTO print_jobs(job_id,order_id,status,created_at) VALUES ('j','o','queued','2026-09-28')")
        principal = local_staff_principal(self.db)
        listing = dispatch({"database": str(self.db), "operation": "list_ready_jobs"})
        self.assertEqual([job["job_id"] for job in listing["jobs"]], ["j"])
        self.assertEqual(dispatch({"database": str(self.db), "operation": "assign_printer",
                                   "job_id": "j", "printer_id": printer["printer_id"]})["status"], "READY_FOR_PRODUCTION")
        self.assertEqual(dispatch({"database": str(self.db), "operation": "start_job", "job_id": "j"})["status"], "IN_PROGRESS")
        self.assertEqual(dispatch({"database": str(self.db), "operation": "finish_job", "job_id": "j",
                                   "outcome": "COMPLETED"})["status"], "COMPLETED")
        with connect_database(self.db) as connection:
            self.assertEqual(connection.execute("SELECT status FROM print_jobs WHERE job_id='j'").fetchone()[0], "completed")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM job_events WHERE job_id='j'").fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
