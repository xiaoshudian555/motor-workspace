#!/usr/bin/env python3
"""Read-only probe for motor-workspace repo-init.

Reports platform, GitHub CLI/auth, recursive submodule state, lock alignment,
and remote topology for workspace + motor + vllm + vllm-ascend. Never mutates.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(SCRIPTS))

from repo_paths import REPO_ROOT  # noqa: E402

from mws_lock import verify_lock  # noqa: E402
from mws_local_state import load_profile  # noqa: E402
from mws_result import emit_result, progress, utc_now_iso  # noqa: E402
from mws_run_state import new_run_id, new_workflow_run_id, write_run  # noqa: E402
from mws_workspace import (  # noqa: E402
    build_workspace_checks,
    build_workspace_result_envelope,
    probe_payload_to_workspace_record,
)

from _repo_init_common import (  # noqa: E402
    REPO_PATHS,
    REPO_ROLES,
    compact_fork_summary,
    compact_repo_summary,
    compact_submodule_summary,
    detect_platform,
    gh_fork_info,
    gh_install_plan,
    gh_login,
    git_root,
    git_submodule_status,
    inspect_repo,
    tool_state,
)


def workspace_ready_facts(
    *,
    repos: dict[str, Any],
    submodules: dict[str, Any],
    gh: dict[str, Any],
    lock: dict[str, Any],
) -> dict[str, Any]:
    repo_rows = repos or {}
    initialized_roles = [
        role
        for role, repo in repo_rows.items()
        if repo.get("initialized") and not repo.get("error")
    ]
    return {
        "repos_initialized": initialized_roles,
        "all_repos_initialized": len(initialized_roles) == len(REPO_ROLES),
        "submodules_initialized": submodules.get("all_initialized"),
        "gh_available": bool(gh.get("installed")),
        "gh_authenticated": bool(gh.get("logged_in")),
        "lock_status": lock.get("status"),
        "lock_errors": lock.get("errors", []),
        "lock_warnings": lock.get("warnings", []),
    }


def build_probe_payload() -> dict[str, Any]:
    platform_info = detect_platform()
    root = git_root(REPO_ROOT) or REPO_ROOT
    gh_state = gh_login()
    user_login = gh_state.get("user_login")
    profile = load_profile(persist_missing=False)
    lock = verify_lock(require_base_image=False)
    submodule_rows = git_submodule_status(root) if root.exists() else []
    compact_submodules = compact_submodule_summary(submodule_rows)
    repos = {
        role: inspect_repo(REPO_PATHS[role], role, user_login)
        for role in REPO_ROLES
    }
    return {
        "status": lock["status"],
        "workspace": str(REPO_ROOT),
        "workspace_id": profile.get("workspace_id"),
        "platform": platform_info,
        "tools": tool_state(),
        "gh": gh_state,
        "gh_install_plan": gh_install_plan(platform_info),
        "repo_root": str(root),
        "submodules": compact_submodules,
        "repos": repos,
        "forks": gh_fork_info(user_login) if gh_state.get("logged_in") and user_login else {},
        "lock": lock,
        "next": "run repo_init_apply.py with explicit flags after user consent",
    }


def compact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    gh = payload.get("gh") or {}
    compact_submodules = payload.get("submodules") or {}
    lock = payload.get("lock") or {}
    repos = {
        role: compact_repo_summary(repo)
        for role, repo in (payload.get("repos") or {}).items()
    }
    return {
        "status": payload.get("status"),
        "workspace": payload.get("workspace"),
        "workspace_id": payload.get("workspace_id"),
        "workspace_run_id": payload.get("workspace_run_id"),
        "schema_version": payload.get("schema_version"),
        "kind": payload.get("kind"),
        "run_id": payload.get("run_id"),
        "workflow_run_id": payload.get("workflow_run_id"),
        "platform": {
            "kind": payload.get("platform", {}).get("kind"),
            "machine": payload.get("platform", {}).get("machine"),
        },
        "gh": {
            "installed": gh.get("installed"),
            "logged_in": gh.get("logged_in"),
            "user_login": gh.get("user_login"),
            "git_protocol": gh.get("git_protocol"),
        },
        "gh_install_plan": {
            "preferred": payload.get("gh_install_plan", {}).get("preferred", {}).get("label"),
            "fallback": payload.get("gh_install_plan", {}).get("fallback", {}).get("label"),
        },
        "submodules": compact_submodules,
        "repos": repos,
        "forks": compact_fork_summary(payload.get("forks") or {}),
        "lock": {
            "status": lock.get("status"),
            "errors": lock.get("errors", []),
            "warnings": lock.get("warnings", []),
        },
        "workspace_ready": workspace_ready_facts(
            repos=repos,
            submodules=compact_submodules,
            gh=gh,
            lock=lock,
        ),
        "checks": payload.get("checks", []),
        "warnings": payload.get("warnings", []),
        "errors": payload.get("errors", []),
        "decision_checkpoint": {
            "required_for_broad_init": True,
            "repo_topology": {
                "required": True,
                "options": ["keep-current", "recommended-fork-mode", "community-only"],
            },
            "submodules": {
                "required": True,
                "initialized": compact_submodules.get("all_initialized"),
            },
        },
        "next": payload.get("next"),
    }


def write_workspace_ready_from_probe(
    probe: dict[str, Any],
    *,
    workspace_run_id: str | None = None,
    workflow_run_id: str | None = None,
    started_at: str | None = None,
) -> dict[str, Any]:
    started = started_at or utc_now_iso()
    run_id = workspace_run_id or new_run_id("workspace")
    workflow = workflow_run_id or new_workflow_run_id()
    runner = build_workspace_checks(
        tools=probe.get("tools") or {},
        gh=probe.get("gh") or {},
        repos=probe.get("repos") or {},
        submodules=probe.get("submodules") or {},
        lock=probe.get("lock") or {},
    )
    record = probe_payload_to_workspace_record(probe=probe, runner=runner)
    envelope = build_workspace_result_envelope(
        run_id=run_id,
        workflow_run_id=workflow,
        payload=record,
        started_at=started,
    )
    write_run("workspace-ready", run_id, envelope, immutable=True)
    return envelope


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only repo-init probe")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="print a compact summary instead of the full raw payload",
    )
    parser.add_argument("--workspace-run-id", default="", help="optional explicit workspace run id")
    parser.add_argument("--workflow-run-id", default="", help="optional workflow run id")
    args = parser.parse_args()

    progress("probing workspace")
    started_at = utc_now_iso()
    probe = build_probe_payload()
    envelope = write_workspace_ready_from_probe(
        probe,
        workspace_run_id=args.workspace_run_id.strip() or None,
        workflow_run_id=args.workflow_run_id.strip() or None,
        started_at=started_at,
    )
    if args.compact:
        merged = compact_payload({**probe, **envelope})
        return emit_result({**envelope, **merged})
    return emit_result(envelope)


if __name__ == "__main__":
    raise SystemExit(main())
