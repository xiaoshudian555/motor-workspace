#!/usr/bin/env python3
"""K8s / MindCluster environment preflight (3+3 part-2 step 1)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_environment import (  # noqa: E402
    build_environment_result_envelope,
    load_environment_contract,
    run_environment_preflight_checks,
)
from mws_local_state import WorkspaceStateError, get_machine  # noqa: E402
from mws_parity import load_machine_ready_evidence  # noqa: E402
from mws_result import emit, progress, utc_now_iso  # noqa: E402
from mws_run_state import new_run_id, new_workflow_run_id, write_run  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alias", required=True, help="machine alias from inventory")
    parser.add_argument("--machine-run-id", default="", help="pin machine-ready run id")
    parser.add_argument(
        "--environment-contract",
        default="",
        help="optional environment contract path (defaults to skill reference)",
    )
    parser.add_argument("--workflow-run-id", default="", help="workflow run id for this 3+3 flow")
    parser.add_argument("--environment-run-id", default="", help="optional explicit environment run id")
    args = parser.parse_args()

    alias = require_safe_id(args.alias, label="alias")
    started_at = utc_now_iso()
    workflow_run_id = args.workflow_run_id.strip() or new_workflow_run_id()
    environment_run_id = args.environment_run_id.strip() or new_run_id("environment")

    try:
        machine = get_machine(alias)
        machine_ready = load_machine_ready_evidence(
            alias,
            machine_run_id=args.machine_run_id.strip() or None,
        )
        contract_path = Path(args.environment_contract) if args.environment_contract else None
        contract = load_environment_contract(contract_path)
    except WorkspaceStateError as exc:
        return emit(
            {
                "schema_version": "mws.result.v1",
                "kind": "deploy-environment-ready",
                "run_id": environment_run_id,
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

    progress("checking Kubernetes and MindCluster base environment")
    payload = run_environment_preflight_checks(
        machine=machine,
        machine_ready=machine_ready,
        contract=contract,
    )
    envelope = build_environment_result_envelope(
        run_id=environment_run_id,
        workflow_run_id=workflow_run_id,
        machine_run_id=str(machine_ready["machine_run_id"]),
        payload=payload,
        started_at=started_at,
    )
    if envelope["status"] == "ready":
        write_run(
            "deploy-environment-ready",
            environment_run_id,
            {
                **envelope,
                "alias": alias,
                "machine_run_id": machine_ready["machine_run_id"],
            },
        )
    return emit(envelope)


if __name__ == "__main__":
    raise SystemExit(main())
