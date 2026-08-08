#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_result import build_result_envelope, emit_result, utc_now_iso  # noqa: E402
from mws_run_state import load_deploy_run  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the upstream deploy for a future Motor benchmark."
    )
    parser.add_argument("--machine", required=True)
    parser.add_argument("--deploy-run-id", required=True)
    parser.add_argument("--benchmark-run-id", default="")
    args = parser.parse_args()
    started_at = utc_now_iso()
    alias = require_safe_id(args.machine, label="machine")
    deploy_run_id = require_safe_id(args.deploy_run_id, label="deploy_run_id")
    run_id = require_safe_id(
        args.benchmark_run_id.strip() or f"bench-plan-{deploy_run_id}",
        label="benchmark_run_id",
    )

    try:
        run_record = load_deploy_run(deploy_run_id, allow_failed=False)
    except Exception as exc:  # noqa: BLE001
        envelope = build_result_envelope(
            kind="benchmark-plan",
            run_id=run_id,
            workflow_run_id="workflow-unset",
            checks=[],
            started_at=started_at,
            errors=[str(exc)],
            status="failed",
            extra={"machine": alias, "deploy_run_id": deploy_run_id},
        )
        return emit_result(envelope)

    if run_record.get("machine") != alias:
        envelope = build_result_envelope(
            kind="benchmark-plan",
            run_id=run_id,
            workflow_run_id=str(run_record.get("workflow_run_id", "workflow-unset")),
            checks=[],
            started_at=started_at,
            errors=["deploy run machine mismatch"],
            status="failed",
            extra={"machine": alias, "deploy_run_id": deploy_run_id},
        )
        return emit_result(envelope)

    envelope = build_result_envelope(
        kind="benchmark-plan",
        run_id=run_id,
        workflow_run_id=str(run_record.get("workflow_run_id", "workflow-unset")),
        checks=[
            {
                "name": "deploy_context",
                "status": "ok",
                "message": (
                    "ready deploy run and machine binding validated; "
                    "benchmark workload has not been executed"
                ),
            }
        ],
        started_at=started_at,
        upstream_refs=[{"kind": "deploy-complete", "run_id": deploy_run_id}],
        status="ready",
        extra={
            "machine": alias,
            "deploy_run_id": deploy_run_id,
            "config_run_id": run_record.get("config_run_id"),
            "bundle_dir": run_record.get("bundle_dir"),
            "namespace": run_record.get("namespace"),
            "implementation_status": "upstream_validated_only",
            "next": "follow motor-benchmark/references/aisbench.md",
        },
    )
    return emit_result(envelope)


if __name__ == "__main__":
    raise SystemExit(main())
