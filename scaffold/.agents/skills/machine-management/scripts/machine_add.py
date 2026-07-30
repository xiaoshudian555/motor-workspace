#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_local_state import WorkspaceStateError, upsert_machine, utc_now_iso  # noqa: E402
from mws_result import emit, emit_error, progress  # noqa: E402
from mws_validate import normalize_mount_root, require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alias", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user", default="root")
    parser.add_argument("--mount-root", default="/mnt")
    parser.add_argument("--remote-workspace-root", default="")
    parser.add_argument("--kube-context", default="")
    parser.add_argument(
        "--parity-backend",
        choices=["shared-hostpath", "node-local-hostpath"],
        default="shared-hostpath",
    )
    parser.add_argument("--candidate-nodes", default="")
    args = parser.parse_args()

    alias = require_safe_id(args.alias, label="alias")
    mount_root = normalize_mount_root(args.mount_root)
    remote_workspace_root = args.remote_workspace_root.strip() or f"{mount_root}/motor-workspace"
    nodes = [n.strip() for n in args.candidate_nodes.split(",") if n.strip()]

    progress(f"registering machine {alias}")
    record = {
        "alias": alias,
        "host": args.host,
        "port": args.port,
        "user": args.user,
        "mount_root": mount_root,
        "remote_workspace_root": remote_workspace_root,
        "kube_context": args.kube_context,
        "parity_backend": args.parity_backend,
        "candidate_nodes": nodes,
        "created_at": utc_now_iso(),
    }
    try:
        action, saved = upsert_machine(record)
    except WorkspaceStateError as exc:
        return emit_error(str(exc), alias=alias)

    return emit(
        {
            "status": "ok",
            "action": action,
            "alias": alias,
            "mount_root": saved["mount_root"],
            "remote_workspace_root": saved["remote_workspace_root"],
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
