#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from repo_paths import REPO_ROOT, SCAFFOLD_ROOT  # noqa: E402

from mws_deploy import collect_component_status, load_profile, openai_smoke, pod_readiness_probe  # noqa: E402
from mws_deploy import load_plan_from_dir  # noqa: E402
from mws_result import emit  # noqa: E402
from mws_run_state import load_deploy_run  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", required=True)
    parser.add_argument("--deploy-run-id", required=True)
    parser.add_argument("--profile", default="profiles/a2-dev.yaml")
    parser.add_argument("--openai-smoke", action="store_true")
    args = parser.parse_args()
    alias = require_safe_id(args.machine, label="machine")
    run_record = load_deploy_run(args.deploy_run_id)
    if run_record.get("machine") != alias:
        return emit({"status": "error", "errors": ["deploy run machine mismatch"]})
    plan_dir = run_record.get("plan_dir")
    if not plan_dir:
        return emit({"status": "error", "errors": ["plan_dir missing"]})
    plan = load_plan_from_dir(REPO_ROOT / plan_dir)
    profile = load_profile(SCAFFOLD_ROOT / args.profile)
    namespace = plan.get("namespace", "")
    pods = pod_readiness_probe(profile, namespace)
    components = collect_component_status(profile, namespace)
    smoke = openai_smoke(profile, namespace) if args.openai_smoke else {"status": "skipped"}
    ready = pods.get("ready") is True
    status = "ok" if ready else "warning"
    return emit(
        {
            "status": status,
            "machine": alias,
            "deploy_run_id": args.deploy_run_id,
            "namespace": namespace,
            "job_id": plan.get("job_id"),
            "pods": pods,
            "components": components,
            "openai_smoke": smoke,
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
