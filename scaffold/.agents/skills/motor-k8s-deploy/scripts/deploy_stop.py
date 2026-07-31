#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from repo_paths import REPO_ROOT  # noqa: E402

from mws_deploy import stop_from_bundle  # noqa: E402
from mws_local_state import get_machine  # noqa: E402
from mws_result import emit, progress  # noqa: E402
from mws_run_state import deploy_run_dir, load_deploy_run  # noqa: E402
from mws_state import atomic_write_json  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", required=True)
    parser.add_argument("--deploy-run-id", required=True)
    parser.add_argument("--approved-by-user", action="store_true")
    args = parser.parse_args()
    if not args.approved_by_user:
        return emit({"status": "error", "errors": ["stop requires --approved-by-user"]})
    alias = require_safe_id(args.machine, label="machine")
    machine = get_machine(alias)
    kube_context = str(machine.get("kube_context") or "")
    run_record = load_deploy_run(args.deploy_run_id)
    if run_record.get("machine") != alias:
        return emit({"status": "error", "errors": ["deploy run machine mismatch"]})
    bundle_rel = run_record.get("bundle_dir")
    if not bundle_rel:
        return emit({"status": "error", "errors": ["bundle_dir missing; nothing to stop"]})
    bundle_ref = str(bundle_rel)
    bundle_dir = Path(bundle_ref)
    if not bundle_dir.is_absolute():
        bundle_dir = REPO_ROOT / bundle_ref
    namespace = str(run_record.get("namespace") or "")
    progress("stopping run-scoped deployment")
    result = stop_from_bundle(bundle_dir, kube_context=kube_context, namespace=namespace)
    payload = {
        "status": result["status"],
        "deploy_run_id": args.deploy_run_id,
        "machine": alias,
        "stop": result,
    }
    atomic_write_json(deploy_run_dir(args.deploy_run_id) / "stop.json", payload)
    return emit(payload)


if __name__ == "__main__":
    raise SystemExit(main())
