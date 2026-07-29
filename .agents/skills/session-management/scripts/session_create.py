#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LIB = ROOT / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_local_state import get_machine, load_profile, utc_now_iso  # noqa: E402
from mws_session_id import generate_session_id  # noqa: E402
from mws_session_state import build_session_paths, upsert_session_record  # noqa: E402
from mws_result import emit, progress  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", required=True)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--namespace", default="")
    parser.add_argument("--job-id", default="")
    args = parser.parse_args()
    alias = require_safe_id(args.machine, label="machine")
    machine = get_machine(alias)
    session_id = args.session_id or generate_session_id()
    ws_profile = load_profile()
    workspace_id = ws_profile.get("workspace_id", f"mws-{uuid.uuid4().hex[:8]}")
    paths = build_session_paths(
        workspace_id=workspace_id,
        session_id=session_id,
        mount_root=machine.get("mount_root", "/mnt"),
    )
    namespace = args.namespace or f"motorws-{session_id[-8:]}"
    job_id = args.job_id or namespace
    progress(f"creating session {session_id}")
    session = {
        "session_id": session_id,
        "machine": alias,
        "workspace_id": workspace_id,
        "namespace": namespace,
        "job_id": job_id,
        "remote_session_root": paths["remote_session_root"],
        "paths": paths,
        "created_at": utc_now_iso(),
    }
    path = upsert_session_record(session)
    return emit(
        {
            "status": "ok",
            "session_id": session_id,
            "session_file": str(path.relative_to(ROOT)),
            "remote_session_root": paths["remote_session_root"],
            "namespace": namespace,
            "job_id": job_id,
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
