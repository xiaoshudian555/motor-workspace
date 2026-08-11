#!/usr/bin/env python3
"""Local evidence for the two retained executable workflows."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Literal

from mws_local_state import LOCAL_ROOT, WorkspaceStateError, utc_now_iso
from mws_state import atomic_write_json, load_json

RunKind = Literal["parity-complete", "motor-wheel-build"]

RUN_KINDS: frozenset[str] = frozenset({"parity-complete", "motor-wheel-build"})
RUN_DIRS: dict[RunKind, str] = {
    "parity-complete": "parity-runs",
    "motor-wheel-build": "wheel-build-runs",
}


def new_run_id(prefix: str) -> str:
    stamp = utc_now_iso().replace(":", "").replace("-", "")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def run_dir_for_kind(kind: RunKind, run_id: str) -> Path:
    if kind not in RUN_KINDS:
        raise WorkspaceStateError(f"unknown run kind: {kind}")
    return LOCAL_ROOT / RUN_DIRS[kind] / run_id


def run_record_path(kind: RunKind, run_id: str) -> Path:
    return run_dir_for_kind(kind, run_id) / "run.json"


def parity_run_dir(run_id: str) -> Path:
    return run_dir_for_kind("parity-complete", run_id)


def load_run(kind: RunKind, run_id: str) -> dict[str, Any]:
    path = run_record_path(kind, run_id)
    if not path.exists():
        raise WorkspaceStateError(f"missing run record: {path}")
    data = load_json(path)
    if not isinstance(data, dict):
        raise WorkspaceStateError(f"{path} must contain an object")
    if str(data.get("kind", kind)) != kind:
        raise WorkspaceStateError(f"run {run_id} kind mismatch: expected {kind}")
    if str(data.get("run_id", run_id)) != run_id:
        raise WorkspaceStateError(f"run_id mismatch for {path}")
    if data.get("status") != "ready":
        raise WorkspaceStateError(f"run {run_id} is not ready")
    return data


def write_run(
    kind: RunKind,
    run_id: str,
    payload: dict[str, Any],
    *,
    immutable: bool = True,
) -> Path:
    if kind not in RUN_KINDS:
        raise WorkspaceStateError(f"unknown run kind: {kind}")
    path = run_record_path(kind, run_id)
    if immutable and path.exists():
        raise WorkspaceStateError(f"run already exists and is immutable: {path}")
    record = {**payload, "kind": kind, "run_id": run_id}
    record.setdefault("created_at", utc_now_iso())
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, record)
    return path


def write_parity_run(run_id: str, payload: dict[str, Any]) -> Path:
    record = dict(payload)
    if record.get("parity_complete") and record.get("status") in {"ok", "ready"}:
        record["status"] = "ready"
    path = write_run("parity-complete", run_id, record)
    if "manifest" in record:
        atomic_write_json(parity_run_dir(run_id) / "manifest.json", record["manifest"])
    return path
