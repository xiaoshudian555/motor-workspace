#!/usr/bin/env python3
"""Workspace lock helpers."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from mws_local_state import ROOT, WorkspaceStateError
from mws_validate import normalize_mount_root

LOCK_PATH = ROOT / "workspace.lock.yaml"


def load_lock() -> dict[str, Any]:
    text = LOCK_PATH.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise WorkspaceStateError("workspace.lock.yaml requires PyYAML") from exc
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise WorkspaceStateError("workspace.lock.yaml must contain an object")
    return data


def git_head(path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise WorkspaceStateError(f"cannot resolve HEAD for {path}: {result.stderr.strip()}")
    return result.stdout.strip()


def verify_lock(*, require_base_image: bool = False) -> dict[str, Any]:
    lock = load_lock()
    errors: list[str] = []
    sources_out: list[dict[str, Any]] = []
    for name, config in lock.get("sources", {}).items():
        repo_path = ROOT / str(config["path"])
        entry: dict[str, Any] = {"name": name, "path": config.get("path")}
        if not repo_path.exists():
            errors.append(f"{name}: submodule not initialized")
            entry["present"] = False
        else:
            head = git_head(repo_path)
            entry.update(
                {
                    "present": True,
                    "commit": head,
                    "lock_commit": config.get("commit"),
                    "lock_match": head == config.get("commit"),
                }
            )
            if config.get("commit") == "UNRESOLVED":
                errors.append(f"{name}: lock commit is unresolved")
            elif not entry["lock_match"]:
                errors.append(f"{name}: submodule HEAD does not match lock")
        sources_out.append(entry)
    runtime = lock.get("runtime", {})
    mount_root = normalize_mount_root(runtime.get("mount_root"))
    base_image_ref = runtime.get("base_image_ref") or runtime.get("base_image", "")
    if require_base_image and (not base_image_ref or base_image_ref == "UNRESOLVED"):
        errors.append("runtime.base_image_ref is required for deploy")
    return {
        "status": "error" if errors else "ok",
        "sources": sources_out,
        "runtime": {
            "mount_root": mount_root,
            "base_image_ref": base_image_ref,
            "hardware_profile": runtime.get("hardware_profile"),
        },
        "errors": errors,
    }
