#!/usr/bin/env python3
"""Parity sync + targeted restart for code-only updates."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LIB = ROOT / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_deploy import (  # noqa: E402
    collect_component_status,
    load_plan_from_dir,
    load_profile,
    openai_smoke,
    pod_readiness_probe,
    restart_deploy_workloads,
)
from mws_machine_target import pythonpath_for_machine, resolve_machine  # noqa: E402
from mws_result import emit, progress  # noqa: E402
from mws_run_state import deploy_run_dir, load_deploy_run, new_run_id  # noqa: E402
from mws_state import atomic_write_json  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def run_parity(machine: str) -> dict:
    script = ROOT / ".agents/skills/remote-code-parity/scripts/parity_sync.py"
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
    parser.add_argument("--profile", default="profiles/a2-dev.yaml")
    parser.add_argument("--skip-parity", action="store_true")
    parser.add_argument("--skip-restart", action="store_true")
    parser.add_argument("--openai-smoke", action="store_true")
    parser.add_argument("--approved-by-user", action="store_true")
    args = parser.parse_args()
    if not args.approved_by_user:
        return emit({"status": "error", "errors": ["restart requires --approved-by-user"]})
    alias = require_safe_id(args.machine, label="machine")
    machine = resolve_machine(alias)
    profile = load_profile(ROOT / args.profile)
    run_record = load_deploy_run(args.deploy_run_id)
    if run_record.get("machine") != alias:
        return emit({"status": "error", "errors": ["deploy run machine mismatch"]})
    plan_dir = run_record.get("plan_dir")
    if not plan_dir:
        return emit({"status": "error", "errors": ["plan_dir missing; deploy once before restart"]})
    plan = load_plan_from_dir(ROOT / plan_dir)
    namespace = plan.get("namespace", "")

    parity = {"status": "skipped", "reason": "--skip-parity"}
    if not args.skip_parity:
        progress("syncing code to remote fixed directories")
        parity = run_parity(alias)
        if parity.get("status") != "ok":
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
        restart = restart_deploy_workloads(plan, profile)
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

    pods = pod_readiness_probe(profile, namespace)
    components = collect_component_status(profile, namespace)
    smoke = openai_smoke(profile, namespace) if args.openai_smoke else {"status": "skipped"}
    ready = pods.get("ready") is True
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
        "components": components,
        "openai_smoke": smoke,
    }
    atomic_write_json(deploy_run_dir(args.deploy_run_id) / f"{restart_run_id}.json", payload)
    return emit(payload)


if __name__ == "__main__":
    raise SystemExit(main())
