#!/usr/bin/env python3
"""Minimal untracked endpoint inventory for retained executors."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mws_validate import (
    normalize_mount_root,
    require_hostname,
    require_safe_id,
    validate_remote_workspace_in_mount,
)
from repo_paths import REPO_ROOT

STATE_DIRNAME = ".motor-workspace-local"
INVENTORY_FILENAME = "machine-inventory.json"
INVENTORY_SCHEMA_VERSION = 1
PARITY_BACKEND_CHOICES = ("shared-hostpath", "node-local-hostpath")
EXECUTOR_CHOICES = ("ssh", "native")

ROOT = REPO_ROOT
STATE_DIR = ROOT / STATE_DIRNAME
LOCAL_ROOT = STATE_DIR
INVENTORY_PATH = STATE_DIR / INVENTORY_FILENAME
INVENTORY_LOCK_PATH = STATE_DIR / f"{INVENTORY_FILENAME}.lock"


class WorkspaceStateError(RuntimeError):
    """Raised for deterministic user-facing local-state failures."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_state_dir(path: Path = STATE_DIR) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_json(path: Path) -> Any:
    from mws_state import load_json

    return load_json(path)


def _save_json(path: Path, data: Any) -> None:
    from mws_state import atomic_write_json

    atomic_write_json(path, data)


def _empty_inventory() -> dict[str, Any]:
    return {"schema_version": INVENTORY_SCHEMA_VERSION, "machines": {}}


def validate_machine_record(record: Any, *, where: str = "machine") -> dict[str, Any]:
    if not isinstance(record, dict):
        raise WorkspaceStateError(f"{where} must be an object")

    alias = require_safe_id(str(record.get("alias", "")), label=f"{where}.alias")
    host = require_hostname(str(record.get("host", "")), label=f"{where}.host")
    user = str(record.get("user", "root")).strip()
    if not user:
        raise WorkspaceStateError(f"{where}.user must be non-empty")
    port = record.get("port", 22)
    if not isinstance(port, int) or port <= 0:
        raise WorkspaceStateError(f"{where}.port must be a positive integer")

    mount_root = normalize_mount_root(record.get("mount_root"))
    remote_workspace_root = validate_remote_workspace_in_mount(
        mount_root,
        str(record.get("remote_workspace_root") or f"{mount_root}/motor-workspace"),
    )
    parity_backend = record.get("parity_backend", "shared-hostpath")
    if parity_backend not in PARITY_BACKEND_CHOICES:
        raise WorkspaceStateError(
            f"{where}.parity_backend must be one of: {', '.join(PARITY_BACKEND_CHOICES)}"
        )
    executor = record.get("executor", "ssh")
    if executor not in EXECUTOR_CHOICES:
        raise WorkspaceStateError(
            f"{where}.executor must be one of: {', '.join(EXECUTOR_CHOICES)}"
        )

    source_dirs = record.get("source_dirs") or {}
    if not isinstance(source_dirs, dict):
        raise WorkspaceStateError(f"{where}.source_dirs must be an object")
    normalized_sources: dict[str, str] = {}
    for key in ("motor", "vllm", "vllm_ascend", "python_overlay"):
        if source_dirs.get(key):
            normalized_sources[key] = validate_remote_workspace_in_mount(
                mount_root,
                str(source_dirs[key]),
                label=f"{where}.source_dirs.{key}",
            )

    candidate_nodes = record.get("candidate_nodes") or []
    if not isinstance(candidate_nodes, list) or not all(
        isinstance(item, str) and item.strip() for item in candidate_nodes
    ):
        raise WorkspaceStateError(f"{where}.candidate_nodes must be non-empty strings")

    normalized = {
        "alias": alias,
        "host": host,
        "port": port,
        "user": user,
        "mount_root": mount_root,
        "remote_workspace_root": remote_workspace_root,
        "kube_context": str(record.get("kube_context") or "").strip(),
        "parity_backend": parity_backend,
        "executor": executor,
        "candidate_nodes": [item.strip() for item in candidate_nodes],
    }
    if normalized_sources:
        normalized["source_dirs"] = normalized_sources
    return normalized


def _validate_inventory_payload(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise WorkspaceStateError(f"{INVENTORY_PATH} must contain a JSON object")
    if data.get("schema_version") != INVENTORY_SCHEMA_VERSION:
        raise WorkspaceStateError(
            f"unsupported inventory schema_version: {data.get('schema_version')!r}"
        )
    machines = data.get("machines")
    if not isinstance(machines, dict):
        raise WorkspaceStateError("inventory machines must be an object keyed by alias")
    validated: dict[str, dict[str, Any]] = {}
    for alias, record in machines.items():
        normalized = validate_machine_record(record, where=f"machines[{alias!r}]")
        if normalized["alias"] != alias:
            raise WorkspaceStateError(f"machine key {alias!r} does not match its alias")
        validated[alias] = normalized
    return {**data, "machines": validated}


def load_inventory(*, path: Path | None = None) -> dict[str, Any]:
    target = path or INVENTORY_PATH
    if not target.exists():
        return _empty_inventory()
    return _validate_inventory_payload(_load_json(target))


def save_inventory(data: dict[str, Any], *, path: Path | None = None) -> None:
    payload = _validate_inventory_payload(
        {**data, "schema_version": INVENTORY_SCHEMA_VERSION, "updated_at": utc_now_iso()}
    )
    _save_json(path or INVENTORY_PATH, payload)


def get_machine(alias: str, *, path: Path | None = None) -> dict[str, Any]:
    normalized_alias = require_safe_id(alias, label="alias")
    machines = load_inventory(path=path)["machines"]
    if normalized_alias not in machines:
        raise WorkspaceStateError(f"machine not found: {normalized_alias}")
    return machines[normalized_alias]


def redact_secrets(payload: Any) -> Any:
    secret_keys = {"password", "token", "kubeconfig", "secret", "private_key"}
    if isinstance(payload, dict):
        return {
            key: "<redacted>"
            if any(part in str(key).lower() for part in secret_keys)
            else redact_secrets(value)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [redact_secrets(item) for item in payload]
    return payload
