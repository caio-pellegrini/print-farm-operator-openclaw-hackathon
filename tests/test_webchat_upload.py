import base64
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from experiments.farm_domain import connect_database
from experiments.openclaw_channel_bridge import dispatch_plugin_request
from experiments.identity_workflow import UploadError

ROOT = Path(__file__).resolve().parents[1]
ANALYZER = ROOT / "experiments/stl-analysis/analyze_stl.py"
SECRET = b"webchat-upload-test-signing-key-at-least-32-bytes"
AUDIENCE = "print-farm-operator-test"


def sample_stl() -> bytes:
    data = bytearray(b"webchat binary triangle".ljust(80, b" "))
    data += struct.pack("<I", 1)
    data += struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 20, 0, 0, 0, 20, 12, 0)
    return bytes(data)


class WebChatUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "farm.sqlite"
        self.spool = self.root / "private" / "spool"
        self.jobs = self.root / "private" / "jobs"
        self.jobs.mkdir(parents=True, mode=0o700)
        self.key_file = self.root / "identity.key"
        self.key_file.write_bytes(SECRET)
        self.key_file.chmod(0o600)

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, *, filename="sample.stl", content=None, channel="webchat", message_id="webchat-message-1"):
        return {
            "operation": "submit_webchat_stl",
            "database": str(self.db),
            "spool_root": str(self.spool),
            "private_jobs_root": str(self.jobs),
            "key_file": str(self.key_file),
            "audience": AUDIENCE,
            "channel": channel,
            "account_id": "local-webchat",
            "sender_id": "authenticated-browser-owner",
            "conversation_id": "webchat-session-1",
            "message_id": message_id,
            "filename": filename,
            "attachment_b64": base64.b64encode(content if content is not None else sample_stl()).decode("ascii"),
            "analyzer_script": str(ANALYZER),
        }

    def test_valid_webchat_stl_persists_one_analysis_request_and_job_across_restart(self):
        result = dispatch_plugin_request(self.payload())
        self.assertEqual(result["status"], "created")
        self.assertFalse(result["idempotent_replay"])
        self.assertEqual(result["quote_status"], "draft")
        self.assertEqual(result["analysis"]["dimensions_mm"], {"x": 20.0, "y": 20.0, "z": 12.0})
        self.assertIsNone(result["analysis"]["volume_cm3"])

        replay = dispatch_plugin_request(self.payload())
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["job_id"], result["job_id"])
        with connect_database(self.db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM print_jobs").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM stl_analyses").fetchone()[0], 1)
            file_row = connection.execute("SELECT relative_path,size_bytes,sha256 FROM job_files").fetchone()
            self.assertTrue((self.jobs / file_row["relative_path"]).is_file())
            self.assertEqual(file_row["sha256"], __import__("hashlib").sha256(sample_stl()).hexdigest())

        script = (
            "import json,sys; sys.path.insert(0,sys.argv[2]); from persistence import latest; "
            "print(json.dumps(latest(__import__('pathlib').Path(sys.argv[1]))))"
        )
        restarted = subprocess.run(
            [sys.executable, "-c", script, str(self.db), str(ROOT / "experiments/stl-analysis")],
            check=True, capture_output=True, text=True,
        )
        analysis = json.loads(restarted.stdout)
        self.assertEqual(analysis["analysis_id"], result["analysis_id"])
        self.assertEqual(analysis["request"], {
            "status": "pending", "job_id": result["job_id"], "job_status": "created",
            "quote_status": "draft",
            "quote_reason": "No Stage 4 quote trust check has been run for this request.",
        })

    def test_invalid_non_stl_oversize_traversal_and_non_webchat_events_are_rejected(self):
        bad_inputs = [
            self.payload(filename="sample.obj"),
            self.payload(content=b"not an STL", message_id="bad-signature"),
            self.payload(filename="../sample.stl", message_id="traversal"),
            self.payload(content=b"x" * (25 * 1024 * 1024 + 1), message_id="oversize"),
            self.payload(channel="whatsapp", message_id="wrong-channel"),
        ]
        for payload in bad_inputs:
            with self.subTest(filename=payload["filename"], message_id=payload["message_id"]):
                with self.assertRaises((UploadError, ValueError, PermissionError)):
                    dispatch_plugin_request(payload)
        with connect_database(self.db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM farm_users").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM print_jobs").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
