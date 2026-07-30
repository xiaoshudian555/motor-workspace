#!/usr/bin/env python3
"""Workspace lock helpers."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from mws_local_state import ROOT, WorkspaceStateError, utc_now_iso
from mws_validate import normalize_mount_root

LOCK_PATH = ROOT / "workspace.lock.yaml"
SOURCE_KEYS = ("motor", "vllm", "vllm_ascend")
UNRESOLVED = "UNRESOLVED"


def _load_yaml_or_json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise WorkspaceStateError("workspace.lock.yaml requires PyYAML") from exc
        loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise WorkspaceStateError("workspace.lock.yaml must contain an object")
        return loaded
    if not isinstance(data, dict):
        raise WorkspaceStateError("workspace.lock.yaml must contain an object")
    return data


def _dump_lock(data: dict[str, Any]) -> str:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise WorkspaceStateError("writing workspace.lock.yaml requires PyYAML") from exc
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def load_lock() -> dict[str, Any]:
    return _load_yaml_or_json(LOCK_PATH.read_text(encoding="utf-8"))


def save_lock(data: dict[str, Any]) -> None:
    data = dict(data)
    data.setdefault("schema_version", 1)
    LOCK_PATH.write_text(_dump_lock(data), encoding="utf-8")


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


def gitlink_commit(path: Path) -> str | None:
    result = subprocess.run(
        ["git", "ls-tree", "HEAD", str(path.relative_to(ROOT))],
        cwd=str(ROOT),
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode or not result.stdout.strip():
        return None
    parts = result.stdout.split()
    if len(parts) >= 3 and parts[1] == "commit":
        return parts[2]
    return None


def submodule_initialized(path: Path) -> bool:
    return path.exists() and (path / ".git").exists() or (
        (ROOT / ".git" / "modules" / path.name.replace("-", "_")).exists()
    )


def resolve_base_image_ref(
    *,
    lock: dict[str, Any] | None = None,
    config_dir: Path | None = None,
    explicit: str | None = None,
) -> str:
    if explicit and explicit.strip() and explicit.strip() != UNRESOLVED:
        return explicit.strip()
    lock = lock or load_lock()
    runtime = lock.get("runtime", {})
    locked = runtime.get("base_image_ref") or runtime.get("base_image", "")
    if locked and locked != UNRESOLVED:
        return str(locked)
    if config_dir is not None:
        for name in ("user_config.json", "user_config_pd_hetero.json"):
            candidate = config_dir / name
            if not candidate.exists():
                continue
            data = json.loads(candidate.read_text(encoding="utf-8"))
            deploy = data.get("motor_deploy_config", {})
            image = deploy.get("image_name") or deploy.get("image")
            if isinstance(image, str) and image.strip():
                return image.strip()
    raise WorkspaceStateError(
        "base_image_ref unresolved: set runtime.base_image_ref in workspace.lock.yaml "
        "or motor_deploy_config.image_name in deploy config"
    )


def refresh_lock(
    *,
    base_image_ref: str | None = None,
    allow_uninitialized: bool = False,
) -> dict[str, Any]:
    lock = load_lock()
    errors: list[str] = []
    sources = lock.setdefault("sources", {})
    refreshed: list[dict[str, Any]] = []

    for name in SOURCE_KEYS:
        config = sources.get(name)
        if not isinstance(config, dict):
            errors.append(f"{name}: missing sources entry")
            continue
        repo_path = ROOT / str(config["path"])
        entry: dict[str, Any] = {"name": name, "path": config.get("path")}
        if not submodule_initialized(repo_path):
            entry["present"] = False
            if not allow_uninitialized:
                errors.append(f"{name}: submodule not initialized")
        else:
            head = git_head(repo_path)
            gitlink = gitlink_commit(repo_path)
            config["commit"] = head
            entry.update(
                {
                    "present": True,
                    "commit": head,
                    "gitlink": gitlink,
                    "gitlink_match": gitlink == head if gitlink else None,
                }
            )
            branch = config.get("branch")
            if branch:
                branch_head = subprocess.run(
                    ["git", "-C", str(repo_path), "rev-parse", f"origin/{branch}"],
                    check=False,
                    text=True,
                    capture_output=True,
                )
                if branch_head.returncode == 0 and branch_head.stdout.strip() != head:
                    entry["branch_drift"] = {
                        "branch": branch,
                        "origin_head": branch_head.stdout.strip(),
                        "checked_out": head,
                    }
        refreshed.append(entry)

    runtime = lock.setdefault("runtime", {})
    runtime["mount_root"] = normalize_mount_root(runtime.get("mount_root"))
    if base_image_ref:
        runtime["base_image_ref"] = base_image_ref
    elif runtime.get("base_image_ref") in (None, "", UNRESOLVED):
        try:
            config_dir = ROOT / "motor" / "examples" / "infer_engines" / "vllm"
            runtime["base_image_ref"] = resolve_base_image_ref(lock=lock, config_dir=config_dir)
        except WorkspaceStateError:
            runtime.setdefault("base_image_ref", UNRESOLVED)

    lock["refreshed_at"] = utc_now_iso()
    if not errors:
        save_lock(lock)

    return {
        "status": "error" if errors else "ok",
        "sources": refreshed,
        "runtime": runtime,
        "errors": errors,
        "saved": not errors,
    }


def verify_lock(
    *,
    require_base_image: bool = False,
    strict_commits: bool = False,
) -> dict[str, Any]:
    lock = load_lock()
    errors: list[str] = []
    warnings: list[str] = []
    sources_out: list[dict[str, Any]] = []
    for name, config in lock.get("sources", {}).items():
        repo_path = ROOT / str(config["path"])
        entry: dict[str, Any] = {"name": name, "path": config.get("path")}
        if not submodule_initialized(repo_path):
            errors.append(f"{name}: submodule not initialized")
            entry["present"] = False
        else:
            head = git_head(repo_path)
            lock_commit = config.get("commit")
            lock_match = head == lock_commit
            entry.update(
                {
                    "present": True,
                    "commit": head,
                    "lock_commit": lock_commit,
                    "lock_match": lock_match,
                }
            )
            if lock_commit == UNRESOLVED:
                if strict_commits:
                    errors.append(f"{name}: lock commit is unresolved")
                else:
                    warnings.append(f"{name}: lock commit is unresolved (runtime refresh unaffected)")
            elif not lock_match:
                msg = f"{name}: submodule HEAD differs from lock (dirty tree allowed for runtime refresh)"
                if strict_commits:
                    errors.append(msg)
                else:
                    warnings.append(msg)
        sources_out.append(entry)
    runtime = lock.get("runtime", {})
    mount_root = normalize_mount_root(runtime.get("mount_root"))
    base_image_ref = runtime.get("base_image_ref") or runtime.get("base_image", "")
    if require_base_image and (not base_image_ref or base_image_ref == UNRESOLVED):
        errors.append("runtime.base_image_ref is required for deploy")
    return {
        "status": "error" if errors else ("warning" if warnings else "ok"),
        "sources": sources_out,
        "runtime": {
            "mount_root": mount_root,
            "base_image_ref": base_image_ref,
            "hardware_profile": runtime.get("hardware_profile"),
        },
        "errors": errors,
        "warnings": warnings,
    }


def report_origin_main_gone() -> dict[str, Any]:
    result = subprocess.run(
        ["git", "branch", "-vv"],
        cwd=str(ROOT),
        check=False,
        text=True,
        capture_output=True,
    )
    gone = []
    for line in result.stdout.splitlines():
        if re.search(r"\[origin/main:\s*gone\]", line):
            gone.append(line.strip())
    return {
        "status": "warning" if gone else "ok",
        "origin_main_gone": bool(gone),
        "branches": gone,
        "message": (
            "upstream origin/main is gone; fix remote tracking locally — not auto-modified"
            if gone
            else "origin/main tracking looks healthy"
        ),
    }
