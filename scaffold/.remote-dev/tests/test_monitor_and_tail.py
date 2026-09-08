from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.job_ops as job_ops  # noqa: E402
import core.monitor_ops as monitor_ops  # noqa: E402
import core.state_store as state_store  # noqa: E402
from core.endpoint import Endpoint  # noqa: E402
from core.ssh_transport import RemoteCompleted  # noqa: E402


class _FakeRemote:
    """Script-aware fake for run_script used by monitor/tail tests."""

    def __init__(self) -> None:
        self.log_content = ""
        self.status = '{"status":"running"}'
        self.cursor: dict[str, int] = {}
        self.check_calls = 0

    def __call__(self, _endpoint, script, **_kwargs):
        if "REMOTE_DEV_VALIDATE" in script and "run.sh" not in script:
            return RemoteCompleted(0, "", "")
        if "nohup" in script:
            return RemoteCompleted(0, "999\n", "")
        if "REMOTE_DEV_TAIL" in script:
            # Emulate the incremental reader against in-memory content.
            offset = self.cursor.get("stdout", 0)
            data = self.log_content[offset:offset + 1048576]
            self.cursor["stdout"] = offset + len(data)
            import json as _json

            return RemoteCompleted(0, _json.dumps({"streams": {"stdout": {"data": data, "offset": self.cursor["stdout"], "size": len(self.log_content)}}, "rotated": []}) + "\n", "")
        # monitor condition check
        self.check_calls += 1
        return RemoteCompleted(0, f"{self.log_content}\n__STATUS__\n{self.status}\n", "")


class MonitorOpsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.endpoint = Endpoint(host="1.2.3.4", port=46000)
        self.tmp = tempfile.TemporaryDirectory()
        self.original_state_root = state_store.substrate_root
        self.original_job_runner = job_ops.run_script
        self.original_monitor_runner = monitor_ops.run_script
        state_store.substrate_root = lambda: Path(self.tmp.name)  # type: ignore[assignment]
        self.fake = _FakeRemote()
        job_ops.run_script = self.fake  # type: ignore[assignment]
        monitor_ops.run_script = self.fake  # type: ignore[assignment]

    def tearDown(self) -> None:
        state_store.substrate_root = self.original_state_root  # type: ignore[assignment]
        job_ops.run_script = self.original_job_runner  # type: ignore[assignment]
        monitor_ops.run_script = self.original_monitor_runner  # type: ignore[assignment]
        self.tmp.cleanup()

    def test_monitor_pattern_match(self) -> None:
        self.fake.log_content = "booting\nREADY on port 8000\n"
        payload = monitor_ops.remote_monitor(self.endpoint, command="run server", pattern=r"READY on port \d+", wait_timeout_ms=5000)
        result = payload["result"]
        self.assertEqual(result["outcome"], "success")
        self.assertTrue(result["monitor"]["matched"])
        self.assertIn("MATCHED", payload["text"])

    def test_monitor_wait_deadline_is_not_a_match(self) -> None:
        self.fake.log_content = "nothing useful\n"
        payload = monitor_ops.remote_monitor(self.endpoint, command="run server", pattern="NEVER_APPEARS", wait_timeout_ms=1500)
        result = payload["result"]
        self.assertFalse(result["monitor"]["matched"])
        self.assertIn("NOT matched", payload["text"])
        self.assertNotIn("MATCHED", payload["text"])

    def test_monitor_task_exit_before_match(self) -> None:
        self.fake.log_content = "crash\n"
        self.fake.status = '{"status":"failed","exit_code":1}'
        payload = monitor_ops.remote_monitor(self.endpoint, command="run server", pattern="READY", wait_timeout_ms=30000)
        result = payload["result"]
        self.assertFalse(result["monitor"]["matched"])
        self.assertEqual(result["monitor"]["task_status"], "failed")
        self.assertIn("NOT a match", payload["text"])

    def test_monitor_invalid_pattern_fails_fast(self) -> None:
        payload = monitor_ops.remote_monitor(self.endpoint, command="run server", pattern="([unclosed", wait_timeout_ms=1000)
        self.assertEqual(payload["result"]["status"], "invalid_pattern")
        self.assertEqual(payload["result"]["outcome"], "failed")

    def test_monitor_execution_deadline_default_is_bounded(self) -> None:
        payload = monitor_ops.remote_monitor(self.endpoint, command="run server")
        record_path = state_store.job_record_path(self.endpoint, payload["result"]["job"]["job_id"])
        import json as _json

        record = _json.loads(record_path.read_text())
        self.assertEqual(record["execution_deadline_ms"], 600000)


class JobTailIncrementalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.endpoint = Endpoint(host="1.2.3.4", port=46000)
        self.tmp = tempfile.TemporaryDirectory()
        self.original_state_root = state_store.substrate_root
        self.original_runner = job_ops.run_script
        state_store.substrate_root = lambda: Path(self.tmp.name)  # type: ignore[assignment]
        self.fake = _FakeRemote()
        job_ops.run_script = self.fake  # type: ignore[assignment]
        state_store.atomic_write_json(
            state_store.job_record_path(self.endpoint, "job-inc"),
            {"job_id": "job-inc", "target": self.endpoint.to_result_target(), "remote_dir": "/mnt/.remote-dev/jobs/job-inc"},
        )

    def tearDown(self) -> None:
        state_store.substrate_root = self.original_state_root  # type: ignore[assignment]
        job_ops.run_script = self.original_runner  # type: ignore[assignment]
        self.tmp.cleanup()

    def test_incremental_tail_does_not_repeat_old_content(self) -> None:
        self.fake.log_content = "line1\nline2\n"
        first = job_ops.remote_job_tail(None, job_id="job-inc", stream="stdout", incremental=True)
        self.assertIn("line1", first["text"])
        self.fake.log_content += "line3\n"
        second = job_ops.remote_job_tail(None, job_id="job-inc", stream="stdout", incremental=True)
        self.assertIn("line3", second["text"])
        self.assertNotIn("line1", second["text"])


if __name__ == "__main__":
    unittest.main()
