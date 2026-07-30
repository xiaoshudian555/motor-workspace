#!/usr/bin/env python3
"""Conservative repair for an existing registered machine.

Repair only refreshes inventory metadata when explicitly requested and re-runs
read-only machine-ready checks. It does not create containers, modify Kubernetes,
or perform destructive remote cleanup without separate user consent.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_deploy import load_profile  # noqa: E402
from mws_local_state import (  # noqa: E402
    WorkspaceStateError,
    get_machine,
    upsert_machine,
    utc_now_iso,
)
from mws_machine_target import run_machine_ready_checks  # noqa: E402
from mws_result import emit, emit_error, progress  # noqa: E402
from mws_transport import transport_for_machine  # noqa: E402
from mws_validate import normalize_mount_root, require_safe_id  # noqa: E402
from repo_paths import SCAFFOLD_ROOT  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alias", required=True)
    parser.add_argument("--profile", default="profiles/a2-dev.yaml")
    parser.add_argument("--host", help="update host only when explicitly provided")
    parser.add_argument("--port", type=int, help="update SSH port only when explicitly provided")
    parser.add_argument("--user", help="update SSH user only when explicitly provided")
    parser.add_argument("--mount-root", help="update mount root only when explicitly provided")
    parser.add_argument(
        "--remote-workspace-root",
        help="update remote workspace root only when explicitly provided",
    )
    parser.add_argument("--kube-context", help="update kube context metadata only when provided")
    parser.add_argument(
        "--parity-backend",
        choices=["shared-hostpath", "node-local-hostpath"],
        help="update parity backend only when explicitly provided",
    )
    parser.add_argument("--candidate-nodes", help="comma-separated candidate nodes")
    args = parser.parse_args()

    alias = require_safe_id(args.alias, label="alias")
    try:
        record = dict(get_machine(alias))
    except WorkspaceStateError as exc:
        return emit_error(str(exc), alias=alias)

    updated_fields: list[str] = []
    if args.host is not None:
        record["host"] = args.host
        updated_fields.append("host")
    if args.port is not None:
        record["port"] = args.port
        updated_fields.append("port")
    if args.user is not None:
        record["user"] = args.user
        updated_fields.append("user")
    if args.mount_root is not None:
        record["mount_root"] = normalize_mount_root(args.mount_root)
        updated_fields.append("mount_root")
    if args.remote_workspace_root is not None:
        record["remote_workspace_root"] = args.remote_workspace_root.strip()
        updated_fields.append("remote_workspace_root")
    if args.kube_context is not None:
        record["kube_context"] = args.kube_context
        updated_fields.append("kube_context")
    if args.parity_backend is not None:
        record["parity_backend"] = args.parity_backend
        updated_fields.append("parity_backend")
    if args.candidate_nodes is not None:
        record["candidate_nodes"] = [n.strip() for n in args.candidate_nodes.split(",") if n.strip()]
        updated_fields.append("candidate_nodes")

    if updated_fields:
        progress(f"updating inventory fields: {', '.join(updated_fields)}")
        try:
            _, record = upsert_machine(record)
        except WorkspaceStateError as exc:
            return emit_error(str(exc), alias=alias, updated_fields=updated_fields)

    profile_path = SCAFFOLD_ROOT / args.profile
    profile = load_profile(profile_path) if profile_path.exists() else {}
    profile_context = profile.get("kubernetes", {}).get("context", "")

    progress("re-running machine-ready checks")
    try:
        transport = transport_for_machine(record)
        result = run_machine_ready_checks(
            record,
            transport,
            profile_kube_context=str(profile_context or ""),
        )
    except WorkspaceStateError as exc:
        return emit_error(str(exc), alias=alias, updated_fields=updated_fields)

    if updated_fields:
        record["last_repaired_at"] = utc_now_iso()
        try:
            upsert_machine(record)
        except WorkspaceStateError as exc:
            return emit_error(str(exc), alias=alias, updated_fields=updated_fields)

    return emit(
        {
            "status": "ok" if result["ready"] else "error",
            "alias": alias,
            "ready": result["ready"],
            "updated_fields": updated_fields,
            "checks": result["checks"],
            "errors": result["errors"],
            "machine_ref": result["machine_ref"],
            "endpoint": result["endpoint"],
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
