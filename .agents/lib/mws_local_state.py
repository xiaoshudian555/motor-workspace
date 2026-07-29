#!/usr/bin/env python3
"""Local untracked state helpers for motor-workspace."""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import string
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_DIRNAME = ".motor-workspace-local"
PROFILE_FILENAME = "machine-profile.json"
INVENTORY_FILENAME = "machine-inventory.json"
SESSIONS_DIRNAME = "sessions"
CONSENT_DIRNAME = "consent"
PROFILE_SCHEMA_VERSION = 1
INVENTORY_SCHEMA_VERSION = 1
WORKSPACE_ID_PREFIX = "mws-"
USERNAME_PATTERN = re.compile(r"^[a-z0-9]{3,32}$")
RANDOM_ALPHABET = string.digits
DEFAULT_RANDOM_PREFIX = "agent"
DEFAULT_RANDOM_SUFFIX_LENGTH = 5

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / STATE_DIRNAME
LOCAL_ROOT = STATE_DIR
PROFILE_PATH = STATE_DIR / PROFILE_FILENAME
INVENTORY_PATH = STATE_DIR / INVENTORY_FILENAME
SESSIONS_DIR = STATE_DIR / SESSIONS_DIRNAME
CONSENT_DIR = STATE_DIR / CONSENT_DIRNAME


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
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkspaceStateError(f"invalid JSON in {path}: {exc}") from exc


def _save_json(path: Path, data: Any) -> None:
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


def load_profile() -> dict[str, Any]:
    if not PROFILE_PATH.exists():
        return {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "workspace_id": default_workspace_id(),
            "created_at": utc_now_iso(),
        }
    data = _load_json(PROFILE_PATH)
    if not isinstance(data, dict):
        raise WorkspaceStateError(f"{PROFILE_PATH} must contain a JSON object")
    if "workspace_id" not in data:
        data["workspace_id"] = default_workspace_id()
    return data


def save_profile(data: dict[str, Any]) -> None:
    data = dict(data)
    data["schema_version"] = PROFILE_SCHEMA_VERSION
    data["updated_at"] = utc_now_iso()
    _save_json(PROFILE_PATH, data)


def load_inventory() -> dict[str, Any]:
    if not INVENTORY_PATH.exists():
        return {"schema_version": INVENTORY_SCHEMA_VERSION, "machines": {}}
    data = _load_json(INVENTORY_PATH)
    if not isinstance(data, dict):
        raise WorkspaceStateError(f"{INVENTORY_PATH} must contain a JSON object")
    data.setdefault("machines", {})
    return data


def save_inventory(data: dict[str, Any]) -> None:
    data = dict(data)
    data["schema_version"] = INVENTORY_SCHEMA_VERSION
    data["updated_at"] = utc_now_iso()
    _save_json(INVENTORY_PATH, data)


def get_machine(alias: str) -> dict[str, Any]:
    inventory = load_inventory()
    machines = inventory.get("machines", {})
    if alias not in machines:
        raise WorkspaceStateError(f"machine not found: {alias}")
    return machines[alias]


def list_machines() -> dict[str, dict[str, Any]]:
    return dict(load_inventory().get("machines", {}))


def redact_secrets(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy with common secret keys masked."""
    secret_keys = {"password", "token", "kubeconfig", "secret", "private_key"}
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if any(part in key.lower() for part in secret_keys):
            out[key] = "<redacted>"
        elif isinstance(value, dict):
            out[key] = redact_secrets(value)
        else:
            out[key] = value
    return out
