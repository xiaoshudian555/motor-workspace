#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_local_state import (  # noqa: E402
    WorkspaceStateError,
    get_machine,
    list_machines,
    redact_secrets,
    remove_machine,
)
from mws_result import emit, emit_error  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    get_cmd = sub.add_parser("get")
    get_cmd.add_argument("alias")

    remove_cmd = sub.add_parser("remove")
    remove_cmd.add_argument("identifier")

    args = parser.parse_args()

    if args.cmd == "list":
        machines = list_machines()
        return emit(
            {
                "status": "ok",
                "machines": redact_secrets(machines),
                "count": len(machines),
            }
        )

    if args.cmd == "get":
        alias = require_safe_id(args.alias, label="alias")
        try:
            record = get_machine(alias)
        except WorkspaceStateError as exc:
            return emit_error(str(exc), alias=alias)
        return emit({"status": "ok", "machine": redact_secrets(record)})

    if args.cmd == "remove":
        try:
            removed = remove_machine(args.identifier)
        except WorkspaceStateError as exc:
            return emit_error(str(exc), identifier=args.identifier)
        return emit(
            {
                "status": "ok",
                "action": "removed",
                "alias": removed["alias"],
                "host": removed["host"],
            }
        )

    return emit_error(f"unsupported command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
