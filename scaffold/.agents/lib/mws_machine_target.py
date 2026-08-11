#!/usr/bin/env python3
"""Resolve inventory aliases to fixed remote workspace paths."""

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
        if root != mount and not root.startswith(f"{mount}/"):
            raise WorkspaceStateError("remote_workspace_root must stay under mount_root")
        return root
    return f"{mount}/{DEFAULT_REMOTE_WORKSPACE_SUFFIX}"


def build_fixed_source_paths(machine: dict[str, Any]) -> dict[str, str]:
    mount = normalize_mount_root(machine.get("mount_root"))
    root = remote_workspace_root(machine)
    custom = machine.get("source_dirs") or {}
    return {
        "mount_root": mount,
        "remote_workspace_root": root,
        "motor_source": custom.get("motor") or f"{root}/motor",
        "vllm_source": custom.get("vllm") or f"{root}/vllm",
        "vllm_ascend_source": custom.get("vllm_ascend") or f"{root}/vllm-ascend",
        "python_overlay": custom.get("python_overlay") or f"{root}/python-overlay",
    }


def machine_ref(machine: dict[str, Any]) -> dict[str, Any]:
    paths = build_fixed_source_paths(machine)
    return {
        "alias": machine.get("alias") or machine.get("host"),
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
    }


def resolve_machine(alias: str) -> dict[str, Any]:
    normalized = require_safe_id(alias, label="machine")
    machine = dict(get_machine(normalized))
    machine.setdefault("alias", normalized)
    return machine
