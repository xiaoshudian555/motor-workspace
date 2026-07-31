#!/usr/bin/env python3
"""Workspace-ready probe helpers for repo-init."""

from __future__ import annotations

from typing import Any

from mws_result import CheckRunner, build_result_envelope, utc_now_iso


def build_workspace_checks(
    *,
    tools: dict[str, Any],
    gh: dict[str, Any],
    repos: dict[str, Any],
    submodules: dict[str, Any],
    lock: dict[str, Any],
) -> CheckRunner:
    runner = CheckRunner()

    git_path = tools.get("git")
    if git_path:
        runner.append({"name": "tool:git", "status": "ok", "message": "git available", "evidence": git_path})
    else:
        runner.append({"name": "tool:git", "status": "error", "message": "git not found in PATH"})
        return runner

    python_path = tools.get("python3") or tools.get("python")
    if python_path:
        runner.append(
            {"name": "tool:python", "status": "ok", "message": "python available", "evidence": python_path}
        )
    else:
        runner.append({"name": "tool:python", "status": "warning", "message": "python3 not found in PATH"})

    if gh.get("installed"):
        if gh.get("logged_in"):
            runner.append(
                {
                    "name": "gh_auth",
                    "status": "ok",
                    "message": "GitHub CLI authenticated",
                    "evidence": gh.get("user_login"),
                }
            )
        else:
            runner.append(
                {
                    "name": "gh_auth",
                    "status": "warning",
                    "message": "GitHub CLI installed but not authenticated",
                }
            )
    else:
        runner.append(
            {
                "name": "gh_auth",
                "status": "warning",
                "message": "GitHub CLI not installed",
            }
        )

    for role, repo in repos.items():
        name = f"repo:{role}"
        if repo.get("error"):
            runner.append({"name": name, "status": "error", "message": str(repo["error"])})
            continue
        if not repo.get("initialized"):
            severity = "warning" if role != "workspace" else "error"
            runner.append(
                {
                    "name": name,
                    "status": severity,
                    "message": f"{role} repository is not initialized",
                }
            )
            if severity == "error":
                return runner
            continue
        worktree = repo.get("worktree") or {}
        if worktree.get("dirty"):
            runner.append(
                {
                    "name": f"{name}:dirty",
                    "status": "warning",
                    "message": f"{role} worktree has local changes",
                    "evidence": str(worktree.get("entry_count", 0)),
                }
            )
        runner.append(
            {
                "name": name,
                "status": "ok",
                "message": f"{role} repository initialized",
                "evidence": repo.get("head_commit") or repo.get("path"),
            }
        )

    if submodules.get("all_initialized") is False:
        runner.append(
            {
                "name": "submodules",
                "status": "warning",
                "message": "one or more submodules are not initialized",
                "evidence": str(submodules.get("count", 0)),
            }
        )
    elif submodules.get("count", 0) > 0:
        runner.append(
            {
                "name": "submodules",
                "status": "ok",
                "message": "submodules initialized",
                "evidence": str(submodules.get("count")),
            }
        )

    lock_status = str(lock.get("status", "ok"))
    lock_errors = list(lock.get("errors") or [])
    lock_warnings = list(lock.get("warnings") or [])
    if lock_errors:
        runner.append(
            {
                "name": "workspace_lock",
                "status": "error",
                "message": "; ".join(lock_errors),
            }
        )
        return runner
    if lock_warnings:
        runner.append(
            {
                "name": "workspace_lock",
                "status": "warning",
                "message": "; ".join(lock_warnings),
            }
        )
    else:
        runner.append(
            {
                "name": "workspace_lock",
                "status": "ok",
                "message": f"workspace lock status={lock_status}",
            }
        )
    return runner


def build_workspace_result_envelope(
    *,
    run_id: str,
    workflow_run_id: str,
    payload: dict[str, Any],
    started_at: str,
) -> dict[str, Any]:
    return build_result_envelope(
        kind="workspace-ready",
        run_id=run_id,
        workflow_run_id=workflow_run_id,
        checks=payload.get("checks", []),
        started_at=started_at,
        warnings=payload.get("warnings", []),
        errors=payload.get("errors", []),
        extra={
            "workspace_root": payload.get("workspace_root"),
            "workspace_id": payload.get("workspace_id"),
            "repos": payload.get("repos"),
            "submodules": payload.get("submodules"),
            "tools": payload.get("tools"),
            "gh": payload.get("gh"),
            "lock": payload.get("lock"),
            "platform": payload.get("platform"),
            "stopped_at": payload.get("stopped_at"),
        },
    )


def probe_payload_to_workspace_record(
    *,
    probe: dict[str, Any],
    runner: CheckRunner,
) -> dict[str, Any]:
    repos = probe.get("repos") or {}
    compact_repos = {
        role: {
            "path": (repos.get(role) or {}).get("path"),
            "initialized": (repos.get(role) or {}).get("initialized"),
            "head_commit": ((repos.get(role) or {}).get("branch") or {}).get("head_commit"),
            "dirty": ((repos.get(role) or {}).get("worktree") or {}).get("dirty"),
        }
        for role in repos
    }
    return {
        "workspace_root": probe.get("workspace"),
        "workspace_id": probe.get("workspace_id"),
        "checks": runner.checks,
        "warnings": runner.warnings,
        "errors": runner.errors,
        "stopped_at": runner.stopped_at,
        "repos": compact_repos,
        "submodules": probe.get("submodules") if isinstance(probe.get("submodules"), dict) else {},
        "tools": probe.get("tools") or {},
        "gh": {
            "installed": (probe.get("gh") or {}).get("installed"),
            "logged_in": (probe.get("gh") or {}).get("logged_in"),
            "user_login": (probe.get("gh") or {}).get("user_login"),
        },
        "lock": probe.get("lock") or {},
        "platform": probe.get("platform") or {},
    }
