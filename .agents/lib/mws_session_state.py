#!/usr/bin/env python3
"""Session state and lease helpers for motor-workspace."""

from __future__ import annotations

import contextlib
import json
import os
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mws_local_state import ROOT, STATE_DIR, WorkspaceStateError, ensure_state_dir, utc_now_iso
from mws_session_id import normalize_session_id, require_session_id
from mws_validate import normalize_mount_root

SESSION_SCHEMA_VERSION = 1
INDEX_SCHEMA_VERSION = 1
SESSION_ROOT = STATE_DIR / "sessions"
SESSION_INDEX_PATH = SESSION_ROOT / "index.json"
SESSION_LOCK_DIR = SESSION_ROOT / "locks"
DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0
DEFAULT_LOCK_POLL_SECONDS = 0.1
DEFAULT_STALE_LOCK_SECONDS = 60 * 60 * 6


class SessionStateError(WorkspaceStateError):
    pass


@dataclass(frozen=True)
class SessionLookup:
    session: dict[str, Any]
    session_file: Path
    state_repo_root: Path


def _atomic_write_json(path: Path, data: Any) -> None:
    ensure_state_dir(path.parent)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_name)


def _load_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SessionStateError(f"invalid JSON in {path}: {exc}") from exc


@contextlib.contextmanager
def file_lock(
    path: Path,
    *,
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
    stale_after_seconds: float = DEFAULT_STALE_LOCK_SECONDS,
):
    ensure_state_dir(path.parent)
    deadline = time.monotonic() + timeout_seconds
    owner = {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "created_at": utc_now_iso(),
    }
    fd: int | None = None
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, json.dumps(owner, ensure_ascii=False).encode("utf-8"))
            break
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age >= stale_after_seconds:
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()
                continue
            if time.monotonic() >= deadline:
                raise SessionStateError(f"timed out waiting for session lock {path}")
            time.sleep(poll_seconds)
    try:
        yield path
    finally:
        if fd is not None:
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def session_dir(session_id: str, repo_root: Path = ROOT) -> Path:
    normalized = require_session_id(session_id)
    return repo_root / ".motor-workspace-local" / "sessions" / normalized


def session_file_path(session_id: str, repo_root: Path = ROOT) -> Path:
    return session_dir(session_id, repo_root) / "session.json"


def session_remote_root(session: dict[str, Any]) -> str:
    return str(session["remote_session_root"])


def build_session_paths(
    *,
    workspace_id: str,
    session_id: str,
    mount_root: str,
) -> dict[str, str]:
    mount = normalize_mount_root(mount_root)
    session_root = f"{mount}/motor-workspace/{workspace_id}/{session_id}"
    return {
        "mount_root": mount,
        "remote_session_root": session_root,
        "motor_source": f"{session_root}/motor",
        "vllm_source": f"{session_root}/vllm",
        "vllm_ascend_source": f"{session_root}/vllm-ascend",
        "python_overlay": f"{session_root}/python-overlay",
    }


def pythonpath_for_session(session: dict[str, Any]) -> str:
    paths = session.get("paths", {})
    ordered = [
        paths.get("motor_source"),
        paths.get("vllm_source"),
        paths.get("vllm_ascend_source"),
        paths.get("python_overlay"),
    ]
    return ":".join(p for p in ordered if p)


def load_session(session_id: str, repo_root: Path = ROOT) -> SessionLookup:
    path = session_file_path(session_id, repo_root)
    data = _load_json(path)
    if not isinstance(data, dict):
        raise SessionStateError(f"{path} must contain a JSON object")
    return SessionLookup(session=data, session_file=path, state_repo_root=repo_root)


def load_session_index(repo_root: Path = ROOT) -> dict[str, Any]:
    path = repo_root / ".motor-workspace-local" / "sessions" / "index.json"
    data = _load_json(path, default={"schema_version": INDEX_SCHEMA_VERSION, "sessions": {}})
    if not isinstance(data, dict):
        raise SessionStateError(f"{path} must contain a JSON object")
    data.setdefault("sessions", {})
    return data


def save_session_index(index: dict[str, Any], repo_root: Path = ROOT) -> None:
    path = repo_root / ".motor-workspace-local" / "sessions" / "index.json"
    index = dict(index)
    index["schema_version"] = INDEX_SCHEMA_VERSION
    index["updated_at"] = utc_now_iso()
    _atomic_write_json(path, index)


def upsert_session_record(session: dict[str, Any], repo_root: Path = ROOT) -> Path:
    session_id = require_session_id(session["session_id"])
    path = session_file_path(session_id, repo_root)
    ensure_state_dir(path.parent)
    session = dict(session)
    session["schema_version"] = SESSION_SCHEMA_VERSION
    session["updated_at"] = utc_now_iso()
    _atomic_write_json(path, session)
    index = load_session_index(repo_root)
    index["sessions"][session_id] = {
        "session_id": session_id,
        "machine": session.get("machine"),
        "namespace": session.get("namespace"),
        "job_id": session.get("job_id"),
        "remote_session_root": session.get("remote_session_root"),
        "updated_at": session["updated_at"],
    }
    save_session_index(index, repo_root)
    return path
