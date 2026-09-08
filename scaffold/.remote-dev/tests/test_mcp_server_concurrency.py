from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mcp.server as mcp_server  # noqa: E402


class _FakeStdout:
    """Captures line-mode writes; mimics sys.stdout for server.send."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.lock = threading.Lock()

    def write(self, data: str) -> None:
        with self.lock:
            self.lines.append(data)

    def flush(self) -> None:
        return None


class ServerConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_call_tool = mcp_server.call_tool
        self.original_stdout = sys.stdout
        self.fake_out = _FakeStdout()
        sys.stdout = self.fake_out  # type: ignore[assignment]

    def tearDown(self) -> None:
        sys.stdout = self.original_stdout
        mcp_server.call_tool = self.original_call_tool
        # Drain any in-flight futures from the shared executor.
        mcp_server._EXECUTOR.shutdown(wait=True)
        mcp_server._EXECUTOR = mcp_server.ThreadPoolExecutor(max_workers=8, thread_name_prefix="remote-dev-mcp")

    def _responses(self) -> list[dict]:
        with self.fake_out.lock:
            return [json.loads(line) for line in self.fake_out.lines if line.strip()]

    def test_slow_tool_call_does_not_block_short_request(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def slow_call_tool(name, arguments):
            if arguments.get("command") == "slow":
                entered.set()
                release.wait(timeout=10)
                return {"text": "slow done\n", "result": {"outcome": "success", "status": "ok"}}
            return {"text": "fast done\n", "result": {"outcome": "success", "status": "ok"}}

        mcp_server.call_tool = slow_call_tool  # type: ignore[assignment]
        mcp_server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "remote.bash", "arguments": {"command": "slow"}}})
        self.assertTrue(entered.wait(timeout=5))
        start = time.monotonic()
        mcp_server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "remote.bash", "arguments": {"command": "fast"}}})
        # The fast request must be answered while the slow one still runs.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            ids = {msg["id"] for msg in self._responses()}
            if 2 in ids:
                break
            time.sleep(0.01)
        elapsed = time.monotonic() - start
        release.set()
        self.assertLess(elapsed, 5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            responses = self._responses()
            if {msg["id"] for msg in responses} == {1, 2}:
                break
            time.sleep(0.01)
        responses = self._responses()
        self.assertEqual({msg["id"] for msg in responses}, {1, 2})
        # Every line is a complete, parseable JSON message (no interleaving).
        with self.fake_out.lock:
            for line in self.fake_out.lines:
                json.loads(line)

    def test_is_error_distinguishes_query_success_from_task_failure(self) -> None:
        # Job started ok but task later failed remotely: query is not an error.
        payload = {"result": {"outcome": "success", "status": "failed", "job": {"job_id": "j"}}}
        self.assertFalse(mcp_server._tool_call_is_error(payload))
        # Job handle returned while running: not an error.
        payload = {"result": {"outcome": "success", "status": "running"}}
        self.assertFalse(mcp_server._tool_call_is_error(payload))
        # Request itself timed out: error.
        payload = {"result": {"outcome": "timeout", "status": "timeout"}}
        self.assertTrue(mcp_server._tool_call_is_error(payload))
        # Foreground command failed: error.
        payload = {"result": {"outcome": "failed", "status": "nonzero_exit"}}
        self.assertTrue(mcp_server._tool_call_is_error(payload))
        payload = {"result": {"outcome": "success", "status": "ok"}}
        self.assertFalse(mcp_server._tool_call_is_error(payload))


if __name__ == "__main__":
    unittest.main()
