#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from repo_paths import REPO_ROOT  # noqa: E402

from mws_deploy import (  # noqa: E402
    DEFAULT_ROLLOUT_TIMEOUT_S,
    apply_config_bundle,
    collect_runtime_code_paths,
    load_config_bundle,
    verify_bundle_digest,
    verify_min_service_access,
    verify_runtime_code_paths,
    wait_workload_rollouts_from_context,
)
from mws_local_state import WorkspaceStateError, get_machine  # noqa: E402
from mws_machine_target import build_fixed_source_paths  # noqa: E402
from mws_parity import load_machine_ready_evidence  # noqa: E402
from mws_result import CheckRunner, build_result_envelope, emit, progress, utc_now_iso  # noqa: E402
from mws_run_state import deploy_run_dir, load_run, new_run_id, relative_repo, write_deploy_run  # noqa: E402
from mws_state import atomic_write_json  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", required=True)
    parser.add_argument("--config-run-id", required=True)
    parser.add_argument("--machine-run-id", default="")
    parser.add_argument("--approved-by-user", action="store_true")
    parser.add_argument("--deploy-run-id", default="")
    parser.add_argument(
        "--rollout-timeout",
        type=float,
        default=DEFAULT_ROLLOUT_TIMEOUT_S,
        help="Seconds to wait per deploy-scoped Deployment/StatefulSet rollout",
    )
    args = parser.parse_args()
    if args.rollout_timeout <= 0:
        return emit({"status": "error", "errors": ["--rollout-timeout must be positive"]})
    if not args.approved_by_user:
        return emit({"status": "error", "errors": ["apply requires --approved-by-user"]})

    alias = require_safe_id(args.machine, label="machine")
    deploy_run_id = args.deploy_run_id.strip() or new_run_id("deploy")
    started_at = utc_now_iso()
    runner = CheckRunner()

    try:
        config_run = load_run("deploy-config-ready", args.config_run_id)
        machine = get_machine(alias)
        machine_ready = load_machine_ready_evidence(
            alias,
            machine_run_id=args.machine_run_id.strip() or None,
        )
        bundle_ref = str(config_run.get("bundle_dir", ""))
        bundle_dir = Path(bundle_ref)
        if not bundle_dir.is_absolute():
            bundle_dir = REPO_ROOT / bundle_ref
        bundle = load_config_bundle(bundle_dir)
        verify_bundle_digest(bundle_dir, str(config_run.get("bundle_digest", "")))
        machine_paths = build_fixed_source_paths(machine)
        stored_paths = bundle.get("machine_paths", {})
        if stored_paths and stored_paths != machine_paths:
            raise WorkspaceStateError("bundle fixed path mapping does not match current machine")
        namespace = str(config_run.get("namespace") or bundle.get("namespace") or "")
        kube_context = str(machine.get("kube_context") or "")
    except WorkspaceStateError as exc:
        envelope = build_result_envelope(
            kind="deploy-complete",
            run_id=deploy_run_id,
            workflow_run_id="workflow-unset",
            checks=[],
            started_at=started_at,
            errors=[str(exc)],
            status="failed",
        )
        return emit(envelope)

    progress("applying immutable config bundle")
    apply_result = apply_config_bundle(
        bundle_dir=bundle_dir,
        machine=machine,
        kube_context=kube_context,
        namespace=namespace,
    )
    runner.append(
        {
            "name": "apply",
            "status": "ok" if apply_result.get("status") == "ok" else "error",
            "message": apply_result.get("status", "error"),
        }
    )
    if not runner.continue_ok:
        log_collection = (
            apply_result.get("upstream_deploy", {}).get("log_collection", {})
            if isinstance(apply_result.get("upstream_deploy"), dict)
            else {}
        )
        envelope = build_result_envelope(
            kind="deploy-complete",
            run_id=deploy_run_id,
            workflow_run_id=str(config_run.get("workflow_run_id", "workflow-unset")),
            checks=runner.checks,
            started_at=started_at,
            upstream_refs=[{"kind": "deploy-config-ready", "run_id": args.config_run_id}],
            errors=runner.errors,
            extra={
                "machine": alias,
                "config_run_id": args.config_run_id,
                "namespace": namespace,
                "bundle_digest": config_run.get("bundle_digest"),
                "bundle_dir": relative_repo(bundle_dir),
                "apply": apply_result,
                "log_collection": log_collection,
            },
            status="failed",
        )
        write_deploy_run(
            deploy_run_id,
            {
                **envelope,
                "machine": alias,
                "config_run_id": args.config_run_id,
                "bundle_dir": relative_repo(bundle_dir),
            },
        )
        atomic_write_json(deploy_run_dir(deploy_run_id) / "apply.json", envelope)
        return emit(envelope)

    workload_names = list(bundle.get("workload_names") or [])
    progress("waiting for deploy-scoped workload rollouts")
    rollout = wait_workload_rollouts_from_context(
        machine,
        kube_context,
        namespace,
        workload_names,
        timeout=args.rollout_timeout,
    )
    runner.append(
        {
            "name": "workload_rollout",
            "status": "ok" if rollout.get("ready") else "error",
            "message": rollout.get("error") or str(rollout),
        }
    )
    if runner.continue_ok:
        min_access = verify_min_service_access(
            machine=machine,
            kube_context=kube_context,
            namespace=namespace,
        )
        runner.append(min_access)

    runtime_paths = {"status": "skipped", "paths": {}}
    code_path_check = {"name": "runtime_code_paths", "status": "skipped", "message": "not reached"}
    if runner.continue_ok:
        runtime_paths = collect_runtime_code_paths(
            machine=machine,
            kube_context=kube_context,
            namespace=namespace,
        )
        if runtime_paths.get("status") == "unavailable":
            runner.append(
                {
                    "name": "runtime_code_paths",
                    "status": "unavailable",
                    "message": runtime_paths.get("reason", "unavailable"),
                }
            )
        elif runtime_paths.get("status") != "ok":
            runner.append(
                {
                    "name": "runtime_code_paths",
                    "status": "error",
                    "message": runtime_paths.get("reason", "collection failed"),
                }
            )
        else:
            code_path_check = verify_runtime_code_paths(runtime_paths, machine_paths)
            runner.append(code_path_check)

    status = "ready" if runner.continue_ok else "failed"
    log_collection = (
        apply_result.get("upstream_deploy", {}).get("log_collection", {})
        if isinstance(apply_result.get("upstream_deploy"), dict)
        else {}
    )
    envelope = build_result_envelope(
        kind="deploy-complete",
        run_id=deploy_run_id,
        workflow_run_id=str(config_run.get("workflow_run_id", "workflow-unset")),
        checks=runner.checks,
        started_at=started_at,
        upstream_refs=[{"kind": "deploy-config-ready", "run_id": args.config_run_id}],
        warnings=runner.warnings,
        errors=runner.errors,
        extra={
            "machine": alias,
            "config_run_id": args.config_run_id,
            "machine_run_id": machine_ready.get("machine_run_id"),
            "namespace": namespace,
            "bundle_digest": config_run.get("bundle_digest"),
            "bundle_dir": relative_repo(bundle_dir),
            "apply": apply_result,
            "log_collection": log_collection,
            "rollout": rollout,
            "workload_names": workload_names,
            "runtime_paths": runtime_paths,
            "validation_note": (
                "deploy-complete ready means apply + rollout + runtime_code_paths; "
                "Coordinator service readiness is validated by motor-smoke"
            ),
            "code_paths": code_path_check,
        },
        status=status,
    )
    write_deploy_run(
        deploy_run_id,
        {
            **envelope,
            "machine": alias,
            "config_run_id": args.config_run_id,
            "bundle_dir": relative_repo(bundle_dir),
        },
    )
    atomic_write_json(deploy_run_dir(deploy_run_id) / "apply.json", envelope)
    return emit(envelope)


if __name__ == "__main__":
    raise SystemExit(main())
