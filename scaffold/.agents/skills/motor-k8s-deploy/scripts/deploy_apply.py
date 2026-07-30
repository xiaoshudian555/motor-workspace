#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from repo_paths import REPO_ROOT, SCAFFOLD_ROOT  # noqa: E402

from mws_deploy import apply_from_plan, load_plan_from_dir, load_profile  # noqa: E402
from mws_lock import verify_lock  # noqa: E402
from mws_result import emit, progress  # noqa: E402
from mws_run_state import deploy_run_dir, load_deploy_run  # noqa: E402
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
        return emit({"status": "error", "errors": ["apply requires --approved-by-user"]})
    alias = require_safe_id(args.machine, label="machine")
    lock = verify_lock(require_base_image=False, strict_commits=False)
    run_record = load_deploy_run(args.deploy_run_id)
    if run_record.get("machine") != alias:
        return emit({"status": "error", "errors": ["deploy run machine mismatch"]})
    plan_dir = args.plan_dir or run_record.get("plan_dir")
    if not plan_dir:
        return emit({"status": "error", "errors": ["plan_dir missing in deploy run"]})
    plan = load_plan_from_dir(REPO_ROOT / plan_dir)
    profile = load_profile(SCAFFOLD_ROOT / args.profile)
    progress("applying approved plan")
    result = apply_from_plan(plan, profile)
    payload = {
        "status": result["status"],
        "deploy_run_id": args.deploy_run_id,
        "machine": alias,
        "plan_dir": plan_dir,
        "namespace": plan.get("namespace"),
        "apply": result,
        "lock_warnings": lock.get("warnings", []),
    }
    atomic_write_json(deploy_run_dir(args.deploy_run_id) / "apply.json", payload)
    return emit(payload)


if __name__ == "__main__":
    raise SystemExit(main())
