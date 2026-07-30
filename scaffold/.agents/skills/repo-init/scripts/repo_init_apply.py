#!/usr/bin/env python3
"""Apply repo-init mutations with explicit user consent flags."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(SCRIPTS))

from repo_paths import REPO_ROOT  # noqa: E402

from mws_result import emit, progress  # noqa: E402

from _repo_init_common import REPO_PATHS, REPO_ROLES  # noqa: E402
from repo_topology import (  # noqa: E402
    RepoTopologyError,
    configure_remotes,
    remote_names,
    resolve_repo,
)


def _git_run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "safe.directory=*", *args],
        cwd=str(REPO_ROOT),
        check=False,
        text=True,
        capture_output=True,
    )


def init_submodules() -> dict[str, Any]:
    progress("initializing submodules")
    result = _git_run(["submodule", "sync", "--recursive"])
    if result.returncode:
        return {
            "status": "error",
            "errors": [result.stderr.strip() or result.stdout.strip()],
        }

    result = _git_run(["submodule", "update", "--init", "--recursive"])
    if result.returncode:
        return {
            "status": "error",
            "errors": [result.stderr.strip() or result.stdout.strip()],
        }
    return {"status": "ok", "action": "submodules_init"}


def apply_topology(
    *,
    repo_role: str,
    origin_url: str | None,
    upstream_url: str | None,
    gh_default: str,
) -> dict[str, Any]:
    if repo_role not in REPO_ROLES:
        return {"status": "error", "errors": [f"unknown repo role: {repo_role}"]}
    repo_path = REPO_PATHS[repo_role]
    progress(f"configuring remotes for {repo_role}")
    try:
        resolved = resolve_repo(str(repo_path))
    except RepoTopologyError as exc:
        return {"status": "error", "errors": [str(exc)], "repo_role": repo_role}

    before = set(remote_names(resolved))
    result = configure_remotes(
        resolved,
        origin_url=origin_url,
        upstream_url=upstream_url,
        gh_default=gh_default,
    )
    after = set(remote_names(resolved))
    preserved_extra = sorted(name for name in before if name not in {"origin", "upstream"})
    still_present = [name for name in preserved_extra if name in after]
    return {
        "status": "ok",
        "action": "configure_remotes",
        "repo_role": repo_role,
        "topology": result,
        "preserved_extra_remotes": still_present,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply repo-init mutations")
    parser.add_argument(
        "--submodules",
        action="store_true",
        help="initialize recursive submodules (requires explicit consent flag)",
    )
    parser.add_argument(
        "--configure-remotes",
        action="store_true",
        help="configure origin/upstream for one repo role (requires explicit consent flag)",
    )
    parser.add_argument(
        "--repo",
        choices=list(REPO_ROLES),
        help="repo role for --configure-remotes",
    )
    parser.add_argument("--origin-url", help="desired origin fetch/push URL")
    parser.add_argument("--upstream-url", help="desired upstream fetch/push URL")
    parser.add_argument(
        "--gh-default",
        choices=["origin", "upstream", "none"],
        default="none",
        help="optional gh repo set-default target",
    )
    args = parser.parse_args()

    actions: list[dict[str, Any]] = []

    if args.submodules:
        sub_result = init_submodules()
        actions.append(sub_result)
        if sub_result.get("status") == "error":
            return emit({"status": "error", "errors": sub_result.get("errors", []), "actions": actions})

    if args.configure_remotes:
        if not args.repo:
            return emit({"status": "error", "errors": ["--configure-remotes requires --repo"]})
        if not args.origin_url and not args.upstream_url and args.gh_default == "none":
            return emit(
                {
                    "status": "error",
                    "errors": [
                        "--configure-remotes requires at least one of "
                        "--origin-url, --upstream-url, or --gh-default"
                    ],
                }
            )
        topo_result = apply_topology(
            repo_role=args.repo,
            origin_url=args.origin_url,
            upstream_url=args.upstream_url,
            gh_default=args.gh_default,
        )
        actions.append(topo_result)
        if topo_result.get("status") == "error":
            return emit({"status": "error", "errors": topo_result.get("errors", []), "actions": actions})

    if not actions:
        return emit({"status": "error", "errors": ["no apply action selected"]})

    return emit({"status": "ok", "actions": actions})


if __name__ == "__main__":
    raise SystemExit(main())
