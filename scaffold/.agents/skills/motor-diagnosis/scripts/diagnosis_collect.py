#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from repo_paths import REPO_ROOT  # noqa: E402

from mws_diagnosis import resolve_diagnosis_context  # noqa: E402
from mws_kubectl import build_kubectl_runner  # noqa: E402
from mws_local_state import WorkspaceStateError, get_machine, utc_now_iso  # noqa: E402
from mws_result import build_result_envelope, emit_result, progress, utc_now_iso as result_now  # noqa: E402
from mws_run_state import validation_run_dir  # noqa: E402
from mws_state import atomic_write_json  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", required=True)
    parser.add_argument("--deploy-run-id", required=True)
    parser.add_argument("--diagnosis-run-id", default="")
    args = parser.parse_args()
    alias = require_safe_id(args.machine, label="machine")
    started_at = result_now()
    diagnosis_run_id = args.diagnosis_run_id.strip() or f"diag-{uuid.uuid4().hex[:8]}"

    try:
        context = resolve_diagnosis_context(
            machine_alias=alias,
            deploy_run_id=args.deploy_run_id,
        )
    except WorkspaceStateError as exc:
        envelope = build_result_envelope(
            kind="deploy-diagnosis",
            run_id=diagnosis_run_id,
            workflow_run_id="workflow-unset",
            checks=[],
            started_at=started_at,
            errors=[str(exc)],
            status="failed",
            extra={"machine": alias, "deploy_run_id": args.deploy_run_id},
        )
        return emit_result(envelope)

    machine = get_machine(alias)
    kubectl = build_kubectl_runner(machine, kube_context=context["kube_context"])
    namespace = context["namespace"]
    out_dir = validation_run_dir(diagnosis_run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    progress("collecting pod and event evidence")
    artifacts: list[str] = []
    for name, kubectl_args in {
        "pods": ("get", "pods", "-n", namespace, "-o", "json"),
        "events": ("get", "events", "-n", namespace, "--sort-by=.lastTimestamp", "-o", "json"),
    }.items():
        result = kubectl(*kubectl_args)
        path = out_dir / f"{name}.json"
        path.write_text(result.stdout or result.stderr, encoding="utf-8")
        artifacts.append(str(path.relative_to(REPO_ROOT)))

    context_path = out_dir / "context.json"
    atomic_write_json(context_path, context)
    artifacts.append(str(context_path.relative_to(REPO_ROOT)))

    manifest = {
        "schema_version": 1,
        "machine": alias,
        "deploy_run_id": args.deploy_run_id,
        "config_run_id": context["config_run_id"],
        "bundle_dir": context["bundle_dir"],
        "bundle_digest": context["bundle_digest"],
        "namespace": namespace,
        "kube_context": context["kube_context"],
        "workload_names": context["workload_names"],
        "validation_run_id": diagnosis_run_id,
        "artifacts": artifacts,
        "collected_at": utc_now_iso(),
    }
    manifest_path = out_dir / "manifest.json"
    atomic_write_json(manifest_path, manifest)
    artifacts.append(str(manifest_path.relative_to(REPO_ROOT)))

    envelope = build_result_envelope(
        kind="deploy-diagnosis",
        run_id=diagnosis_run_id,
        workflow_run_id="workflow-unset",
        checks=[
            {
                "name": "diagnosis_context",
                "status": "ok",
                "message": "deploy/config/bundle context resolved",
            }
        ],
        started_at=started_at,
        upstream_refs=[{"kind": "deploy-complete", "run_id": args.deploy_run_id}],
        artifacts=[{"path": item} for item in artifacts],
        extra={
            "machine": alias,
            "deploy_run_id": args.deploy_run_id,
            "config_run_id": context["config_run_id"],
            "bundle_dir": context["bundle_dir"],
            "namespace": namespace,
            "validation_run_dir": str(out_dir.relative_to(REPO_ROOT)),
        },
    )
    return emit_result(envelope)


if __name__ == "__main__":
    raise SystemExit(main())
