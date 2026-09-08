from __future__ import annotations

import contextlib
import hashlib
import json
import re
import shlex
import time
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from .endpoint import Endpoint
from .preview import MAX_JOB_TAIL_LINES, MAX_TEXT_CHARS, compact_text
from .result import make_result, utc_now_iso
from .ssh_transport import run_script
from .state_store import atomic_write_json, find_job_record, job_record_path

JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,95}$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Separate budget roles (see scaffold/docs/mcp-repair-handoff.md):
# - wait budget: how long a foreground request may block inside run_script.
# - execution deadline: lifetime of the remote command (shell `timeout`).
# Historically one default covered both; they must stay independent.
REQUEST_WAIT_DEFAULT_MS = 120000
REQUEST_WAIT_MAX_MS = 3600000
JOB_EXECUTION_DEFAULT_MS = 3600000
JOB_EXECUTION_MAX_MS = 86400000
JOB_TAIL_CURSOR_MAX_BYTES = 1048576


def normalize_wait_timeout_ms(
    timeout_ms: int | None,
    timeout: int | None = None,
    *,
    default_ms: int = REQUEST_WAIT_DEFAULT_MS,
) -> int:
    """Resolve the request wait budget in milliseconds.

    Explicit ``timeout_ms`` wins over the legacy ``timeout`` alias. Zero and
    negative values are rejected instead of being silently widened to "run
    forever". Omitted values fall back to ``default_ms``.
    """
    if timeout_ms is not None:
        value = int(timeout_ms)
    elif timeout is not None:
        value = int(timeout)
    else:
        return int(default_ms)
    if value <= 0:
        raise ValueError("timeout must be a positive number of milliseconds (0/negative are not allowed)")
    if value > REQUEST_WAIT_MAX_MS:
        raise ValueError(f"timeout must not exceed {REQUEST_WAIT_MAX_MS} ms")
    return value


def normalize_execution_deadline_ms(execution_deadline_ms: int | None) -> int:
    """Resolve a background job execution deadline in milliseconds.

    ``None`` means the default bounded deadline (1h), never "run forever".
    Zero and negative values are rejected.
    """
    if execution_deadline_ms is None:
        return JOB_EXECUTION_DEFAULT_MS
    value = int(execution_deadline_ms)
    if value <= 0:
        raise ValueError("execution_deadline_ms must be a positive number of milliseconds (0/negative are not allowed)")
    if value > JOB_EXECUTION_MAX_MS:
        raise ValueError(f"execution_deadline_ms must not exceed {JOB_EXECUTION_MAX_MS} ms")
    return value


def _duration_ms(start: float) -> int:
    return int(round((time.monotonic() - start) * 1000))


def new_job_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"job-{stamp}-{uuid.uuid4().hex[:8]}"


def require_job_id(value: str) -> str:
    if not JOB_ID_RE.fullmatch(value):
        raise ValueError("job id must be 3-96 chars from A-Z a-z 0-9 _ . -")
    return value


def require_env_name(value: str) -> str:
    if not ENV_NAME_RE.fullmatch(value):
        raise ValueError(f"invalid environment variable name: {value!r}")
    return value


def remote_job_dir(endpoint: Endpoint, job_id: str) -> str:
    require_job_id(job_id)
    return str(PurePosixPath(endpoint.root) / ".remote-dev" / "jobs" / job_id)


def _job_fingerprint(
    endpoint: Endpoint,
    *,
    command: str,
    cwd: str,
    env: dict[str, str],
    execution_deadline_ms: int | None,
    runtime_enabled: bool,
) -> str:
    payload = {
        "endpoint_id": endpoint.endpoint_id,
        "command": command,
        "cwd": cwd,
        "env": {key: str(value) for key, value in sorted(env.items())},
        "execution_deadline_ms": execution_deadline_ms,
        "runtime_env": runtime_enabled,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _resume_existing_job(
    endpoint: Endpoint,
    record: dict[str, Any],
    *,
    record_path: Any,
    cwd: str,
    started: str,
    start: float,
) -> dict[str, Any]:
    job_id = str(record.get("job_id"))
    remote_dir = str(record.get("remote_dir") or remote_job_dir(endpoint, job_id))
    script = "\n".join(
        [
            "set +e",
            f"status_path={shlex.quote(remote_dir + '/status.json')}",
            "if [ -f \"$status_path\" ]; then cat \"$status_path\"; "
            "else printf '%s\\n' '{\"status\":\"pending\"}'; fi",
        ]
    )
    completed = run_script(endpoint, script, timeout_ms=20000)
    remote_status: dict[str, Any] = {"status": "unknown"}
    if completed.stdout:
        with contextlib.suppress(json.JSONDecodeError):
            remote_status = json.loads(completed.stdout.splitlines()[0])
    status = str(remote_status.get("status") or record.get("phase") or "unknown")
    if completed.timed_out or completed.returncode != 0:
        status = str(record.get("phase") or "unknown")
        remote_status["reconcile"] = "unreachable"
    result = make_result(
        tool="remote.bash",
        target={**endpoint.to_result_target(), "cwd": cwd},
        outcome="success",
        status=status,
        summary=f"Remote background task resumed for job_id {job_id} (current status: {status}).",
        started_at=started,
        duration_ms=_duration_ms(start),
        refs={"job_record": str(record_path)},
        extra={
            "job_id": job_id,
            "resumed": True,
            "job": {
                "job_id": job_id,
                "status_tool": "remote.job_status",
                "tail_tool": "remote.job_tail",
                "stop_tool": "remote.job_stop",
                "remote_dir": remote_dir,
            },
        },
    )
    text = f"RemoteBash resumed on {endpoint.user}@{endpoint.host}:{endpoint.port}\njob_id: {job_id}\nstatus: {status}\nremote_dir: {remote_dir}\n"
    return {"text": text, "result": result}


def start_remote_job(
    endpoint: Endpoint,
    *,
    command: str,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout_ms: int | None = None,
    execution_deadline_ms: int | None = None,
    runtime_env: bool | None = None,
    description: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    started = utc_now_iso()
    start = time.monotonic()
    env = env or {}
    runtime_enabled = endpoint.runtime_env if runtime_env is None else runtime_env
    # ``timeout_ms`` is the legacy name for the execution deadline of a
    # background job; the explicit ``execution_deadline_ms`` wins. Both map to
    # a bounded shell `timeout`, never to an unbounded run.
    deadline_ms = execution_deadline_ms if execution_deadline_ms is not None else timeout_ms
    execution_ms = normalize_execution_deadline_ms(deadline_ms)
    job_id = require_job_id(job_id or new_job_id())
    cwd = cwd or endpoint.effective_cwd
    fingerprint = _job_fingerprint(
        endpoint,
        command=command,
        cwd=cwd,
        env=env,
        execution_deadline_ms=execution_ms,
        runtime_enabled=runtime_enabled,
    )
    local_record = job_record_path(endpoint, job_id)
    found_record = find_job_record(job_id)
    if local_record.exists() or found_record:
        record_path, record = (
            (local_record, json.loads(local_record.read_text(encoding="utf-8")))
            if local_record.exists()
            else found_record  # type: ignore[misc]
        )
        assert record is not None
        if record.get("fingerprint") == fingerprint:
            return _resume_existing_job(endpoint, record, record_path=record_path, cwd=str(record.get("cwd") or cwd), started=started, start=start)
        result = make_result(
            tool="remote.bash",
            target={**endpoint.to_result_target(), "cwd": cwd},
            outcome="blocked",
            status="job_id_conflict",
            summary=(
                f"Remote background task blocked because job_id {job_id} already exists "
                "with different parameters; choose a new job_id or query the existing job."
            ),
            started_at=started,
            duration_ms=_duration_ms(start),
            refs={"job_record": str(record_path)},
            extra={"job_id": job_id},
        )
        return {"text": result["summary"] + "\n", "result": result}
    # Persist the intent record BEFORE any remote action so a lost response
    # can still be reconciled: retrying with the same job_id and parameters
    # resumes this record instead of starting a duplicate.
    intent_record = {
        "schema_version": "remote-dev.job.v1",
        "job_id": job_id,
        "phase": "intent",
        "fingerprint": fingerprint,
        "description": description,
        "target": endpoint.to_result_target(),
        "command_preview": command[:500],
        "cwd": cwd,
        "env_keys": sorted(env),
        "runtime_env": runtime_enabled,
        "remote_dir": remote_job_dir(endpoint, job_id),
        "started_at": started,
        "execution_deadline_ms": execution_ms,
    }
    atomic_write_json(local_record, intent_record)
    validation_script = "\n".join(
        [
            "python3 - <<'REMOTE_DEV_VALIDATE'",
            "import pathlib, sys",
            f"root = pathlib.Path({endpoint.root!r}).resolve()",
            f"cwd = pathlib.Path({cwd!r})",
            "if not cwd.exists():",
            "    print('REMOTE_DEV_CWD_NOT_FOUND', file=sys.stderr)",
            "    raise SystemExit(70)",
            "resolved = cwd.resolve()",
            "if resolved != root and root not in resolved.parents:",
            "    print('REMOTE_DEV_CWD_OUTSIDE_ROOT', file=sys.stderr)",
            "    raise SystemExit(71)",
            "if not cwd.is_dir():",
            "    print('REMOTE_DEV_CWD_NOT_DIRECTORY', file=sys.stderr)",
            "    raise SystemExit(72)",
            "REMOTE_DEV_VALIDATE",
        ]
    )
    validation = run_script(endpoint, validation_script, timeout_ms=20000)
    if validation.timed_out or validation.returncode in {70, 71, 72} or validation.returncode != 0:
        if validation.timed_out:
            outcome = "timeout"
            status = "timeout"
            summary = "Remote background task cwd validation timed out."
        elif validation.returncode == 70:
            outcome = "failed"
            status = "cwd_not_found"
            summary = "Remote background task failed because cwd does not exist."
        elif validation.returncode == 71:
            outcome = "blocked"
            status = "cwd_outside_root"
            summary = "Remote background task blocked because cwd is outside root."
        elif validation.returncode == 72:
            outcome = "failed"
            status = "cwd_not_directory"
            summary = "Remote background task failed because cwd is not a directory."
        else:
            outcome = "failed"
            status = "job_start_failed"
            summary = "Remote background task failed cwd validation."
        result = make_result(
            tool="remote.bash",
            target={**endpoint.to_result_target(), "cwd": cwd},
            outcome=outcome,  # type: ignore[arg-type]
            status=status,
            summary=summary,
            started_at=started,
            duration_ms=_duration_ms(start),
            preview={"stdout": validation.stdout, "stderr": validation.stderr},
            extra={"error": validation.stderr[-4000:], "job_id": job_id},
        )
        return {"text": summary + "\n", "result": result}
    remote_dir = remote_job_dir(endpoint, job_id)
    timeout_prefix = f"timeout {int(execution_ms / 1000)} "
    env_lines = [f"export {require_env_name(key)}={shlex.quote(str(value))}" for key, value in sorted(env.items())]
    runtime_lines = [
        "if [ -f /etc/profile.d/mws-ascend-env.sh ]; then set +u; . /etc/profile.d/mws-ascend-env.sh; set -u; fi"
    ] if runtime_enabled else []
    status_running = shlex.quote('{"status":"running","job_id":"' + job_id + '","started_at":"' + started + '"}')
    runner = "\n".join(
        [
            "#!/usr/bin/env bash",
            "set +e",
            f"JOB_DIR={shlex.quote(remote_dir)}",
            "python3 - <<'REMOTE_DEV_VALIDATE' > \"$JOB_DIR/stdout.log\" 2> \"$JOB_DIR/stderr.log\"",
            "import pathlib, sys",
            f"root = pathlib.Path({endpoint.root!r}).resolve()",
            f"cwd = pathlib.Path({cwd!r})",
            "if not cwd.exists():",
            "    print('REMOTE_DEV_CWD_NOT_FOUND', file=sys.stderr)",
            "    raise SystemExit(70)",
            "resolved = cwd.resolve()",
            "if resolved != root and root not in resolved.parents:",
            "    print('REMOTE_DEV_CWD_OUTSIDE_ROOT', file=sys.stderr)",
            "    raise SystemExit(71)",
            "if not cwd.is_dir():",
            "    print('REMOTE_DEV_CWD_NOT_DIRECTORY', file=sys.stderr)",
            "    raise SystemExit(72)",
            "REMOTE_DEV_VALIDATE",
            "rc=$?",
            "if [ \"$rc\" -eq 0 ]; then",
            *["  " + line for line in runtime_lines],
            f"  cd {shlex.quote(cwd)} || rc=70",
            "fi",
            "if [ \"$rc\" -eq 0 ]; then",
            *["  " + line for line in env_lines],
            f"  printf '%s\\n' {status_running} > \"$JOB_DIR/status.json\"",
            f"  {timeout_prefix}bash -c {shlex.quote(command)} > \"$JOB_DIR/stdout.log\" 2> \"$JOB_DIR/stderr.log\"",
            "  rc=$?",
            "fi",
            "finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)",
            "status=failed",
            "[ \"$rc\" -eq 0 ] && status=succeeded",
            "[ \"$rc\" -eq 70 ] && status=cwd_not_found",
            "[ \"$rc\" -eq 71 ] && status=cwd_outside_root",
            "[ \"$rc\" -eq 72 ] && status=cwd_not_directory",
            "if [ \"$rc\" -eq 124 ] || [ \"$rc\" -eq 137 ]; then status=timeout; fi",
            f"printf '{{\"status\":\"%s\",\"job_id\":\"{job_id}\",\"exit_code\":%s,\"finished_at\":\"%s\"}}\\n' \"$status\" \"$rc\" \"$finished\" > \"$JOB_DIR/status.json\"",
        ]
    )
    script = "\n".join(
        [
            "set -e",
            f"mkdir -p {shlex.quote(remote_dir)}",
            f"cat > {shlex.quote(str(PurePosixPath(remote_dir) / 'run.sh'))} <<'REMOTE_DEV_RUN'",
            runner,
            "REMOTE_DEV_RUN",
            f"chmod +x {shlex.quote(str(PurePosixPath(remote_dir) / 'run.sh'))}",
            f"nohup bash {shlex.quote(str(PurePosixPath(remote_dir) / 'run.sh'))} >/dev/null 2>&1 </dev/null &",
            "pid=$!",
            f"echo \"$pid\" > {shlex.quote(str(PurePosixPath(remote_dir) / 'pid'))}",
            "printf '%s\\n' \"$pid\"",
        ]
    )
    completed = run_script(endpoint, script, timeout_ms=20000)
    if completed.returncode != 0 or completed.timed_out:
        result = make_result(
            tool="remote.bash",
            target=endpoint.to_result_target(),
            outcome="timeout" if completed.timed_out else "failed",
            status="job_start_failed",
            summary="Remote background task failed to start.",
            started_at=started,
            duration_ms=_duration_ms(start),
            preview={"stdout": completed.stdout, "stderr": completed.stderr},
            extra={"error": completed.stderr[-4000:]},
        )
        return {"text": "Remote background task failed to start.\n", "result": result}
    pid = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else None
    record = {
        **intent_record,
        "phase": "started",
        "pid": int(pid) if pid and pid.isdigit() else pid,
    }
    atomic_write_json(local_record, record)
    result = make_result(
        tool="remote.bash",
        target=endpoint.to_result_target(),
        outcome="success",
        status="running",
        summary="Remote background task started.",
        started_at=started,
        duration_ms=_duration_ms(start),
        refs={"job_record": str(local_record)},
        extra={
            "job": {
                "job_id": job_id,
                "status_tool": "remote.job_status",
                "tail_tool": "remote.job_tail",
                "stop_tool": "remote.job_stop",
                "remote_dir": remote_dir,
            }
        },
    )
    text = f"RemoteBash started on {endpoint.user}@{endpoint.host}:{endpoint.port}\njob_id: {job_id}\nremote_dir: {remote_dir}\n"
    return {"text": text, "result": result}


def _endpoint_from_record(record: dict[str, Any]) -> Endpoint:
    target = record.get("target", {})
    return Endpoint(
        host=str(target["host"]),
        port=int(target["port"]),
        user=str(target.get("user") or "root"),
        root=str(target.get("root") or "/"),
        cwd=str(target.get("cwd") or "/mnt/motor-workspace"),
        runtime_env=bool(target.get("runtime_env", True)),
        identity_file=str(target["identity_file"]) if target.get("identity_file") else None,
        connect_timeout_ms=int(target.get("connect_timeout_ms") or 10000),
        kind=str(target.get("kind") or "direct-endpoint"),
        alias=str(target["alias"]) if target.get("alias") else None,
    )


def endpoint_from_job_record(record: dict[str, Any]) -> Endpoint:
    return _endpoint_from_record(record)


def _load_record(endpoint: Endpoint | None, job_id: str) -> tuple[Endpoint, dict[str, Any]]:
    job_id = require_job_id(job_id)
    if endpoint is not None:
        path = job_record_path(endpoint, job_id)
        if not path.exists():
            raise FileNotFoundError(f"unknown remote job id for endpoint: {job_id}")
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        return endpoint, data
    found = find_job_record(job_id)
    if not found:
        raise FileNotFoundError(f"unknown remote job id: {job_id}")
    _, data = found
    return _endpoint_from_record(data), data


def remote_job_status(endpoint: Endpoint | None, *, job_id: str) -> dict[str, Any]:
    endpoint, record = _load_record(endpoint, job_id)
    started = utc_now_iso()
    start = time.monotonic()
    remote_dir = PurePosixPath(record["remote_dir"])
    script = "\n".join(
        [
            "set +e",
            f"status_path={shlex.quote(str(remote_dir / 'status.json'))}",
            f"pid_path={shlex.quote(str(remote_dir / 'pid'))}",
            "if [ -f \"$status_path\" ]; then cat \"$status_path\"; else printf '%s\\n' '{\"status\":\"unknown\"}'; fi",
            "if [ -f \"$pid_path\" ]; then pid=$(cat \"$pid_path\"); if kill -0 \"$pid\" 2>/dev/null; then echo '__PID_ALIVE__=1'; else echo '__PID_ALIVE__=0'; fi; fi",
        ]
    )
    completed = run_script(endpoint, script, timeout_ms=20000)
    status_data: dict[str, Any] = {"status": "unknown"}
    pid_alive = None
    if completed.stdout:
        lines = completed.stdout.splitlines()
        if lines:
            import contextlib
            import json

            with contextlib.suppress(json.JSONDecodeError):
                status_data = json.loads(lines[0])
            for line in lines[1:]:
                if line.startswith("__PID_ALIVE__="):
                    pid_alive = line.endswith("1")
    if status_data.get("status") == "running" and pid_alive is False:
        status_data["status"] = "failed"
        status_data["reason"] = "pid is no longer alive but status was not finalized"
    status = str(status_data.get("status") or "unknown")
    outcome = "success" if completed.returncode == 0 else "failed"
    result = make_result(
        tool="remote.job_status",
        target=endpoint.to_result_target(),
        outcome=outcome,
        status=status,
        summary=f"Remote job {job_id} is {status}.",
        started_at=started,
        duration_ms=_duration_ms(start),
        preview={"stdout": completed.stdout, "stderr": completed.stderr},
        extra={"job": {**record, "remote_status": status_data, "pid_alive": pid_alive}},
    )
    return {"text": f"Remote job {job_id}: {status}\n", "result": result}


def remote_job_tail(
    endpoint: Endpoint | None,
    *,
    job_id: str,
    lines: int = 80,
    stream: str = "both",
    incremental: bool = False,
) -> dict[str, Any]:
    endpoint, record = _load_record(endpoint, job_id)
    started = utc_now_iso()
    start = time.monotonic()
    remote_dir = PurePosixPath(record["remote_dir"])
    warnings = []
    if incremental:
        return _remote_job_tail_incremental(endpoint, record, job_id=job_id, stream=stream, started=started, start=start)
    if lines > MAX_JOB_TAIL_LINES:
        warnings.append(f"lines clamped from {lines} to {MAX_JOB_TAIL_LINES}")
        lines = MAX_JOB_TAIL_LINES
    if lines < 1:
        lines = 1
    commands: list[str] = []
    if stream in {"stdout", "both"}:
        commands.append(f"echo __STDOUT__; tail -n {int(lines)} {shlex.quote(str(remote_dir / 'stdout.log'))} 2>/dev/null | head -c {MAX_TEXT_CHARS} || true")
    if stream in {"stderr", "both"}:
        commands.append(f"echo __STDERR__; tail -n {int(lines)} {shlex.quote(str(remote_dir / 'stderr.log'))} 2>/dev/null | head -c {MAX_TEXT_CHARS} || true")
    completed = run_script(endpoint, "\n".join(commands), timeout_ms=20000)
    text = compact_text(completed.stdout)
    result = make_result(
        tool="remote.job_tail",
        target=endpoint.to_result_target(),
        outcome="success" if completed.returncode == 0 else "failed",
        status="ok" if completed.returncode == 0 else "failed",
        summary=f"Remote job tail for {job_id}.",
        started_at=started,
        duration_ms=_duration_ms(start),
        preview={"tail": text, "stderr": completed.stderr},
        warnings=warnings,
        extra={"job_id": job_id, "lines": lines, "incremental": False},
    )
    return {"text": text, "result": result}


def _remote_job_tail_incremental(
    endpoint: Endpoint,
    record: dict[str, Any],
    *,
    job_id: str,
    stream: str,
    started: str,
    start: float,
) -> dict[str, Any]:
    remote_dir = PurePosixPath(record["remote_dir"])
    streams = ["stdout", "stderr"] if stream == "both" else [stream]
    reader = "\n".join(
        [
            "import json, os, sys",
            "directory = sys.argv[1]",
            f"streams = {json.dumps(streams)}",
            f"max_bytes = {JOB_TAIL_CURSOR_MAX_BYTES}",
            "cursor_path = os.path.join(directory, 'cursor.json')",
            "try:",
            "    cursor = json.load(open(cursor_path))",
            "except Exception:",
            "    cursor = {}",
            "out = {}",
            "rotated = []",
            "for name in streams:",
            "    log_path = os.path.join(directory, name + '.log')",
            "    try:",
            "        size = os.path.getsize(log_path)",
            "    except OSError:",
            "        out[name] = {'data': '', 'offset': 0}",
            "        continue",
            "    offset = int(cursor.get(name, 0))",
            "    if offset > size:",
            "        rotated.append(name)",
            "        offset = 0",
            "    with open(log_path, 'rb') as fh:",
            "        fh.seek(offset)",
            "        data = fh.read(max_bytes)",
            "        new_offset = offset + len(data)",
            "    out[name] = {'data': data.decode('utf-8', errors='replace'), 'offset': new_offset, 'size': size}",
            "cursor.update({name: out[name]['offset'] for name in out})",
            "tmp = cursor_path + '.tmp'",
            "with open(tmp, 'w') as fh:",
            "    json.dump(cursor, fh)",
            "os.replace(tmp, cursor_path)",
            "print(json.dumps({'streams': out, 'rotated': rotated}))",
        ]
    )
    script = "\n".join(
        [
            "set +e",
            f"python3 - {shlex.quote(str(remote_dir))} <<'REMOTE_DEV_TAIL'",
            reader,
            "REMOTE_DEV_TAIL",
        ]
    )
    completed = run_script(endpoint, script, timeout_ms=20000)
    streams_out: dict[str, Any] = {}
    rotated: list[str] = []
    if completed.returncode == 0 and completed.stdout.strip():
        with contextlib.suppress(json.JSONDecodeError):
            payload = json.loads(completed.stdout.strip().splitlines()[-1])
            streams_out = payload.get("streams", {})
            rotated = payload.get("rotated", [])
    warnings: list[str] = []
    if rotated:
        warnings.append(f"log truncated or rotated for streams: {', '.join(rotated)}; cursor reset to 0")
    text_parts = []
    for name in streams:
        info = streams_out.get(name, {})
        data = info.get("data", "")
        text_parts.append(f"__{name.upper()}__ (offset={info.get('offset', 0)})\n{data}")
    if rotated:
        text_parts.insert(0, f"<cursor reset: log truncated/rotated for {', '.join(rotated)}>")
    text = compact_text("\n".join(text_parts))
    result = make_result(
        tool="remote.job_tail",
        target=endpoint.to_result_target(),
        outcome="success" if completed.returncode == 0 else "failed",
        status="ok" if completed.returncode == 0 else "failed",
        summary=f"Remote job incremental tail for {job_id}.",
        started_at=started,
        duration_ms=_duration_ms(start),
        preview={"tail": text, "stderr": completed.stderr},
        warnings=warnings,
        extra={
            "job_id": job_id,
            "incremental": True,
            "rotated": rotated,
            "cursors": {name: info.get("offset", 0) for name, info in streams_out.items()},
        },
    )
    return {"text": text, "result": result}


def remote_job_stop(endpoint: Endpoint | None, *, job_id: str, force: bool = False) -> dict[str, Any]:
    endpoint, record = _load_record(endpoint, job_id)
    started = utc_now_iso()
    start = time.monotonic()
    remote_dir = PurePosixPath(record["remote_dir"])
    sig = "-9" if force else "-15"
    script = "\n".join(
        [
            "set +e",
            f"pid_path={shlex.quote(str(remote_dir / 'pid'))}",
            "if [ ! -f \"$pid_path\" ]; then echo missing; exit 3; fi",
            "pid=$(cat \"$pid_path\")",
            f"kill {sig} \"$pid\" 2>/dev/null || true",
            "sleep 1",
            "alive=0",
            "kill -0 \"$pid\" 2>/dev/null && alive=1",
            f"if [ \"$alive\" -eq 0 ]; then printf '{{\"status\":\"cancelled\",\"job_id\":\"{job_id}\",\"exit_code\":null,\"finished_at\":\"%s\"}}\\n' \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\" > {shlex.quote(str(remote_dir / 'status.json'))}; fi",
            "echo \"$alive\"",
        ]
    )
    completed = run_script(endpoint, script, timeout_ms=20000)
    alive = completed.stdout.strip().splitlines()[-1:] == ["1"]
    status = "failed" if alive else "cancelled"
    result = make_result(
        tool="remote.job_stop",
        target=endpoint.to_result_target(),
        outcome="failed" if alive or completed.returncode not in {0, None} else "cancelled",
        status=status,
        summary=f"Remote job {job_id} {status}.",
        started_at=started,
        duration_ms=_duration_ms(start),
        preview={"stdout": completed.stdout, "stderr": completed.stderr},
        extra={"job_id": job_id},
    )
    return {"text": f"Remote job {job_id}: {status}\n", "result": result}
