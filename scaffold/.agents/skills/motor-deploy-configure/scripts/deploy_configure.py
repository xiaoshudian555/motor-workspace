#!/usr/bin/env python3
"""Generate or reuse immutable deploy config bundles (3+3 part-2 step 2)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from repo_paths import MOTOR_ROOT, REPO_ROOT, SCAFFOLD_ROOT  # noqa: E402

from mws_deploy import (  # noqa: E402
    compute_config_fingerprint,
    configure_deploy_bundle,
    deployer_version_token,
    normalize_native_config,
    resolve_deploy_base_image,
)
from mws_local_state import WorkspaceStateError, get_machine  # noqa: E402
from mws_lock import load_lock, verify_lock  # noqa: E402
from mws_machine_target import build_fixed_source_paths, resolve_machine  # noqa: E402
from mws_parity import load_machine_ready_evidence  # noqa: E402
from mws_result import build_result_envelope, emit, progress, utc_now_iso  # noqa: E402
from mws_run_state import (  # noqa: E402
    config_bundle_dir,
    load_run,
    new_run_id,
    run_dir_for_kind,
    write_run,
)
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", required=True)
    parser.add_argument("--environment-run-id", required=True)
    parser.add_argument("--machine-run-id", default="")
    parser.add_argument("--parity-run-id", required=True)
    parser.add_argument("--config-dir", default="")
    parser.add_argument("--workflow-run-id", default="")
    parser.add_argument("--config-run-id", default="")
    parser.add_argument("--reuse", action="store_true", help="Reuse existing bundle when fingerprint matches")
    parser.add_argument("--skip-npu-check", action="store_true", help="Skip the node NPU capacity check")
    args = parser.parse_args()

    alias = require_safe_id(args.machine, label="machine")
    workflow_run_id = args.workflow_run_id.strip() or "workflow-unset"
    config_run_id = args.config_run_id.strip() or new_run_id("config")
    started_at = utc_now_iso()
    config_dir = Path(args.config_dir) if args.config_dir else MOTOR_ROOT / "examples/infer_engines/vllm"

    try:
        machine = resolve_machine(alias)
        machine_ready = load_machine_ready_evidence(
            alias,
            machine_run_id=args.machine_run_id.strip() or None,
        )
        environment = load_run("deploy-environment-ready", args.environment_run_id)
        parity = load_run("parity-complete", args.parity_run_id)
        if environment.get("workflow_run_id") not in {None, workflow_run_id}:
            raise WorkspaceStateError("environment run belongs to another workflow")
        if parity.get("machine") not in {None, alias} and parity.get("alias") not in {None, alias}:
            raise WorkspaceStateError("parity run machine mismatch")
        kube_context = str(get_machine(alias).get("kube_context") or environment.get("kube_context") or "")
        lock = verify_lock(require_base_image=False, strict_commits=False)
        base_image_ref = resolve_deploy_base_image(config_dir, lock=load_lock())
    except WorkspaceStateError as exc:
        envelope = build_result_envelope(
            kind="deploy-config-ready",
            run_id=config_run_id,
            workflow_run_id=workflow_run_id,
            checks=[],
            started_at=started_at,
            errors=[str(exc)],
            status="failed",
        )
        return emit(envelope)

    run_dir = run_dir_for_kind("deploy-config-ready", config_run_id)
    parity_paths = build_fixed_source_paths(machine)
    reuse_bundle_dir = None
    if args.reuse:
        native_config = normalize_native_config(config_dir)
        fingerprint = compute_config_fingerprint(
            native_config=native_config,
            machine_paths=parity_paths,
            deployer_version=deployer_version_token(),
        )
        candidate = config_bundle_dir(fingerprint)
        if candidate.exists():
            reuse_bundle_dir = candidate
    progress("rendering or reusing deploy config bundle")
    result = configure_deploy_bundle(
        machine=machine,
        config_dir=config_dir,
        run_dir=run_dir,
        kube_context=kube_context,
        base_image_ref=base_image_ref,
        parity_path_refs=parity_paths,
        reuse_bundle_dir=reuse_bundle_dir,
        skip_npu_check=args.skip_npu_check,
    )
    envelope = build_result_envelope(
        kind="deploy-config-ready",
        run_id=config_run_id,
        workflow_run_id=workflow_run_id,
        checks=result.get("checks", []),
        started_at=started_at,
        upstream_refs=[
            {"kind": "deploy-environment-ready", "run_id": args.environment_run_id},
            {"kind": "machine-ready", "run_id": str(machine_ready["machine_run_id"])},
            {"kind": "parity-complete", "run_id": args.parity_run_id},
        ],
        warnings=result.get("warnings", []),
        errors=result.get("errors", []),
        extra={
            "machine": alias,
            "namespace": result.get("namespace"),
            "job_id": result.get("job_id"),
            "config_fingerprint": result.get("config_fingerprint"),
            "bundle_digest": result.get("bundle_digest"),
            "bundle_dir": result.get("bundle_dir"),
            "manifest_files": result.get("manifest_files", []),
            "reused": result.get("reused", False),
        },
    )
    if envelope["status"] == "ready":
        write_run(
            "deploy-config-ready",
            config_run_id,
            {
                **envelope,
                "machine": alias,
                "machine_run_id": machine_ready["machine_run_id"],
                "parity_run_id": args.parity_run_id,
                "environment_run_id": args.environment_run_id,
            },
        )
    return emit(envelope)


if __name__ == "__main__":
    raise SystemExit(main())
