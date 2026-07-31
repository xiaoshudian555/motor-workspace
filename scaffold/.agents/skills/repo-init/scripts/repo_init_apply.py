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

from mws_result import build_result_envelope, emit_result, progress, utc_now_iso  # noqa: E402
from mws_run_state import new_run_id, new_workflow_run_id  # noqa: E402

from _repo_init_common import REPO_PATHS, REPO_ROLES  # noqa: E402
from repo_init_probe import build_probe_payload, write_workspace_ready_from_probe  # noqa: E402
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
            envelope = build_result_envelope(
                kind="repo-init-apply",
                run_id=new_run_id("repo-init"),
                workflow_run_id=new_workflow_run_id(),
                checks=[],
                started_at=utc_now_iso(),
                errors=sub_result.get("errors", []),
                status="failed",
                extra={"actions": actions},
            )
            return emit_result(envelope)

    if args.configure_remotes:
        if not args.repo:
            envelope = build_result_envelope(
                kind="repo-init-apply",
                run_id=new_run_id("repo-init"),
                workflow_run_id=new_workflow_run_id(),
                checks=[],
                started_at=utc_now_iso(),
                errors=["--configure-remotes requires --repo"],
                status="failed",
            )
            return emit_result(envelope)
        if not args.origin_url and not args.upstream_url and args.gh_default == "none":
            envelope = build_result_envelope(
                kind="repo-init-apply",
                run_id=new_run_id("repo-init"),
                workflow_run_id=new_workflow_run_id(),
                checks=[],
                started_at=utc_now_iso(),
                errors=[
                    "--configure-remotes requires at least one of "
                    "--origin-url, --upstream-url, or --gh-default"
                ],
                status="failed",
            )
            return emit_result(envelope)
        topo_result = apply_topology(
            repo_role=args.repo,
            origin_url=args.origin_url,
            upstream_url=args.upstream_url,
            gh_default=args.gh_default,
        )
        actions.append(topo_result)
        if topo_result.get("status") == "error":
            envelope = build_result_envelope(
                kind="repo-init-apply",
                run_id=new_run_id("repo-init"),
                workflow_run_id=new_workflow_run_id(),
                checks=[],
                started_at=utc_now_iso(),
                errors=topo_result.get("errors", []),
                status="failed",
                extra={"actions": actions},
            )
            return emit_result(envelope)

    if not actions:
        envelope = build_result_envelope(
            kind="repo-init-apply",
            run_id=new_run_id("repo-init"),
            workflow_run_id=new_workflow_run_id(),
            checks=[],
            started_at=utc_now_iso(),
            errors=["no apply action selected"],
            status="failed",
        )
        return emit_result(envelope)

    errors: list[str] = []
    for item in actions:
        if item.get("status") == "error":
            errors.extend(item.get("errors") or [])
    if errors:
        envelope = build_result_envelope(
            kind="repo-init-apply",
            run_id=new_run_id("repo-init"),
            workflow_run_id=new_workflow_run_id(),
            checks=[],
            started_at=utc_now_iso(),
            errors=errors,
            status="failed",
            extra={"actions": actions},
        )
        return emit_result(envelope)

    progress("recording workspace-ready evidence after apply")
    workspace_envelope = write_workspace_ready_from_probe(build_probe_payload())
    apply_envelope = build_result_envelope(
        kind="repo-init-apply",
        run_id=new_run_id("repo-init"),
        workflow_run_id=str(workspace_envelope.get("workflow_run_id")),
        checks=[{"name": "apply", "status": "ok", "message": "apply actions completed"}],
        started_at=utc_now_iso(),
        upstream_refs=[{"kind": "workspace-ready", "run_id": workspace_envelope["run_id"]}],
        extra={"actions": actions, "workspace_run_id": workspace_envelope["run_id"]},
    )
    return emit_result(apply_envelope)


if __name__ == "__main__":
    raise SystemExit(main())
