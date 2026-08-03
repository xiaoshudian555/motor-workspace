#!/usr/bin/env python3
"""Remote-native self-identification.

In the remote-native topology the Agent runs directly on the target NPU host,
so the SSH connection metadata in a machine record (host/port/user) is
meaningless. This module probes the current host and derives the fields that
*matter* (mount_root, fixed source paths, kube context) so the agent can
register itself as an `executor=native` machine without a manual
machine_add call.
"""

from __future__ import annotations

import getpass
import re
import socket
import subprocess
from typing import Any

from mws_local_state import WorkspaceStateError, find_machines, load_inventory

DEFAULT_MOUNT_ROOT = "/mnt"
DEFAULT_WORKSPACE_SUFFIX = "motor-workspace"
ALIAS_FALLBACK_PREFIX = "native"
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$")


def detect_hostname() -> str:
    return (socket.gethostname() or "localhost").strip()


def detect_current_user() -> str:
    return getpass.getuser() or "root"


def detect_mount_root() -> str:
    """Return the existing shared mount root, defaulting to /mnt."""
    candidate = DEFAULT_MOUNT_ROOT
    if candidate.startswith("/") and _path_exists(candidate):
        return candidate
    return candidate


def detect_remote_workspace_root(mount_root: str) -> str:
    candidate = f"{mount_root.rstrip('/')}/{DEFAULT_WORKSPACE_SUFFIX}"
    if _path_exists(candidate):
        return candidate
    return candidate


def detect_kube_context() -> str:
    """Return current kubectl context if readable; empty string otherwise."""
    try:
        result = subprocess.run(
            ["kubectl", "config", "current-context"],
            check=False,
            text=True,
            capture_output=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _path_exists(path: str) -> bool:
    from pathlib import Path

    return Path(path).exists()


def sanitize_alias(value: str, *, prefix: str = ALIAS_FALLBACK_PREFIX) -> str:
    """Derive a machine alias from a hostname, guaranteeing SAFE_ID shape."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "", value.strip())
    if not cleaned or not cleaned[0].isalnum():
        cleaned = f"{prefix}{cleaned}"
    cleaned = cleaned[:63]
    if len(cleaned) < 3:
        cleaned = f"{cleaned}{'x' * (3 - len(cleaned))}"
    if not cleaned[0].isalnum():
        cleaned = f"{prefix}{cleaned}"
    if len(cleaned) > 63:
        cleaned = cleaned[:63]
    if not SAFE_ID_RE.fullmatch(cleaned):
        raise WorkspaceStateError(f"cannot derive a safe alias from hostname {value!r}")
    return cleaned


def find_existing_native_alias(hostname: str) -> str | None:
    """Return the alias of an existing native machine for this host, if any.

    Reusing an existing alias keeps state (parity evidence, machine-ready
    runs) attached to a stable identity instead of churning on hostname
    changes.
    """
    inventory = load_inventory()
    matches = find_machines(inventory, host=hostname)
    native = [m for m in matches if m.get("executor") == "native"]
    if native:
        return str(native[0]["alias"])
    return None


def build_native_record(
    *,
    alias: str | None = None,
    hostname: str | None = None,
    user: str | None = None,
    mount_root: str | None = None,
    remote_workspace_root: str | None = None,
    kube_context: str | None = None,
) -> dict[str, Any]:
    hostname = hostname or detect_hostname()
    alias = alias or find_existing_native_alias(hostname) or sanitize_alias(hostname)
    mount_root = mount_root or detect_mount_root()
    workspace_root = remote_workspace_root or detect_remote_workspace_root(mount_root)
    return {
        "alias": alias,
        "host": hostname,
        "user": user or detect_current_user(),
        "port": 22,
        "mount_root": mount_root,
        "remote_workspace_root": workspace_root,
        "kube_context": kube_context if kube_context is not None else detect_kube_context(),
        "parity_backend": "shared-hostpath",
        "executor": "native",
        "source_dirs": {
            "motor": f"{workspace_root}/motor",
            "vllm": f"{workspace_root}/vllm",
            "vllm_ascend": f"{workspace_root}/vllm-ascend",
            "python_overlay": f"{workspace_root}/python-overlay",
        },
    }
