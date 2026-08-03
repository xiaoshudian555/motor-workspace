#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_functional import compile_validation_spec, write_validation_spec  # noqa: E402
from mws_local_state import WorkspaceStateError  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--machine", required=True)
    parser.add_argument("--deploy-run-id", required=True)
    parser.add_argument("--feature", action="append", default=[])
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    try:
        spec = compile_validation_spec(
            user_request=args.request,
            machine=require_safe_id(args.machine, label="machine"),
            deploy_run_id=require_safe_id(args.deploy_run_id, label="deploy_run_id"),
            selected_features=args.feature or None,
            selected_cases=args.case or None,
        )
        if args.output:
            write_validation_spec(Path(args.output), spec)
    except WorkspaceStateError as exc:
        print(json.dumps({"status": "error", "errors": [str(exc)]}, ensure_ascii=False))
        return 1

    print(json.dumps(spec, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
