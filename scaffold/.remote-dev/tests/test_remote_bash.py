from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.endpoint import Endpoint  # noqa: E402
from core.preview import MAX_JOB_TAIL_LINES, MAX_TEXT_CHARS  # noqa: E402
from core.ssh_transport import RemoteCompleted  # noqa: E402
import core.shell_ops as shell_ops  # noqa: E402
import core.state_store as state_store  # noqa: E402
import core.job_ops as job_ops  # noqa: E402


class RemoteBashTests(unittest.TestCase):
    def test_remote_bash_path_escape_returns_blocked_result(self) -> None:
        endpoint = Endpoint(host="1.2.3.4", port=46000, root="/mnt/motor-workspace")
        payload = shell_ops.remote_bash(endpoint, command="pwd", cwd="/tmp")
        self.assertEqual(payload["result"]["outcome"], "blocked")
        self.assertEqual(payload["result"]["status"], "cwd_outside_root")
        self.assertEqual(
            payload["result"]["next"]["endpoint_patch"],
            {"root": "/tmp", "cwd": "/tmp"},
        )
        self.assertIn("--root /tmp --cwd /tmp", payload["text"])

    def test_remote_bash_core_allows_secret_like_argv(self) -> None:
        endpoint = Endpoint(host="1.2.3.4", port=46000)
        original_state_root = state_store.substrate_root
        original_runner = shell_ops.run_script
        scripts = []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                state_store.substrate_root = lambda: Path(tmp)  # type: ignore[assignment]

                def fake_run_script(_endpoint, script, **_kwargs):
                    scripts.append(script)
                    return RemoteCompleted(0, "ok\n", "")

                shell_ops.run_script = fake_run_script  # type: ignore[assignment]
                payload = shell_ops.remote_bash(endpoint, command="echo token=abc")
                self.assertEqual(payload["result"]["outcome"], "success")
                self.assertIn("echo token=abc", scripts[0])
        finally:
            state_store.substrate_root = original_state_root  # type: ignore[assignment]
            shell_ops.run_script = original_runner  # type: ignore[assignment]

    def test_remote_bash_relative_cwd_hint_does_not_suggest_bad_root(self) -> None:
        endpoint = Endpoint(host="1.2.3.4", port=46000, root="/mnt/motor-workspace")
        payload = shell_ops.remote_bash(endpoint, command="pwd", cwd="tmp")
        self.assertEqual(payload["result"]["outcome"], "blocked")
        self.assertEqual(payload["result"]["next"]["suggested_action"], "rerun_with_absolute_cwd")
        self.assertNotIn("endpoint_patch", payload["result"]["next"])

    def test_remote_bash_success_writes_log_refs(self) -> None:
        endpoint = Endpoint(host="1.2.3.4", port=46000)
        original_state_root = state_store.substrate_root
        original_runner = shell_ops.run_script
        scripts = []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                state_store.substrate_root = lambda: Path(tmp)  # type: ignore[assignment]

                def fake_run_script(_endpoint, script, **_kwargs):
                    scripts.append(script)
                    return RemoteCompleted(0, "ok\n", "")

                shell_ops.run_script = fake_run_script  # type: ignore[assignment]
                payload = shell_ops.remote_bash(endpoint, command="echo ok")
                self.assertEqual(payload["result"]["outcome"], "success")
                self.assertTrue(Path(payload["result"]["refs"]["stdout"]).exists())
                self.assertIn('bash -c "$REMOTE_DEV_COMMAND"', scripts[0])
                self.assertNotIn("bash -lc", scripts[0])
        finally:
            state_store.substrate_root = original_state_root  # type: ignore[assignment]
            shell_ops.run_script = original_runner  # type: ignore[assignment]

    def test_background_remote_bash_missing_cwd_does_not_start_job(self) -> None:
        endpoint = Endpoint(host="1.2.3.4", port=46000)
        original_state_root = state_store.substrate_root
        original_runner = job_ops.run_script
        calls = []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                state_store.substrate_root = lambda: Path(tmp)  # type: ignore[assignment]

                def fake_run_script(_endpoint, script, **_kwargs):
                    calls.append(script)
                    return RemoteCompleted(70, "", "REMOTE_DEV_CWD_NOT_FOUND\n")

                job_ops.run_script = fake_run_script  # type: ignore[assignment]
                payload = shell_ops.remote_bash(
                    endpoint,
                    command="touch /mnt/motor-workspace/should-not-exist",
                    cwd="/mnt/motor-workspace/missing",
                    run_in_background=True,
                )
                self.assertEqual(payload["result"]["outcome"], "failed")
                self.assertEqual(payload["result"]["status"], "cwd_not_found")
                self.assertEqual(len(calls), 1)
                self.assertNotIn("touch /mnt/motor-workspace/should-not-exist", calls[0])
        finally:
            state_store.substrate_root = original_state_root  # type: ignore[assignment]
            job_ops.run_script = original_runner  # type: ignore[assignment]

    def test_background_remote_bash_duplicate_job_id_conflicting_params_is_blocked(self) -> None:
        endpoint = Endpoint(host="1.2.3.4", port=46000)
        original_state_root = state_store.substrate_root
        original_runner = job_ops.run_script
        try:
            with tempfile.TemporaryDirectory() as tmp:
                state_store.substrate_root = lambda: Path(tmp)  # type: ignore[assignment]
                state_store.atomic_write_json(state_store.job_record_path(endpoint, "job-existing"), {"job_id": "job-existing", "target": endpoint.to_result_target()})
                job_ops.run_script = lambda *_args, **_kwargs: RemoteCompleted(0, "", "")  # type: ignore[assignment]
                payload = job_ops.start_remote_job(endpoint, command="echo ok", job_id="job-existing")
                self.assertEqual(payload["result"]["outcome"], "blocked")
                self.assertEqual(payload["result"]["status"], "job_id_conflict")
        finally:
            state_store.substrate_root = original_state_root  # type: ignore[assignment]
            job_ops.run_script = original_runner  # type: ignore[assignment]

    def test_background_remote_bash_same_job_id_same_params_resumes(self) -> None:
        endpoint = Endpoint(host="1.2.3.4", port=46000)
        original_state_root = state_store.substrate_root
        original_runner = job_ops.run_script
        calls: list[str] = []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                state_store.substrate_root = lambda: Path(tmp)  # type: ignore[assignment]

                def fake_run_script(_endpoint, script, **_kwargs):
                    calls.append(script)
                    if "REMOTE_DEV_VALIDATE" in script and "run.sh" not in script:
                        return RemoteCompleted(0, "", "")
                    if "nohup" in script:
                        return RemoteCompleted(0, "4242\n", "")
                    return RemoteCompleted(0, '{"status":"running","job_id":"job-same"}\n', "")

                job_ops.run_script = fake_run_script  # type: ignore[assignment]
                first = job_ops.start_remote_job(endpoint, command="sleep 300", job_id="job-same", execution_deadline_ms=60000)
                self.assertEqual(first["result"]["outcome"], "success")
                self.assertEqual(first["result"]["status"], "running")
                started_scripts = len(calls)
                # Retry with identical parameters (e.g. response was lost):
                # must NOT run the start script again, must resume the record.
                second = job_ops.start_remote_job(endpoint, command="sleep 300", job_id="job-same", execution_deadline_ms=60000)
                self.assertEqual(second["result"]["outcome"], "success")
                self.assertTrue(second["result"]["resumed"])
                self.assertEqual(len(calls), started_scripts + 1)  # only the status check
                self.assertNotIn("nohup", calls[-1])
        finally:
            state_store.substrate_root = original_state_root  # type: ignore[assignment]
            job_ops.run_script = original_runner  # type: ignore[assignment]

    def test_start_remote_job_records_intent_before_remote_action(self) -> None:
        endpoint = Endpoint(host="1.2.3.4", port=46000)
        original_state_root = state_store.substrate_root
        original_runner = job_ops.run_script
        observed_phases: list[str] = []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                state_store.substrate_root = lambda: Path(tmp)  # type: ignore[assignment]

                def fake_run_script(_endpoint, script, **_kwargs):
                    record_path = state_store.job_record_path(endpoint, "job-intent")
                    if record_path.exists():
                        import json as _json

                        observed_phases.append(_json.loads(record_path.read_text())["phase"])
                    if "REMOTE_DEV_VALIDATE" in script and "run.sh" not in script:
                        return RemoteCompleted(0, "", "")
                    return RemoteCompleted(0, "123\n", "")

                job_ops.run_script = fake_run_script  # type: ignore[assignment]
                payload = job_ops.start_remote_job(endpoint, command="echo ok", job_id="job-intent")
                self.assertEqual(payload["result"]["outcome"], "success")
                # The record existed (as intent) before the first remote call.
                self.assertTrue(observed_phases)
                self.assertEqual(observed_phases[0], "intent")
                import json as _json

                final = _json.loads(state_store.job_record_path(endpoint, "job-intent").read_text())
                self.assertEqual(final["phase"], "started")
                self.assertEqual(final["pid"], 123)
        finally:
            state_store.substrate_root = original_state_root  # type: ignore[assignment]
            job_ops.run_script = original_runner  # type: ignore[assignment]

    def test_execution_deadline_defaults_and_validation(self) -> None:
        endpoint = Endpoint(host="1.2.3.4", port=46000)
        original_state_root = state_store.substrate_root
        original_runner = job_ops.run_script
        scripts: list[str] = []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                state_store.substrate_root = lambda: Path(tmp)  # type: ignore[assignment]

                def fake_run_script(_endpoint, script, **_kwargs):
                    scripts.append(script)
                    return RemoteCompleted(0, "7\n", "")

                job_ops.run_script = fake_run_script  # type: ignore[assignment]
                job_ops.start_remote_job(endpoint, command="echo ok")
                run_script_text = next(s for s in scripts if "run.sh" in s or "REMOTE_DEV_RUN" in s)
                default_s = job_ops.JOB_EXECUTION_DEFAULT_MS // 1000
                self.assertIn(f"timeout {default_s} ", run_script_text)
                with self.assertRaises(ValueError):
                    job_ops.start_remote_job(endpoint, command="echo ok", execution_deadline_ms=0)
                with self.assertRaises(ValueError):
                    job_ops.start_remote_job(endpoint, command="echo ok", execution_deadline_ms=-5)
        finally:
            state_store.substrate_root = original_state_root  # type: ignore[assignment]
            job_ops.run_script = original_runner  # type: ignore[assignment]

    def test_endpoint_from_record_preserves_identity_file(self) -> None:
        endpoint = Endpoint(host="1.2.3.4", port=46000, identity_file="/keys/id_rsa", connect_timeout_ms=5000)
        record = {"job_id": "job-x1", "target": endpoint.to_result_target(), "remote_dir": "/mnt/.remote-dev/jobs/job-x1"}
        rebuilt = job_ops.endpoint_from_job_record(record)
        self.assertEqual(rebuilt.identity_file, "/keys/id_rsa")
        self.assertEqual(rebuilt.connect_timeout_ms, 5000)

    def test_remote_job_tail_clamps_lines_and_text(self) -> None:
        endpoint = Endpoint(host="1.2.3.4", port=46000)
        original_state_root = state_store.substrate_root
        original_runner = job_ops.run_script
        scripts = []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                state_store.substrate_root = lambda: Path(tmp)  # type: ignore[assignment]
                job_id = "job-tail-test"
                state_store.atomic_write_json(
                    state_store.job_record_path(endpoint, job_id),
                    {"job_id": job_id, "target": endpoint.to_result_target(), "remote_dir": "/mnt/motor-workspace/.remote-dev/jobs/job-tail-test"},
                )

                def fake_run_script(_endpoint, script, **_kwargs):
                    scripts.append(script)
                    return RemoteCompleted(0, "x" * (MAX_TEXT_CHARS * 2), "")

                job_ops.run_script = fake_run_script  # type: ignore[assignment]
                payload = job_ops.remote_job_tail(None, job_id=job_id, lines=100000)
                self.assertIn(f"tail -n {MAX_JOB_TAIL_LINES}", scripts[0])
                self.assertIn("clamped", payload["result"]["warnings"][0])
                self.assertLessEqual(len(payload["text"]), MAX_TEXT_CHARS)
        finally:
            state_store.substrate_root = original_state_root  # type: ignore[assignment]
            job_ops.run_script = original_runner  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
