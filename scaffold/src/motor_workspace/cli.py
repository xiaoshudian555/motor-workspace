#!/usr/bin/env python3
"""Internal skill backend for motor-workspace.

Product entry points are `.agents/skills/*`. This CLI exists for scripts and
tests that need shared lock/status helpers without importing skill paths.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

SCAFFOLD_ROOT = pathlib.Path(__file__).resolve().parents[2]
REPO_ROOT = SCAFFOLD_ROOT.parent
LIB = SCAFFOLD_ROOT / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_lock import verify_lock  # noqa: E402
from mws_build import build_motor_wheel_in_docker  # noqa: E402
from mws_local_state import WorkspaceStateError, load_inventory, redact_secrets  # noqa: E402
from mws_machine_target import resolve_machine  # noqa: E402
from mws_parity import prove_identity_parity, sync_workspace_fanout  # noqa: E402
from mws_result import emit  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def command_status(_: argparse.Namespace) -> int:
    lock = verify_lock(require_base_image=False)
    inventory = load_inventory()
    return emit(
        {
            "status": lock["status"],
            "backend": "motorws-internal",
            "lock": lock,
            "machines": redact_secrets(inventory.get("machines", {})),
        }
    )


def command_lock_verify(_: argparse.Namespace) -> int:
    return emit(verify_lock(require_base_image=False))


def command_lock_verify_deploy(_: argparse.Namespace) -> int:
    return emit(verify_lock(require_base_image=True))


def command_parity(args: argparse.Namespace) -> int:
    alias = require_safe_id(args.machine, label="machine")
    machine = resolve_machine(alias)
    if machine.get("executor") == "native":
        return emit(prove_identity_parity(machine))
    if not args.approved_overwrite:
        raise WorkspaceStateError(
            "parity sync overwrites remote fixed directories; "
            "re-run with --approved-overwrite"
        )
    return emit(
        sync_workspace_fanout(machine, skip_fast_path=args.skip_fast_path)
    )


def command_build_wheel(args: argparse.Namespace) -> int:
    machine = resolve_machine(require_safe_id(args.machine, label="machine"))
    return emit(
        build_motor_wheel_in_docker(
            machine=machine,
            base_image_ref=args.base_image_ref,
            source_sha=args.source_sha,
            reuse=args.reuse,
        )
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="motorws")
    root.add_argument(
        "--internal-backend",
        action="store_true",
        help="explicitly acknowledge this is not the product CLI",
    )
    commands = root.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="show lock and inventory summary")
    status.set_defaults(handler=command_status)
    lock = commands.add_parser("lock", help="lock operations")
    lock_commands = lock.add_subparsers(dest="lock_command", required=True)
    verify = lock_commands.add_parser("verify", help="verify source lock")
    verify.set_defaults(handler=command_lock_verify)
    verify_deploy = lock_commands.add_parser(
        "verify-deploy", help="verify lock including base_image_ref for deploy"
    )
    verify_deploy.set_defaults(handler=command_lock_verify_deploy)
    parity = commands.add_parser(
        "parity", help="prove identity parity or sync to fixed remote source paths"
    )
    parity.add_argument("--machine", required=True)
    parity.add_argument("--approved-overwrite", action="store_true")
    parity.add_argument("--skip-fast-path", action="store_true")
    parity.set_defaults(handler=command_parity)
    build_wheel = commands.add_parser(
        "build-wheel", help="build a Motor wheel in the runtime Docker image"
    )
    build_wheel.add_argument("--machine", required=True)
    build_wheel.add_argument("--source-sha", required=True)
    build_wheel.add_argument("--base-image-ref", required=True)
    build_wheel.add_argument("--reuse", action="store_true")
    build_wheel.set_defaults(handler=command_build_wheel)
    return root


def main() -> int:
    try:
        args = parser().parse_args()
        return args.handler(args)
    except Exception as exc:  # noqa: BLE001
        return emit({"status": "error", "errors": [str(exc)]})


if __name__ == "__main__":
    raise SystemExit(main())
