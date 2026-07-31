#!/usr/bin/env python3
"""Legacy entrypoint retained for compatibility; configure moved to motor-deploy-configure."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_result import emit  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", default="")
    args = parser.parse_args()
    return emit(
        {
            "status": "error",
            "errors": [
                "deploy_plan.py no longer renders or runs parity; use "
                "motor-deploy-configure/scripts/deploy_configure.py then "
                "motor-k8s-deploy/scripts/deploy_apply.py --config-run-id <id> "
                "--approved-by-user"
            ],
            "machine": args.machine,
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
