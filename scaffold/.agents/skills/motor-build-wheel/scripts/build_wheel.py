#!/usr/bin/env python3
"""Build a release-grade Motor wheel (protobuf + Rust) inside Docker (TD-P2-07)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from repo_paths import MOTOR_ROOT, REPO_ROOT, SCAFFOLD_ROOT  # noqa: E402

from mws_build import (  # noqa: E402
    build_motor_wheel_in_docker,
    build_wheel_run_envelope,
    detect_build_gaps,
    motor_source_root,
    render_wheel_replace_manifest,
)
from mws_lock import load_lock, resolve_base_image_ref, verify_lock  # noqa: E402
from mws_local_state import WorkspaceStateError, get_machine  # noqa: E402
from mws_machine_target import resolve_machine  # noqa: E402
from mws_result import emit, progress, utc_now_iso  # noqa: E402
from mws_run_state import load_run, new_run_id, write_run  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", required=True)
    parser.add_argument("--source-sha", default="")
    parser.add_argument("--base-image-ref", default="")
    parser.add_argument("--no-reuse", action="store_true", help="Force rebuild even when a wheel exists")
    parser.add_argument("--workflow-run-id", default="")
    parser.add_argument("--machine-run-id", default="")
    parser.add_argument("--config-run-id", default="")
    args = parser.parse_args()

    alias = require_safe_id(args.machine, label="machine")
    run_id = new_run_id("motor-wheel-build")
    started_at = utc_now_iso()
    workflow_run_id = args.workflow_run_id.strip() or "workflow-unset"

    try:
        machine = resolve_machine(alias)
        lock = verify_lock(require_base_image=False, strict_commits=False)
        base_image_ref = resolve_base_image_ref(
            lock=load_lock(),
            config_dir=MOTOR_ROOT / "examples" / "infer_engines" / "vllm",
            explicit=args.base_image_ref.strip() or None,
        )
        source_root = motor_source_root(machine)
        gaps = detect_build_gaps(source_root)

        progress(f"building motor wheel in docker for source_sha={args.source_sha}")
        build_result = build_motor_wheel_in_docker(
            machine=machine,
            base_image_ref=base_image_ref,
            source_sha=args.source_sha or _fallback_source_sha(machine),
            reuse=not args.no_reuse,
        )
    except WorkspaceStateError as exc:
        return emit(
            {
                "schema_version": "mws.result.v1",
                "kind": "motor-wheel-build",
                "run_id": run_id,
                "workflow_run_id": workflow_run_id,
                "status": "failed",
                "started_at": started_at,
                "finished_at": utc_now_iso(),
                "upstream_refs": [],
                "checks": [],
                "warnings": [],
                "errors": [str(exc)],
                "artifacts": [],
            }
        )

    envelope = build_wheel_run_envelope(
        run_id=run_id,
        workflow_run_id=workflow_run_id,
        build_result=build_result,
        started_at=started_at,
        upstream_refs=(
            [{"kind": "machine-ready", "run_id": args.machine_run_id}]
            if args.machine_run_id
            else []
        ),
    )
    if envelope["status"] == "ready":
        write_run("motor-wheel-build", run_id, {**envelope, "machine": alias})
    return emit(envelope)


def _fallback_source_sha(machine: dict) -> str:
    """Resolve the motor source sha from the lock or fail closed."""
    from mws_lock import git_head

    root = REPO_ROOT / "sources" / "motor"
    if (root / ".git").exists():
        try:
            return git_head(root)
        except WorkspaceStateError:
            pass
    raise WorkspaceStateError(
        "--source-sha is required when motor source sha cannot be resolved locally"
    )


if __name__ == "__main__":
    raise SystemExit(main())
