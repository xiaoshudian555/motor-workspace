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
from mws_result import emit, progress  # noqa: E402

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


def compact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    gh = payload.get("gh") or {}
    compact_submodules = compact_submodule_summary(payload.get("submodules") or [])
    lock = payload.get("lock") or {}
    repos = {
        role: compact_repo_summary(repo)
        for role, repo in (payload.get("repos") or {}).items()
    }
    return {
        "status": payload.get("status"),
        "workspace": payload.get("workspace"),
        "workspace_id": payload.get("workspace_id"),
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only repo-init probe")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="print a compact summary instead of the full raw payload",
    )
    args = parser.parse_args()

    progress("probing workspace")
    platform_info = detect_platform()
    root = git_root(REPO_ROOT) or REPO_ROOT
    gh_state = gh_login()
    user_login = gh_state.get("user_login")
    profile = load_profile(persist_missing=False)
    lock = verify_lock(require_base_image=False)

    payload: dict[str, Any] = {
        "status": lock["status"],
        "workspace": str(REPO_ROOT),
        "workspace_id": profile.get("workspace_id"),
        "platform": platform_info,
        "tools": tool_state(),
        "gh": gh_state,
        "gh_install_plan": gh_install_plan(platform_info),
        "repo_root": str(root),
        "submodules": git_submodule_status(root) if root.exists() else [],
        "repos": {
            role: inspect_repo(REPO_PATHS[role], role, user_login)
            for role in REPO_ROLES
        },
        "forks": gh_fork_info(user_login) if gh_state.get("logged_in") and user_login else {},
        "lock": lock,
        "next": "run repo_init_apply.py with explicit flags after user consent",
    }

    if args.compact:
        payload = compact_payload(payload)

    return emit(payload)


if __name__ == "__main__":
    raise SystemExit(main())
