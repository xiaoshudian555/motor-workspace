#!/usr/bin/env python3
"""Run-scoped local evidence directories."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from mws_local_state import LOCAL_ROOT, ROOT, utc_now_iso
from mws_state import atomic_write_json, load_json

PARITY_RUNS_DIR = LOCAL_ROOT / "parity-runs"
DEPLOY_RUNS_DIR = LOCAL_ROOT / "deploy-runs"
VALIDATION_RUNS_DIR = LOCAL_ROOT / "validation-runs"


def new_run_id(prefix: str) -> str:
    stamp = utc_now_iso().replace(":", "").replace("-", "")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def parity_run_dir(run_id: str) -> Path:
    return PARITY_RUNS_DIR / run_id


def deploy_run_dir(run_id: str) -> Path:
    return DEPLOY_RUNS_DIR / run_id


def validation_run_dir(run_id: str) -> Path:
    return VALIDATION_RUNS_DIR / run_id


def write_parity_run(run_id: str, payload: dict[str, Any]) -> Path:
    run_dir = parity_run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    record = dict(payload)
    record["run_id"] = run_id
    record.setdefault("created_at", utc_now_iso())
    path = run_dir / "run.json"
    atomic_write_json(path, record)
    if "manifest" in record:
        atomic_write_json(run_dir / "manifest.json", record["manifest"])
    return path


def write_deploy_run(run_id: str, payload: dict[str, Any]) -> Path:
    run_dir = deploy_run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    record = dict(payload)
    record["run_id"] = run_id
    record.setdefault("created_at", utc_now_iso())
    path = run_dir / "run.json"
    atomic_write_json(path, record)
    return path


def load_deploy_run(run_id: str) -> dict[str, Any]:
    path = deploy_run_dir(run_id) / "run.json"
    data = load_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain an object")
    return data


def relative_repo(path: Path) -> str:
    return str(path.relative_to(ROOT))
