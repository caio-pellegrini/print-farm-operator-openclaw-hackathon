import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from experiments.farm_domain import add_printer, connect_database
from experiments.identity_workflow import AuthorizationError
from experiments.production_workflow import (
    assign_printer,
    finish_job,
    inspect_production_job,
    list_ready_jobs,
    mark_ready_for_production,
    start_job,
)


class ProductionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "farm.sqlite"
        self.owner = {"user_id": "owner-1", "capabilities": ["job.read_any", "job.status.update"]}
        self.operator = {"user_id": "operator-1", "capabilities": ["job.read_any", "job.status.update"]}
        self.customer = {"user_id": "customer-1", "capabilities": ["request.read_own"]}
        self.other_customer = {"user_id": "customer-2", "capabilities": ["request.read_own"]}
        self.printer = add_printer(self.database, name="Manual A1", manufacturer="Bambu Lab", model="A1")
        with connect_database(self.database) as connection:
            for user_id, role in (("owner-1", "OWNER"), ("operator-1", "OPERATOR"),
                                  ("customer-1", "CUSTOMER"), ("customer-2", "CUSTOMER")):
                connection.execute("INSERT INTO farm_users VALUES (?,?,?,?)",
                                   (user_id, f"local|{user_id}", user_id, "2026-09-28T00:00:00+00:00"))
                connection.execute("INSERT INTO user_roles VALUES (?,?,?)",
                                   (user_id, role, "2026-09-28T00:00:00+00:00"))
            connection.execute("INSERT INTO stl_analyses VALUES ('analysis-1','model.stl','{}','{}','2026-09-28','completed','customer-1')")
            connection.execute("INSERT INTO quotes VALUES ('quote-1','analysis-1',NULL,'customer-1','approved','{}','2026-09-28')")
            connection.execute("INSERT INTO orders(order_id,quote_id,customer_user_id,quantity,status,created_at,request_summary) VALUES ('order-1','quote-1','customer-1',1,'approved','2026-09-28','A test print')")
            connection.execute("INSERT INTO print_jobs(job_id,order_id,status,created_at) VALUES ('job-ready','order-1','queued','2026-09-28')")
            connection.execute("INSERT INTO stl_analyses VALUES ('analysis-2','other.stl','{}','{}','2026-09-28','completed','customer-2')")
            connection.execute("INSERT INTO quotes VALUES ('quote-2','analysis-2',NULL,'customer-2','approved','{}','2026-09-28')")
            connection.execute("INSERT INTO orders(order_id,quote_id,customer_user_id,quantity,status,created_at,request_summary) VALUES ('order-2','quote-2','customer-2',1,'approved','2026-09-28','Other print')")
            connection.execute("INSERT INTO print_jobs(job_id,order_id,status,created_at) VALUES ('job-other','order-2','queued','2026-09-28')")
            connection.execute("INSERT INTO stl_analyses VALUES ('analysis-draft','draft.stl','{}','{}','2026-09-28','completed','customer-1')")
            connection.execute("INSERT INTO quotes VALUES ('quote-draft','analysis-draft',NULL,'customer-1','draft','{}','2026-09-28')")
            connection.execute("INSERT INTO orders(order_id,quote_id,customer_user_id,quantity,status,created_at,request_summary) VALUES ('order-draft','quote-draft','customer-1',1,'pending','2026-09-28','Draft print')")
            connection.execute("INSERT INTO print_jobs(job_id,order_id,status,created_at) VALUES ('job-draft','order-draft','created','2026-09-28')")

    def tearDown(self):
        self.temp.cleanup()

    def test_owner_and_operator_run_audited_manual_print_lifecycle(self):
        ready = list_ready_jobs(self.database, self.owner)
        self.assertEqual([job["job_id"] for job in ready], ["job-other", "job-ready"])
        assigned = assign_printer(self.database, self.operator, "job-ready", self.printer["printer_id"])
        self.assertEqual((assigned["status"], assigned["adapter_id"]), ("READY_FOR_PRODUCTION", "manual"))
        started = start_job(self.database, self.operator, "job-ready")
        self.assertEqual(started["status"], "IN_PROGRESS")
        completed = finish_job(self.database, self.owner, "job-ready", "COMPLETED", "Part inspected")
        self.assertEqual(completed["status"], "COMPLETED")
        with connect_database(self.database) as connection:
            events = connection.execute("SELECT event_type,details_json,actor_user_id FROM job_events WHERE job_id='job-ready' ORDER BY rowid").fetchall()
            persisted = connection.execute("SELECT j.status,o.status,j.printer_id FROM print_jobs j JOIN orders o USING(order_id) WHERE job_id='job-ready'").fetchone()
        self.assertEqual([row["event_type"] for row in events],
                         ["printer_assigned", "production_started", "production_completed"])
        self.assertEqual([row["actor_user_id"] for row in events], ["operator-1", "operator-1", "owner-1"])
        self.assertEqual(json.loads(events[0]["details_json"])["execution"], "operator_confirmed")
        self.assertEqual(tuple(persisted), ("completed", "completed", self.printer["printer_id"]))

    def test_failure_is_a_terminal_operator_confirmed_outcome(self):
        assign_printer(self.database, self.operator, "job-ready", self.printer["printer_id"])
        start_job(self.database, self.operator, "job-ready")
        result = finish_job(self.database, self.operator, "job-ready", "FAILED", "First layer detached")
        self.assertEqual(result["status"], "FAILED")
        with self.assertRaises(ValueError):
            start_job(self.database, self.operator, "job-ready")

    def test_customer_can_read_own_persisted_status_but_cannot_act_or_read_others(self):
        self.assertEqual(inspect_production_job(self.database, self.customer, "job-ready")["status"], "READY_FOR_PRODUCTION")
        for action in (
            lambda: list_ready_jobs(self.database, self.customer),
            lambda: assign_printer(self.database, self.customer, "job-ready", self.printer["printer_id"]),
            lambda: start_job(self.database, self.customer, "job-ready"),
            lambda: finish_job(self.database, self.customer, "job-ready", "FAILED"),
        ):
            with self.assertRaises(AuthorizationError):
                action()
        with self.assertRaises(AuthorizationError):
            inspect_production_job(self.database, self.customer, "job-other")
        with self.assertRaises(AuthorizationError):
            inspect_production_job(self.database, self.other_customer, "job-ready")

    def test_quote_trust_gate_and_transition_order_are_required(self):
        self.assertEqual([job["job_id"] for job in list_ready_jobs(self.database, self.operator)],
                         ["job-other", "job-ready"])
        with self.assertRaises(ValueError):
            assign_printer(self.database, self.operator, "job-draft", self.printer["printer_id"])
        with self.assertRaises(ValueError):
            mark_ready_for_production(self.database, self.operator, "job-draft")
        with self.assertRaises(ValueError):
            start_job(self.database, self.operator, "job-ready")
        with self.assertRaises(ValueError):
            finish_job(self.database, self.operator, "job-ready", "COMPLETED")
        with self.assertRaises(ValueError):
            finish_job(self.database, self.operator, "job-ready", "PAUSED")

    def test_approved_created_job_can_become_ready_with_an_audit_event(self):
        with connect_database(self.database) as connection:
            connection.execute("UPDATE print_jobs SET status='created' WHERE job_id='job-ready'")
        result = mark_ready_for_production(self.database, self.operator, "job-ready")
        self.assertEqual(result["status"], "READY_FOR_PRODUCTION")
        with connect_database(self.database) as connection:
            event = connection.execute("SELECT event_type FROM job_events WHERE job_id='job-ready'").fetchone()
        self.assertEqual(event[0], "ready_for_production")

    def test_persisted_state_survives_a_new_process(self):
        assign_printer(self.database, self.operator, "job-ready", self.printer["printer_id"])
        start_job(self.database, self.operator, "job-ready")
        script = "from pathlib import Path; import json,sys; from experiments.production_workflow import inspect_production_job; print(json.dumps(inspect_production_job(Path(sys.argv[1]), {'user_id':'customer-1','capabilities':['request.read_own']}, 'job-ready')))"
        read = subprocess.run([sys.executable, "-c", script, str(self.database)],
                              check=True, capture_output=True, text=True)
        result = json.loads(read.stdout)
        self.assertEqual(result["status"], "IN_PROGRESS")
        self.assertEqual(result["printer_id"], self.printer["printer_id"])


if __name__ == "__main__":
    unittest.main()
