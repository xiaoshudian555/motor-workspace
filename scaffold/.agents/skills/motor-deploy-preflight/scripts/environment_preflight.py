#!/usr/bin/env python3
"""K8s / MindCluster environment preflight (3+3 part-2 step 2)."""

from __future__ import annotations

import argparse
import json
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
    parser.add_argument(
        "--config-dir",
        default="",
        help="Motor native config directory containing user_config.json; "
        "deploy_mode is read from it to select the workload-specific check set",
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
        deploy_mode = _read_deploy_mode(args.config_dir)
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
        deploy_mode=deploy_mode,
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
                "deploy_mode": deploy_mode,
            },
        )
    return emit(envelope)


def _read_deploy_mode(config_dir: str) -> str | None:
    """Read motor_deploy_config.deploy_mode from the native config directory.

    The config is generated before preflight in the 3+3 flow (motor-config-edit),
    so when a config directory is supplied preflight adapts its workload check
    set to the deploy mode. Returns None when no config directory is given
    (base environment check only). Fails closed on a missing/invalid config.
    """
    if not config_dir:
        return None
    path = Path(config_dir) / "user_config.json"
    if not path.exists():
        raise WorkspaceStateError(f"--config-dir given but user_config.json not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkspaceStateError(f"user_config.json is not valid JSON: {path}") from exc
    deploy = data.get("motor_deploy_config", {})
    if not isinstance(deploy, dict):
        raise WorkspaceStateError(f"motor_deploy_config missing in {path}")
    mode = deploy.get("deploy_mode")
    if mode is None:
        return "infer_service_set"
    mode = str(mode)
    if mode not in ("infer_service_set", "multi_deployment", "single_container"):
        raise WorkspaceStateError(
            f"motor_deploy_config.deploy_mode must be one of "
            f"infer_service_set/multi_deployment/single_container, got: {mode!r}"
        )
    return mode


if __name__ == "__main__":
    raise SystemExit(main())
