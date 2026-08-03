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
    upsert_machine,
    utc_now_iso,
)
from mws_result import emit, emit_error, progress  # noqa: E402
from mws_self_identify import (  # noqa: E402
    build_native_record,
    detect_hostname,
    detect_kube_context,
    detect_mount_root,
    detect_remote_workspace_root,
    find_existing_native_alias,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Register the current host as an executor=native machine."
    )
    parser.add_argument("--alias", default="", help="explicit alias; defaults to hostname-derived")
    parser.add_argument("--mount-root", default="", help="override probed shared mount root")
    parser.add_argument("--remote-workspace-root", default="", help="override probed fixed workspace root")
    parser.add_argument("--kube-context", default="", help="override probed kubectl context")
    parser.add_argument("--user", default="", help="override probed current user")
    parser.add_argument("--dry-run", action="store_true", help="print probed record without writing")
    args = parser.parse_args()

    hostname = detect_hostname()
    progress(f"self-identifying current host {hostname} as remote-native machine")

    try:
        mount_root = args.mount_root.strip() or detect_mount_root()
        workspace_root = args.remote_workspace_root.strip() or detect_remote_workspace_root(mount_root)
        record = build_native_record(
            alias=args.alias.strip() or None,
            hostname=hostname,
            user=args.user.strip() or None,
            mount_root=mount_root,
            remote_workspace_root=workspace_root,
            kube_context=args.kube_context.strip() or detect_kube_context(),
        )
    except WorkspaceStateError as exc:
        return emit_error(str(exc), host=hostname)

    existing_alias = find_existing_native_alias(hostname)
    action = "reused" if existing_alias and existing_alias == record["alias"] else "inserted"

    if args.dry_run:
        return emit(
            {
                "status": "ok",
                "dry_run": True,
                "action": action,
                "hostname": hostname,
                "probed": record,
                "message": "dry run: no inventory write performed",
            }
        )

    try:
        write_action, saved = upsert_machine(record)
    except WorkspaceStateError as exc:
        return emit_error(str(exc), host=hostname)

    return emit(
        {
            "status": "ok",
            "action": "reused" if write_action == "updated" and saved.get("executor") == "native" else write_action,
            "alias": saved["alias"],
            "host": saved["host"],
            "executor": saved["executor"],
            "mount_root": saved["mount_root"],
            "remote_workspace_root": saved["remote_workspace_root"],
            "source_dirs": saved.get("source_dirs"),
            "registered_at": utc_now_iso(),
            "note": "registration only provides connection defaults; run machine_verify to prove readiness",
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
