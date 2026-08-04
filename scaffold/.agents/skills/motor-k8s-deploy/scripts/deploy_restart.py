#!/usr/bin/env python3
"""Parity sync + targeted restart for code-only updates."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from repo_paths import REPO_ROOT, SCAFFOLD_ROOT  # noqa: E402

from mws_deploy import (  # noqa: E402
    DEFAULT_ROLLOUT_TIMEOUT_S,
    bundle_to_plan,
    collect_runtime_code_paths,
    load_config_bundle,
    restart_deploy_workloads_from_context,
    verify_runtime_code_paths,
    wait_workload_rollouts_from_context,
)
from mws_local_state import get_machine  # noqa: E402
from mws_machine_target import build_fixed_source_paths, pythonpath_for_machine, resolve_machine  # noqa: E402
from mws_result import (  # noqa: E402
    CheckRunner,
    build_result_envelope,
    emit_result,
    progress,
    utc_now_iso,
)
from mws_run_state import deploy_run_dir, load_deploy_run, new_run_id  # noqa: E402
from mws_state import atomic_write_json  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def run_parity(machine: str, *, machine_run_id: str = "") -> dict:
    """Run the parity step appropriate for the machine topology.

    SSH machines sync the local dirty tree to fixed remote directories
    (`parity_sync.py`). Remote-native machines are already on the target host,
    so parity only proves identity (`parity_identity.py`) without any copy or
    overwrite, and never initiates a self-SSH.
    """
    alias = require_safe_id(machine, label="machine")
    record = get_machine(alias)
    if record.get("executor") == "native":
        script = SCAFFOLD_ROOT / ".agents/skills/remote-code-parity/scripts/parity_identity.py"
        cmd = [sys.executable, str(script), "--machine", machine]
    else:
        script = SCAFFOLD_ROOT / ".agents/skills/remote-code-parity/scripts/parity_sync.py"
        cmd = [
            sys.executable,
            str(script),
            "--machine",
            machine,
            "--approved-overwrite",
        ]
    if machine_run_id.strip():
        cmd.extend(["--machine-run-id", machine_run_id.strip()])
    result = subprocess.run(
        cmd,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"status": "error", "errors": [result.stderr.strip() or result.stdout.strip()]}
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", required=True)
    parser.add_argument("--deploy-run-id", required=True)
    parser.add_argument("--skip-parity", action="store_true")
    parser.add_argument("--skip-restart", action="store_true")
    parser.add_argument("--approved-by-user", action="store_true")
    parser.add_argument(
        "--rollout-timeout",
        type=float,
        default=DEFAULT_ROLLOUT_TIMEOUT_S,
        help="Seconds to wait per deploy-scoped Deployment/StatefulSet rollout",
    )
    args = parser.parse_args()
    if args.rollout_timeout <= 0:
        envelope = build_result_envelope(
            kind="deploy-restart",
            run_id=new_run_id("restart"),
            workflow_run_id="workflow-unset",
            checks=[],
            started_at=utc_now_iso(),
            errors=["--rollout-timeout must be positive"],
            status="failed",
            extra={"machine": args.machine, "deploy_run_id": args.deploy_run_id},
        )
        return emit_result(envelope)
    started_at = utc_now_iso()
    restart_run_id = new_run_id("restart")

    def _fail(errors: list, *, extra: dict | None = None) -> int:
        envelope = build_result_envelope(
            kind="deploy-restart",
            run_id=restart_run_id,
            workflow_run_id=str(run_record.get("workflow_run_id", "workflow-unset")) if run_record else "workflow-unset",
            checks=[],
            started_at=started_at,
            errors=errors,
            status="failed",
            extra={"machine": alias, "deploy_run_id": args.deploy_run_id, **(extra or {})},
        )
        return emit_result(envelope)

    run_record = None
    if not args.approved_by_user:
        envelope = build_result_envelope(
            kind="deploy-restart",
            run_id=restart_run_id,
            workflow_run_id="workflow-unset",
            checks=[],
            started_at=started_at,
            errors=["restart requires --approved-by-user"],
            status="failed",
            extra={"machine": args.machine, "deploy_run_id": args.deploy_run_id},
        )
        return emit_result(envelope)
    alias = require_safe_id(args.machine, label="machine")
    machine = resolve_machine(alias)
    kube_context = str(machine.get("kube_context") or "")
    machine_paths = build_fixed_source_paths(machine)
    run_record = load_deploy_run(args.deploy_run_id)
    if run_record.get("machine") != alias:
        return _fail(["deploy run machine mismatch"])
    bundle_rel = run_record.get("bundle_dir")
    if not bundle_rel:
        return _fail(["bundle_dir missing; deploy once before restart"])
    bundle_ref = str(bundle_rel)
    bundle_dir = Path(bundle_ref)
    if not bundle_dir.is_absolute():
        bundle_dir = REPO_ROOT / bundle_ref
    bundle = load_config_bundle(bundle_dir)
    plan = bundle_to_plan(bundle_dir, bundle)
    namespace = str(run_record.get("namespace") or plan.get("namespace") or "")

    runner = CheckRunner()
    parity = {"status": "skipped", "reason": "--skip-parity"}
    if not args.skip_parity:
        machine_run_id = str(run_record.get("machine_run_id") or "")
        if not machine_run_id:
            return _fail(
                [
                    "deploy run missing machine_run_id; cannot run parity without "
                    "machine-ready evidence (re-run deploy_apply or use --skip-parity)"
                ],
                extra={"phase": "parity"},
            )
        progress("syncing code to remote fixed directories")
        parity = run_parity(alias, machine_run_id=machine_run_id)
        parity_ok = parity.get("status") in {"ok", "ready"}
        runner.append(
            {
                "name": "parity",
                "status": "ok" if parity_ok else "error",
                "message": "; ".join(parity.get("errors", [])) or parity.get("status", ""),
            }
        )
        if not runner.continue_ok:
            return _fail(runner.errors, extra={"phase": "parity", "parity": parity})

    restart = {"status": "skipped", "reason": "--skip-restart"}
    if not args.skip_restart:
        progress("restarting deploy-scoped workloads")
        restart = restart_deploy_workloads_from_context(
            plan,
            machine=machine,
            kube_context=kube_context,
        )
        restart_ok = restart.get("status") == "ok"
        runner.append(
            {
                "name": "restart",
                "status": "ok" if restart_ok else "error",
                "message": "; ".join(restart.get("errors", [])) or restart.get("status", ""),
            }
        )
        if not runner.continue_ok:
            return _fail(
                runner.errors,
                extra={"phase": "restart", "parity": parity, "restart": restart},
            )

    progress("waiting for deploy-scoped workload rollouts")
    rollout = wait_workload_rollouts_from_context(
        machine,
        kube_context,
        namespace,
        list(plan.get("workload_names") or []),
        timeout=args.rollout_timeout,
    )
    runtime_paths = collect_runtime_code_paths(
        machine=machine,
        kube_context=kube_context,
        namespace=namespace,
    )
    code_paths = verify_runtime_code_paths(runtime_paths, machine_paths)
    ready = rollout.get("ready") is True and code_paths.get("status") == "ok"
    runner.append(
        {
            "name": "workload_rollout",
            "status": "ok" if rollout.get("ready") is True else "error",
            "message": rollout.get("error") or str(rollout),
        }
    )
    runner.append(code_paths)
    envelope = build_result_envelope(
        kind="deploy-restart",
        run_id=restart_run_id,
        workflow_run_id=str(run_record.get("workflow_run_id", "workflow-unset")),
        checks=runner.checks,
        started_at=started_at,
        warnings=runner.warnings,
        errors=runner.errors,
        upstream_refs=[{"kind": "deploy-complete", "run_id": args.deploy_run_id}],
        status="ready" if ready and runner.continue_ok else "failed",
        extra={
            "machine": alias,
            "deploy_run_id": args.deploy_run_id,
            "workflow": "deploy_restart",
            "pythonpath": pythonpath_for_machine(machine),
            "parity": parity,
            "restart": restart,
            "rollout": rollout,
            "runtime_paths": runtime_paths,
            "code_paths": code_paths,
        },
    )
    atomic_write_json(deploy_run_dir(args.deploy_run_id) / f"{restart_run_id}.json", envelope)
    return emit_result(envelope)


if __name__ == "__main__":
    raise SystemExit(main())
