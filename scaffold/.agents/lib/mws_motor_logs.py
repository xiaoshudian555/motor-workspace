#!/usr/bin/env python3
"""Motor upstream ``--auto_log_collect`` session discovery and collection."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

from mws_execution import ExecutionAdapter, execution_adapter_for_machine
from mws_local_state import WorkspaceStateError
from mws_machine_target import build_fixed_source_paths
from mws_transport import shell_quote
from mws_validate import validate_remote_posix_path


LOG_COLLECTION_RELATIVE_ROOT = "examples/deployer/log_collect/log"


def remote_log_collection_root(machine: dict[str, Any]) -> str:
    motor_source = str(build_fixed_source_paths(machine)["motor_source"]).rstrip("/")
    return validate_remote_posix_path(
        f"{motor_source}/{LOG_COLLECTION_RELATIVE_ROOT}",
        label="remote_log_collection_root",
    )


def snapshot_remote_log_sessions(
    machine: dict[str, Any],
    *,
    adapter: ExecutionAdapter | None = None,
) -> dict[str, Any]:
    """Return timestamp session directories currently present under Motor's log root."""
    run = adapter or execution_adapter_for_machine(machine)
    root = remote_log_collection_root(machine)
    command = "\n".join(
        [
            "set -eo pipefail",
            f"root={shell_quote(root)}",
            'if [ ! -d "$root" ]; then exit 0; fi',
            'find "$root" -mindepth 1 -maxdepth 1 -type d -printf "%f\\n" | sort',
        ]
    )
    result = run.run(command)
    if result.returncode:
        return {
            "status": "error",
            "remote_log_root": root,
            "session_dirs": [],
            "error": result.stderr.strip() or result.stdout.strip() or "log session scan failed",
        }
    sessions = []
    for name in result.stdout.splitlines():
        normalized = name.strip()
        if not normalized or PurePosixPath(normalized).name != normalized:
            continue
        sessions.append(f"{root}/{normalized}")
    return {
        "status": "ok",
        "remote_log_root": root,
        "session_dirs": sorted(set(sessions)),
    }


def correlate_new_log_sessions(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    """Build run evidence for sessions created by one deploy invocation."""
    root = str(after.get("remote_log_root") or before.get("remote_log_root") or "")
    before_dirs = set(before.get("session_dirs") or [])
    after_dirs = set(after.get("session_dirs") or [])
    new_dirs = sorted(after_dirs - before_dirs)
    errors = [
        str(item.get("error"))
        for item in (before, after)
        if item.get("status") == "error" and item.get("error")
    ]
    return {
        "requested": True,
        "status": "recorded" if new_dirs else "unresolved",
        "remote_log_root": root,
        "session_dirs": new_dirs,
        "errors": errors,
    }


def snapshot_local_log_sessions(deployer_root: Path) -> dict[str, Any]:
    root = (deployer_root / "log_collect" / "log").resolve()
    sessions = sorted(str(item.resolve()) for item in root.iterdir() if item.is_dir()) if root.is_dir() else []
    return {"status": "ok", "remote_log_root": str(root), "session_dirs": sessions}


def _validated_session_dir(machine: dict[str, Any], session_dir: str) -> tuple[str, str]:
    root = PurePosixPath(remote_log_collection_root(machine))
    session = PurePosixPath(validate_remote_posix_path(session_dir, label="remote_log_session_dir"))
    if session.parent != root:
        raise WorkspaceStateError(f"log session is outside Motor log root: {session}")
    return str(session), session.name


def collect_remote_log_sessions(
    machine: dict[str, Any],
    session_dirs: list[str],
    destination: Path,
    *,
    adapter: ExecutionAdapter | None = None,
) -> dict[str, Any]:
    """Copy recorded Motor log sessions into one local diagnosis run."""
    run = adapter or execution_adapter_for_machine(machine)
    files: list[dict[str, Any]] = []
    errors: list[str] = []
    for session_ref in session_dirs:
        try:
            session_dir, session_name = _validated_session_dir(machine, str(session_ref))
            list_command = "\n".join(
                [
                    "set -eo pipefail",
                    f"root={shell_quote(session_dir)}",
                    'if [ ! -d "$root" ]; then exit 0; fi',
                    'find "$root" -type f -printf "%P\\n" | sort',
                ]
            )
            listed = run.run(list_command)
            if listed.returncode:
                raise WorkspaceStateError(
                    listed.stderr.strip() or listed.stdout.strip() or "log artifact listing failed"
                )
            for relative in sorted({line.strip() for line in listed.stdout.splitlines() if line.strip()}):
                rel = PurePosixPath(relative)
                if rel.is_absolute() or ".." in rel.parts:
                    raise WorkspaceStateError(f"unsafe log artifact path: {relative}")
                remote_path = f"{session_dir}/{relative}"
                data = run.read_bytes(remote_path)
                actual_digest = hashlib.sha256(data).hexdigest()
                local_path = destination / session_name / Path(*rel.parts)
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(data)
                files.append(
                    {
                        "remote_path": remote_path,
                        "local_path": str(local_path),
                        "sha256": actual_digest,
                        "bytes": len(data),
                        "source_mutable": True,
                    }
                )
        except (OSError, WorkspaceStateError) as exc:
            errors.append(f"{session_ref}: {exc}")
    return {
        "status": "ok" if files and not errors else "partial" if files else "unavailable",
        "session_dirs": list(session_dirs),
        "files": files,
        "errors": errors,
    }


PYMOTOR_CONTROLLER_RECOVERY_PATTERNS = (
    "precision-auto-recover",
    "PrecisionReporter: threshold reached",
    "Reporting alarm to controller",
    "Recovery: separate_instance",
    "terminate_instance_for_recovery",
)


def recommend_pymotor_diagnosis_skills(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Route collected logs only to migrated PyMotor diagnosis skills."""
    matched: dict[str, set[str]] = {}
    for item in files:
        local_path = Path(str(item["local_path"]))
        try:
            content = local_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits = {pattern for pattern in PYMOTOR_CONTROLLER_RECOVERY_PATTERNS if pattern in content}
        if hits:
            matched.setdefault("motor-diagnosis-controller-recovery-terminate", set()).update(hits)
    return [
        {"skill": name, "matched_patterns": sorted(patterns)}
        for name, patterns in sorted(matched.items())
    ]
