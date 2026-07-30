#!/usr/bin/env python3
"""Shared local JSON state helpers."""

from __future__ import annotations

import contextlib
import json
import os
import socket
import tempfile
import time
from pathlib import Path
from typing import Any

from mws_local_state import WorkspaceStateError, ensure_state_dir, utc_now_iso

DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0
DEFAULT_LOCK_POLL_SECONDS = 0.1
DEFAULT_STALE_LOCK_SECONDS = 60 * 60 * 6


def atomic_write_json(path: Path, data: Any) -> None:
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


def load_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkspaceStateError(f"invalid JSON in {path}: {exc}") from exc


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
                raise WorkspaceStateError(f"timed out waiting for lock {path}")
            time.sleep(poll_seconds)
    try:
        yield path
    finally:
        if fd is not None:
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
