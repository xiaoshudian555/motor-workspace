from __future__ import annotations

import re
import shlex
import time
from typing import Any

from .endpoint import Endpoint
from .job_ops import normalize_execution_deadline_ms
from .result import make_result, utc_now_iso
from .shell_ops import remote_bash
from .ssh_transport import run_script

MONITOR_WAIT_DEFAULT_MS = 30000
MONITOR_WAIT_MAX_MS = 600000
MONITOR_POLL_MS = 1000


def _duration_ms(start: float) -> int:
    return int(round((time.monotonic() - start) * 1000))


def remote_monitor(
    endpoint: Endpoint,
    *,
    command: str,
    cwd: str | None = None,
    description: str | None = None,
    timeout_ms: int | None = None,
    pattern: str | None = None,
    wait_timeout_ms: int | None = None,
    env: dict[str, str] | None = None,
    runtime_env: bool | None = None,
    execution_deadline_ms: int | None = None,
) -> dict[str, Any]:
    """Start a monitored background remote command.

    Without ``pattern`` this is exactly a background ``remote.bash`` with an
    independent execution deadline. With ``pattern`` the monitor performs a
    bounded wait: it polls the job logs (bounded by ``wait_timeout_ms``) and
    reports one of three distinct outcomes — matched, wait deadline reached
    without a match, or the task exited before any match. A wait timeout is
    never reported as a match.
    """
    # timeout_ms remains the legacy execution-deadline alias; the explicit
    # execution_deadline_ms wins. Neither makes the job run forever.
    legacy_deadline = timeout_ms if timeout_ms is not None else 600000
    deadline = execution_deadline_ms if execution_deadline_ms is not None else legacy_deadline
    execution_ms = normalize_execution_deadline_ms(deadline)
    started = utc_now_iso()
    start = time.monotonic()
    monitor_description = description or "Remote monitor"
    if pattern:
        monitor_description = f"{monitor_description}; pattern={pattern}"
    payload = remote_bash(
        endpoint,
        command=command,
        cwd=cwd,
        description=monitor_description,
        run_in_background=True,
        runtime_env=runtime_env,
        env=env or {},
        execution_deadline_ms=execution_ms,
    )
    result = payload["result"]
    result["tool"] = "remote.monitor"
    if not pattern:
        return payload
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        result["outcome"] = "failed"
        result["status"] = "invalid_pattern"
        result["summary"] = f"Remote monitor pattern is not a valid regex: {exc}."
        payload["text"] = result["summary"] + "\n"
        return payload
    job = result.get("job") or {}
    job_id = job.get("job_id")
    remote_dir = job.get("remote_dir")
    if not job_id or not remote_dir or result.get("status") not in {"running", "pending", "intent"}:
        # Job never started; surface the start failure rather than pretending
        # the condition wait ran.
        result["monitor"] = {"pattern": pattern, "matched": False, "reason": "job_not_running"}
        payload["text"] = payload["text"] + f"monitor: job did not start (status={result.get('status')}); pattern not evaluated\n"
        return payload

    if wait_timeout_ms is None:
        wait_ms = MONITOR_WAIT_DEFAULT_MS
    else:
        wait_ms = int(wait_timeout_ms)
        if wait_ms < 0:
            raise ValueError("wait_timeout_ms must be >= 0")
        if wait_ms > MONITOR_WAIT_MAX_MS:
            wait_ms = MONITOR_WAIT_MAX_MS
    deadline_monotonic = time.monotonic() + wait_ms / 1000
    matched = False
    last_status = "running"
    check_script = "\n".join(
        [
            "set +e",
            f"dir={shlex.quote(str(remote_dir))}",
            'cat "$dir/stdout.log" "$dir/stderr.log" 2>/dev/null | tail -c 65536',
            'if [ -f "$dir/status.json" ]; then echo __STATUS__; cat "$dir/status.json"; fi',
        ]
    )
    while True:
        completed = run_script(endpoint, check_script, timeout_ms=20000)
        body = completed.stdout or ""
        log_part, _, status_part = body.partition("__STATUS__")
        if regex.search(log_part):
            matched = True
            break
        if '"status":"succeeded"' in status_part or '"status":"failed"' in status_part \
                or '"status":"timeout"' in status_part or '"status":"cancelled"' in status_part:
            for terminal in ("succeeded", "failed", "timeout", "cancelled"):
                if f'"status":"{terminal}"' in status_part:
                    last_status = terminal
                    break
            break
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(MONITOR_POLL_MS / 1000, remaining))
    monitor_info = {
        "pattern": pattern,
        "matched": matched,
        "wait_timeout_ms": wait_ms,
        "task_status": last_status if not matched else "running",
    }
    result["monitor"] = monitor_info
    if matched:
        result["summary"] = f"Remote monitor matched pattern after {_duration_ms(start)} ms; task continues in background."
        text_note = f"monitor: MATCHED pattern {pattern!r}; job {job_id} still running (execution deadline {execution_ms} ms)\n"
    elif last_status != "running":
        result["summary"] = f"Remote monitor task exited ({last_status}) before pattern matched."
        text_note = f"monitor: task exited with status={last_status} before pattern {pattern!r} matched; NOT a match\n"
    else:
        result["summary"] = f"Remote monitor wait deadline ({wait_ms} ms) reached without a match; task still running."
        text_note = (
            f"monitor: wait deadline {wait_ms} ms reached, pattern {pattern!r} NOT matched; "
            f"job {job_id} still running — use remote.job_tail to keep watching\n"
        )
    payload["text"] = payload["text"] + text_note
    return payload
