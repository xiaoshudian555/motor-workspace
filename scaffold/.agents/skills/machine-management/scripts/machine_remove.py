#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_local_state import WorkspaceStateError, remove_machine  # noqa: E402
from mws_result import emit, emit_error, progress  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alias", required=True, help="machine alias or host IP")
    args = parser.parse_args()

    progress(f"removing machine {args.alias}")
    try:
        removed = remove_machine(args.alias)
    except WorkspaceStateError as exc:
        return emit_error(str(exc), alias=args.alias)

    return emit(
        {
            "status": "ok",
            "action": "removed",
            "alias": removed["alias"],
            "host": removed["host"],
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
