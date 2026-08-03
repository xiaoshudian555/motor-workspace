#!/usr/bin/env python3
"""Local untracked state helpers for motor-workspace."""

from __future__ import annotations

import contextlib
import os
import re
import secrets
import string
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_DIRNAME = ".motor-workspace-local"
PROFILE_FILENAME = "machine-profile.json"
INVENTORY_FILENAME = "machine-inventory.json"
PROFILE_SCHEMA_VERSION = 1
INVENTORY_SCHEMA_VERSION = 1
PARITY_BACKEND_CHOICES = ("shared-hostpath", "node-local-hostpath")
EXECUTOR_CHOICES = ("ssh", "native")
WORKSPACE_ID_PREFIX = "mws-"
USERNAME_PATTERN = re.compile(r"^[a-z0-9]{3,32}$")
RANDOM_ALPHABET = string.digits
DEFAULT_RANDOM_PREFIX = "agent"
DEFAULT_RANDOM_SUFFIX_LENGTH = 5
INVENTORY_LOCK_TIMEOUT_SECONDS = 15.0
INVENTORY_LOCK_POLL_SECONDS = 0.05

from mws_validate import (
    ValidationError,
    normalize_mount_root,
    require_hostname,
    require_safe_id,
    validate_remote_workspace_in_mount,
)
from repo_paths import REPO_ROOT, SCAFFOLD_ROOT

ROOT = REPO_ROOT
SCAFFOLD = SCAFFOLD_ROOT
STATE_DIR = ROOT / STATE_DIRNAME
LOCAL_ROOT = STATE_DIR
PROFILE_PATH = STATE_DIR / PROFILE_FILENAME
INVENTORY_PATH = STATE_DIR / INVENTORY_FILENAME
INVENTORY_LOCK_PATH = STATE_DIR / f"{INVENTORY_FILENAME}.lock"

LEGACY_INVENTORY_FILENAME = ".machine-inventory.json"
LEGACY_INVENTORY_PATH = ROOT / LEGACY_INVENTORY_FILENAME


class WorkspaceStateError(RuntimeError):
    """Raised for deterministic user-facing local-state failures."""


def utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def ensure_state_dir(path: Path = STATE_DIR) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def same_path(left: Path, right: Path) -> bool:
    return left.expanduser().resolve() == right.expanduser().resolve()


def normalize_machine_username(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise WorkspaceStateError("machine username must be non-empty")
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise WorkspaceStateError(
            "machine username must be 3-32 characters of English letters and digits only"
        )
    return normalized


validate_machine_username = normalize_machine_username


def generate_machine_username(
    *,
    prefix: str = DEFAULT_RANDOM_PREFIX,
    suffix_length: int = DEFAULT_RANDOM_SUFFIX_LENGTH,
    existing: set[str] | None = None,
) -> str:
    cleaned_prefix = "".join(ch for ch in prefix.lower() if ch.isalnum()) or "agent"
    cleaned_prefix = cleaned_prefix[: max(1, 32 - suffix_length)]
    existing = existing or set()
    for _ in range(128):
        suffix = "".join(secrets.choice(RANDOM_ALPHABET) for _ in range(suffix_length))
        candidate = f"{cleaned_prefix}{suffix}"
        if candidate not in existing and USERNAME_PATTERN.fullmatch(candidate):
            return candidate
    raise WorkspaceStateError("unable to generate a unique machine username")


def default_workspace_id() -> str:
    return f"{WORKSPACE_ID_PREFIX}{secrets.token_hex(4)}"


def _load_json(path: Path) -> Any:
    from mws_state import load_json

    return load_json(path)


def _save_json(path: Path, data: Any) -> None:
    from mws_state import atomic_write_json

    atomic_write_json(path, data)


def ensure_workspace_id(*, persist: bool = True) -> str:
    profile = load_profile(persist_missing=False)
    workspace_id = profile.get("workspace_id")
    if not workspace_id:
        workspace_id = default_workspace_id()
        profile["workspace_id"] = workspace_id
        profile.setdefault("created_at", utc_now_iso())
        if persist:
            save_profile(profile)
    return str(workspace_id)


def load_profile(*, persist_missing: bool = True) -> dict[str, Any]:
    if not PROFILE_PATH.exists():
        data = {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "workspace_id": default_workspace_id(),
            "created_at": utc_now_iso(),
        }
        if persist_missing:
            save_profile(data)
        return data
    data = _load_json(PROFILE_PATH)
    if not isinstance(data, dict):
        raise WorkspaceStateError(f"{PROFILE_PATH} must contain a JSON object")
    if "workspace_id" not in data:
        data["workspace_id"] = default_workspace_id()
        if persist_missing:
            save_profile(data)
    return data


def save_profile(data: dict[str, Any]) -> None:
    data = dict(data)
    data["schema_version"] = PROFILE_SCHEMA_VERSION
    data["updated_at"] = utc_now_iso()
    _save_json(PROFILE_PATH, data)


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
    remote_workspace_root = record.get("remote_workspace_root")
    if remote_workspace_root:
        remote_workspace_root = validate_remote_workspace_in_mount(
            mount_root,
            str(remote_workspace_root),
        )
    else:
        remote_workspace_root = f"{mount_root}/motor-workspace"

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

    source_dirs = record.get("source_dirs")
    if source_dirs is not None and not isinstance(source_dirs, dict):
        raise WorkspaceStateError(f"{where}.source_dirs must be an object when present")
    if source_dirs:
        for key in ("motor", "vllm", "vllm_ascend", "python_overlay"):
            value = source_dirs.get(key)
            if value is None:
                continue
            try:
                validate_remote_workspace_in_mount(mount_root, str(value), label=f"{where}.source_dirs.{key}")
            except Exception as exc:  # noqa: BLE001
                raise WorkspaceStateError(str(exc)) from exc

    candidate_nodes = record.get("candidate_nodes", [])
    if candidate_nodes is None:
        candidate_nodes = []
    if not isinstance(candidate_nodes, list) or not all(
        isinstance(item, str) and item.strip() for item in candidate_nodes
    ):
        raise WorkspaceStateError(f"{where}.candidate_nodes must be a list of non-empty strings")

    kube_context = record.get("kube_context")
    if kube_context is not None and not isinstance(kube_context, str):
        raise WorkspaceStateError(f"{where}.kube_context must be a string when present")
    if isinstance(kube_context, str):
        kube_context = kube_context.strip()

    normalized = {
        "alias": alias,
        "host": host,
        "port": port,
        "user": user,
        "mount_root": mount_root,
        "remote_workspace_root": remote_workspace_root,
        "kube_context": kube_context or "",
        "parity_backend": parity_backend,
        "executor": executor,
        "candidate_nodes": [item.strip() for item in candidate_nodes],
    }
    if source_dirs:
        normalized["source_dirs"] = {
            key: source_dirs[key] for key in ("motor", "vllm", "vllm_ascend", "python_overlay") if source_dirs.get(key)
        }
    for optional_key in ("created_at", "last_verified_at", "last_verify_errors", "last_repaired_at"):
        if optional_key in record:
            normalized[optional_key] = record[optional_key]
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
            raise WorkspaceStateError(
                f"machines[{alias!r}] alias field {normalized['alias']!r} does not match key"
            )
        validated[alias] = normalized
    data["machines"] = validated
    return data


def load_inventory(*, path: Path | None = None) -> dict[str, Any]:
    target = path or INVENTORY_PATH
    if not target.exists():
        return _empty_inventory()
    data = _load_json(target)
    return _validate_inventory_payload(data)


def save_inventory(data: dict[str, Any], *, path: Path | None = None) -> None:
    payload = dict(data)
    payload["schema_version"] = INVENTORY_SCHEMA_VERSION
    payload["updated_at"] = utc_now_iso()
    payload = _validate_inventory_payload(payload)
    _save_json(path or INVENTORY_PATH, payload)


@contextlib.contextmanager
def inventory_lock(*, path: Path | None = None):
    target = path or INVENTORY_PATH
    ensure_state_dir(target.parent)
    lock_path = target.with_name(target.name + ".lock")
    deadline = time.monotonic() + INVENTORY_LOCK_TIMEOUT_SECONDS
    fd: int | None = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise WorkspaceStateError(f"timed out waiting for inventory lock {lock_path}")
            time.sleep(INVENTORY_LOCK_POLL_SECONDS)
    try:
        yield lock_path
    finally:
        if fd is not None:
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            lock_path.unlink()


def find_machines(
    inventory: dict[str, Any],
    *,
    alias: str | None = None,
    host: str | None = None,
    identifier: str | None = None,
) -> list[dict[str, Any]]:
    machines = inventory.get("machines", {})
    matches: list[dict[str, Any]] = []
    for record in machines.values():
        if identifier is not None and (
            record["alias"] == identifier or record["host"] == identifier
        ):
            matches.append(record)
            continue
        if alias is not None and record["alias"] == alias:
            matches.append(record)
            continue
        if host is not None and record["host"] == host:
            matches.append(record)
    return matches


def upsert_machine(record: dict[str, Any], *, path: Path | None = None) -> tuple[str, dict[str, Any]]:
    normalized = validate_machine_record(record)
    alias = normalized["alias"]
    host = normalized["host"]
    with inventory_lock(path=path):
        inventory = load_inventory(path=path)
        machines = inventory.setdefault("machines", {})
        existing = machines.get(alias)
        host_matches = find_machines(inventory, host=host)
        if existing:
            if host_matches and host_matches[0]["alias"] != alias:
                raise WorkspaceStateError(
                    "host already registered under a different alias; resolve the conflict manually"
                )
            action = "updated"
        else:
            if host_matches:
                raise WorkspaceStateError(
                    "host already registered under a different alias; resolve the conflict manually"
                )
            action = "inserted"
            if "created_at" not in normalized:
                normalized["created_at"] = utc_now_iso()
        machines[alias] = normalized
        save_inventory(inventory, path=path)
    return action, normalized


def remove_machine(identifier: str, *, path: Path | None = None) -> dict[str, Any]:
    with inventory_lock(path=path):
        inventory = load_inventory(path=path)
        matches = find_machines(inventory, identifier=identifier)
        if not matches:
            raise WorkspaceStateError(f"no machine found for identifier: {identifier}")
        if len(matches) > 1:
            raise WorkspaceStateError(
                f"multiple machines matched {identifier!r}; use a unique alias or host IP"
            )
        target = matches[0]
        inventory["machines"] = {
            alias: record
            for alias, record in inventory["machines"].items()
            if record is not target
        }
        save_inventory(inventory, path=path)
    return target


def get_machine(alias: str, *, path: Path | None = None) -> dict[str, Any]:
    inventory = load_inventory(path=path)
    machines = inventory.get("machines", {})
    normalized_alias = require_safe_id(alias, label="alias")
    if normalized_alias not in machines:
        raise WorkspaceStateError(f"machine not found: {normalized_alias}")
    return machines[normalized_alias]


def list_machines(*, path: Path | None = None) -> dict[str, dict[str, Any]]:
    return dict(load_inventory(path=path).get("machines", {}))


def redact_secrets(payload: Any) -> Any:
    """Return a copy with common secret keys masked in dict/list trees."""
    secret_keys = {"password", "token", "kubeconfig", "secret", "private_key"}
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for key, value in payload.items():
            if any(part in str(key).lower() for part in secret_keys):
                out[key] = "<redacted>"
            else:
                out[key] = redact_secrets(value)
        return out
    if isinstance(payload, list):
        return [redact_secrets(item) for item in payload]
    return payload


def resolve_inventory_read_path(preferred_path: Path = INVENTORY_PATH) -> Path:
    preferred_path = preferred_path.expanduser().resolve()
    if same_path(preferred_path, INVENTORY_PATH) and not preferred_path.exists() and LEGACY_INVENTORY_PATH.exists():
        return LEGACY_INVENTORY_PATH.expanduser().resolve()
    return preferred_path
