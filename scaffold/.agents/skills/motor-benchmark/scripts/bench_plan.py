#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from repo_paths import REPO_ROOT, SCAFFOLD_ROOT  # noqa: E402

from mws_result import emit  # noqa: E402
from mws_run_state import load_deploy_run  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", required=True)
    parser.add_argument("--deploy-run-id", required=True)
    args = parser.parse_args()
    alias = require_safe_id(args.machine, label="machine")
    run_record = load_deploy_run(args.deploy_run_id)
    if run_record.get("machine") != alias:
        return emit({"status": "error", "errors": ["deploy run machine mismatch"]})
    return emit(
        {
            "status": "warning",
            "machine": alias,
            "deploy_run_id": args.deploy_run_id,
            "phase": "P3",
            "message": "motor-benchmark wrapper scaffold only; requires stable deploy run",
            "namespace": run_record.get("namespace"),
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
