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

from mws_local_state import load_inventory, redact_secrets, save_inventory  # noqa: E402
from mws_result import emit  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    args = parser.parse_args()
    inventory = load_inventory()
    machines = inventory.get("machines", {})
    return emit(
        {
            "status": "ok",
            "machines": redact_secrets(machines),
            "count": len(machines),
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
