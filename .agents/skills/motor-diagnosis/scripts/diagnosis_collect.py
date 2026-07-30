#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LIB = ROOT / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_deploy import kubectl_base, load_profile, load_plan_from_dir  # noqa: E402
from mws_result import emit, progress  # noqa: E402
from mws_run_state import load_deploy_run, validation_run_dir  # noqa: E402
from mws_state import atomic_write_json  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", required=True)
    parser.add_argument("--deploy-run-id", required=True)
    parser.add_argument("--profile", default="profiles/a2-dev.yaml")
    args = parser.parse_args()
    alias = require_safe_id(args.machine, label="machine")
    run_record = load_deploy_run(args.deploy_run_id)
    if run_record.get("machine") != alias:
        return emit({"status": "error", "errors": ["deploy run machine mismatch"]})
    plan_dir = run_record.get("plan_dir")
    if not plan_dir:
        return emit({"status": "error", "errors": ["plan_dir missing"]})
    plan = load_plan_from_dir(ROOT / plan_dir)
    namespace = plan.get("namespace", "")
    profile = load_profile(ROOT / args.profile)
    kubectl = kubectl_base(profile)
    run_id = f"diag-{uuid.uuid4().hex[:8]}"
    out_dir = validation_run_dir(run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    progress("collecting pod and event evidence")
    for name, cmd in {
        "pods": [*kubectl, "get", "pods", "-n", namespace, "-o", "json"],
        "events": [*kubectl, "get", "events", "-n", namespace, "--sort-by=.lastTimestamp", "-o", "json"],
    }.items():
        result = subprocess.run(cmd, check=False, text=True, capture_output=True)
        (out_dir / f"{name}.json").write_text(result.stdout or result.stderr, encoding="utf-8")
    payload = {
        "status": "ok",
        "machine": alias,
        "deploy_run_id": args.deploy_run_id,
        "validation_run_id": run_id,
        "artifacts": [str(p.relative_to(ROOT)) for p in out_dir.iterdir()],
        "collected_at": __import__("mws_local_state").utc_now_iso(),
        "phase": "P3",
    }
    atomic_write_json(out_dir / "manifest.json", payload)
    return emit(payload)


if __name__ == "__main__":
    raise SystemExit(main())
