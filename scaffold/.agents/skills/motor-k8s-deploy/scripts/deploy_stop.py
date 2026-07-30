#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from repo_paths import REPO_ROOT, SCAFFOLD_ROOT  # noqa: E402

from mws_deploy import load_plan_from_dir, load_profile, stop_from_plan  # noqa: E402
from mws_result import emit, progress  # noqa: E402
from mws_run_state import load_deploy_run  # noqa: E402
from mws_state import atomic_write_json  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", required=True)
    parser.add_argument("--deploy-run-id", required=True)
    parser.add_argument("--profile", default="profiles/a2-dev.yaml")
    parser.add_argument("--approved-by-user", action="store_true")
    args = parser.parse_args()
    if not args.approved_by_user:
        return emit({"status": "error", "errors": ["stop requires --approved-by-user"]})
    alias = require_safe_id(args.machine, label="machine")
    run_record = load_deploy_run(args.deploy_run_id)
    if run_record.get("machine") != alias:
        return emit({"status": "error", "errors": ["deploy run machine mismatch"]})
    plan_dir = run_record.get("plan_dir")
    if not plan_dir:
        return emit({"status": "error", "errors": ["plan_dir missing; nothing to stop"]})
    plan = load_plan_from_dir(REPO_ROOT / plan_dir)
    profile = load_profile(SCAFFOLD_ROOT / args.profile)
    progress("stopping run-scoped deployment")
    result = stop_from_plan(plan, profile)
    payload = {
        "status": result["status"],
        "deploy_run_id": args.deploy_run_id,
        "machine": alias,
        "stop": result,
    }
    atomic_write_json(REPO_ROOT / ".motor-workspace-local" / "deploy-runs" / args.deploy_run_id / "stop.json", payload)
    return emit(payload)


if __name__ == "__main__":
    raise SystemExit(main())
