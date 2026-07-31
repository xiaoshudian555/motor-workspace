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
    bundle_to_plan,
    collect_runtime_code_paths,
    load_config_bundle,
    pod_readiness_from_context,
    restart_deploy_workloads_from_context,
    verify_runtime_code_paths,
)
from mws_local_state import get_machine  # noqa: E402
from mws_machine_target import build_fixed_source_paths, pythonpath_for_machine, resolve_machine  # noqa: E402
from mws_result import emit, progress  # noqa: E402
from mws_run_state import deploy_run_dir, load_deploy_run, new_run_id  # noqa: E402
from mws_state import atomic_write_json  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def run_parity(machine: str) -> dict:
    script = SCAFFOLD_ROOT / ".agents/skills/remote-code-parity/scripts/parity_sync.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--machine",
            machine,
            "--approved-overwrite",
        ],
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
    args = parser.parse_args()
    if not args.approved_by_user:
        return emit({"status": "error", "errors": ["restart requires --approved-by-user"]})
    alias = require_safe_id(args.machine, label="machine")
    machine = resolve_machine(alias)
    kube_context = str(machine.get("kube_context") or "")
    machine_paths = build_fixed_source_paths(machine)
    run_record = load_deploy_run(args.deploy_run_id)
    if run_record.get("machine") != alias:
        return emit({"status": "error", "errors": ["deploy run machine mismatch"]})
    bundle_rel = run_record.get("bundle_dir")
    if not bundle_rel:
        return emit({"status": "error", "errors": ["bundle_dir missing; deploy once before restart"]})
    bundle_ref = str(bundle_rel)
    bundle_dir = Path(bundle_ref)
    if not bundle_dir.is_absolute():
        bundle_dir = REPO_ROOT / bundle_ref
    bundle = load_config_bundle(bundle_dir)
    plan = bundle_to_plan(bundle_dir, bundle)
    namespace = str(run_record.get("namespace") or plan.get("namespace") or "")

    parity = {"status": "skipped", "reason": "--skip-parity"}
    if not args.skip_parity:
        progress("syncing code to remote fixed directories")
        parity = run_parity(alias)
        if parity.get("status") not in {"ok", "ready"}:
            return emit(
                {
                    "status": "error",
                    "machine": alias,
                    "deploy_run_id": args.deploy_run_id,
                    "phase": "parity",
                    "errors": parity.get("errors", []),
                }
            )

    restart = {"status": "skipped", "reason": "--skip-restart"}
    if not args.skip_restart:
        progress("restarting deploy-scoped workloads")
        restart = restart_deploy_workloads_from_context(plan, kube_context=kube_context)
        if restart.get("status") != "ok":
            return emit(
                {
                    "status": "error",
                    "machine": alias,
                    "deploy_run_id": args.deploy_run_id,
                    "phase": "restart",
                    "parity": parity,
                    "restart": restart,
                }
            )

    pods = pod_readiness_from_context(kube_context, namespace)
    runtime_paths = collect_runtime_code_paths(kube_context=kube_context, namespace=namespace)
    code_paths = verify_runtime_code_paths(runtime_paths, machine_paths)
    ready = pods.get("ready") is True and code_paths.get("status") == "ok"
    overall = "ok" if ready else "error"
    restart_run_id = new_run_id("restart")
    payload = {
        "status": overall,
        "restart_run_id": restart_run_id,
        "machine": alias,
        "deploy_run_id": args.deploy_run_id,
        "workflow": "deploy_restart",
        "pythonpath": pythonpath_for_machine(machine),
        "parity": parity,
        "restart": restart,
        "pods": pods,
        "runtime_paths": runtime_paths,
        "code_paths": code_paths,
    }
    atomic_write_json(deploy_run_dir(args.deploy_run_id) / f"{restart_run_id}.json", payload)
    return emit(payload)


if __name__ == "__main__":
    raise SystemExit(main())
