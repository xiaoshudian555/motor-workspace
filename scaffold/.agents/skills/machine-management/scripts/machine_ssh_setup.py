#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_local_state import WorkspaceStateError  # noqa: E402
from mws_result import emit, emit_error, progress  # noqa: E402
from mws_ssh_setup import (  # noqa: E402
    read_password,
    resolve_machine_ssh_target,
    setup_passwordless_ssh,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install local SSH public key on a remote machine for BatchMode access.",
    )
    parser.add_argument("--alias", default="", help="machine inventory alias")
    parser.add_argument("--host", default="", help="remote host (required without --alias)")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--user", default="")
    parser.add_argument("--public-key", default="", help="path to public key (default: ~/.ssh/id_ed25519.pub)")
    parser.add_argument("--private-key", default="", help="path to private key (default: ~/.ssh/id_ed25519)")
    parser.add_argument(
        "--password-env",
        default="",
        help="environment variable holding the one-time login password",
    )
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="read one-time login password from stdin",
    )
    parser.add_argument("--connect-timeout", type=int, default=10)
    args = parser.parse_args()

    try:
        target = resolve_machine_ssh_target(
            alias=args.alias.strip(),
            host=args.host.strip(),
            port=args.port,
            user=args.user.strip(),
        )
        password = read_password(
            password_env=args.password_env.strip(),
            password_stdin=args.password_stdin,
        )
        public_key = Path(args.public_key).expanduser() if args.public_key.strip() else None
        private_key = Path(args.private_key).expanduser() if args.private_key.strip() else None

        label = target["alias"] or target["host"]
        progress(f"configuring passwordless SSH for {label}")
        result = setup_passwordless_ssh(
            host=target["host"],
            port=target["port"],
            user=target["user"],
            password=password,
            public_key_path=public_key,
            private_key_path=private_key,
            connect_timeout=args.connect_timeout,
        )
    except WorkspaceStateError as exc:
        return emit_error(str(exc), alias=args.alias.strip() or None)

    payload = {
        "status": "ok",
        "alias": target.get("alias") or None,
        "host": result["host"],
        "port": result["port"],
        "user": result["user"],
        "public_key": result["public_key"],
        "bootstrap_method": result["bootstrap_method"],
        "checks": result["checks"],
    }
    return emit(payload)


if __name__ == "__main__":
    raise SystemExit(main())
