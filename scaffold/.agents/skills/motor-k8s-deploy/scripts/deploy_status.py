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
    bundle_to_plan,
    collect_runtime_code_paths,
    load_config_bundle,
    pod_readiness_from_context,
    verify_min_service_access,
    verify_runtime_code_paths,
)
from mws_local_state import get_machine  # noqa: E402
from mws_machine_target import build_fixed_source_paths  # noqa: E402
from mws_result import build_result_envelope, emit_result, utc_now_iso  # noqa: E402
from mws_run_state import load_deploy_run  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", required=True)
    parser.add_argument("--deploy-run-id", required=True)
    args = parser.parse_args()
    started_at = utc_now_iso()
    status_run_id = f"status-{args.deploy_run_id}"

    def _fail(errors: list) -> int:
        envelope = build_result_envelope(
            kind="deploy-status",
            run_id=status_run_id,
            workflow_run_id="workflow-unset",
            checks=[],
            started_at=started_at,
            errors=errors,
            status="failed",
            extra={"machine": args.machine, "deploy_run_id": args.deploy_run_id},
        )
        return emit_result(envelope)

    alias = require_safe_id(args.machine, label="machine")
    machine = get_machine(alias)
    kube_context = str(machine.get("kube_context") or "")
    machine_paths = build_fixed_source_paths(machine)
    run_record = load_deploy_run(args.deploy_run_id)
    if run_record.get("machine") != alias:
        return _fail(["deploy run machine mismatch"])
    bundle_rel = run_record.get("bundle_dir")
    if not bundle_rel:
        return _fail(["bundle_dir missing"])
    bundle_ref = str(bundle_rel)
    bundle_dir = Path(bundle_ref)
    if not bundle_dir.is_absolute():
        bundle_dir = REPO_ROOT / bundle_ref
    bundle = load_config_bundle(bundle_dir)
    plan = bundle_to_plan(bundle_dir, bundle)
    namespace = str(run_record.get("namespace") or plan.get("namespace") or "")
    pods = pod_readiness_from_context(kube_context, namespace)
    min_access = verify_min_service_access(kube_context=kube_context, namespace=namespace)
    runtime_paths = collect_runtime_code_paths(kube_context=kube_context, namespace=namespace)
    code_paths = verify_runtime_code_paths(runtime_paths, machine_paths)
    ready = pods.get("ready") is True and code_paths.get("status") == "ok"
    checks = [
        {
            "name": "pod_readiness",
            "status": "ok" if pods.get("ready") is True else "warning",
            "message": str(pods),
        },
        {
            "name": "runtime_code_paths",
            "status": "ok" if code_paths.get("status") == "ok" else "warning",
            "message": code_paths.get("message", ""),
        },
    ]
    envelope = build_result_envelope(
        kind="deploy-status",
        run_id=status_run_id,
        workflow_run_id=str(run_record.get("workflow_run_id", "workflow-unset")),
        checks=checks,
        started_at=started_at,
        upstream_refs=[{"kind": "deploy-complete", "run_id": args.deploy_run_id}],
        status="ready" if ready else "failed",
        extra={
            "machine": alias,
            "deploy_run_id": args.deploy_run_id,
            "namespace": namespace,
            "job_id": plan.get("job_id"),
            "pods": pods,
            "min_service_access": min_access,
            "runtime_paths": runtime_paths,
            "code_paths": code_paths,
            "degraded": not ready,
        },
    )
    return emit_result(envelope)


if __name__ == "__main__":
    raise SystemExit(main())
