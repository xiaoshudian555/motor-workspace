#!/usr/bin/env python3
"""Machine-bound fixed remote workspace mapping."""

from __future__ import annotations

from typing import Any

from mws_local_state import WorkspaceStateError, get_machine
from mws_validate import normalize_mount_root, require_safe_id

DEFAULT_REMOTE_WORKSPACE_SUFFIX = "motor-workspace"


def remote_workspace_root(machine: dict[str, Any]) -> str:
    mount = normalize_mount_root(machine.get("mount_root"))
    configured = machine.get("remote_workspace_root")
    if configured:
        root = str(configured).rstrip("/")
        if not root.startswith("/"):
            raise WorkspaceStateError("remote_workspace_root must be an absolute POSIX path")
        return root
    return f"{mount}/{DEFAULT_REMOTE_WORKSPACE_SUFFIX}"


def build_fixed_source_paths(machine: dict[str, Any]) -> dict[str, str]:
    mount = normalize_mount_root(machine.get("mount_root"))
    root = remote_workspace_root(machine)
    return {
        "mount_root": mount,
        "remote_workspace_root": root,
        "motor_source": f"{root}/motor",
        "vllm_source": f"{root}/vllm",
        "vllm_ascend_source": f"{root}/vllm-ascend",
        "python_overlay": f"{root}/python-overlay",
    }


def pythonpath_for_machine(machine: dict[str, Any]) -> str:
    paths = build_fixed_source_paths(machine)
    return ":".join(
        [
            paths["motor_source"],
            paths["vllm_source"],
            paths["vllm_ascend_source"],
            paths["python_overlay"],
        ]
    )


def machine_ref(machine: dict[str, Any]) -> dict[str, Any]:
    alias = machine.get("alias") or machine.get("host")
    paths = build_fixed_source_paths(machine)
    return {
        "alias": alias,
        "host": machine.get("host"),
        "port": machine.get("port", 22),
        "user": machine.get("user", "root"),
        "mount_root": paths["mount_root"],
        "remote_workspace_root": paths["remote_workspace_root"],
        "source_dirs": {
            "motor": paths["motor_source"],
            "vllm": paths["vllm_source"],
            "vllm_ascend": paths["vllm_ascend_source"],
            "python_overlay": paths["python_overlay"],
        },
        "pythonpath": pythonpath_for_machine(machine),
    }


def resolve_machine(alias: str) -> dict[str, Any]:
    normalized = require_safe_id(alias, label="machine")
    machine = get_machine(normalized)
    machine = dict(machine)
    machine.setdefault("alias", normalized)
    return machine


def endpoint_payload_for_machine(
    machine: dict[str, Any],
    *,
    root: str | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    paths = build_fixed_source_paths(machine)
    payload: dict[str, Any] = {
        "host": str(machine["host"]),
        "port": int(machine.get("port", 22)),
        "user": str(machine.get("user", "root")),
        "root": root or paths["remote_workspace_root"],
        "cwd": cwd or paths["remote_workspace_root"],
        "alias": machine.get("alias"),
        "source": {"machine_ref": machine_ref(machine)},
    }
    return payload
