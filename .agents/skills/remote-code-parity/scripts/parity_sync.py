#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LIB = ROOT / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_local_state import ROOT as REPO_ROOT, get_machine, utc_now_iso  # noqa: E402
from mws_parity import fanout_nodes, sync_session_to_remote  # noqa: E402
from mws_result import emit, progress  # noqa: E402
from mws_session_state import load_session, session_dir, upsert_session_record  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    session_id = require_safe_id(args.session_id, label="session-id")
    lookup = load_session(session_id)
    session = dict(lookup.session)
    machine = get_machine(session["machine"])
    nodes = fanout_nodes(machine, machine.get("candidate_nodes", []))
    progress(f"parity sync to {len(nodes)} target host(s)")
    manifest = sync_session_to_remote(session, machine)
    run_dir = session_dir(session_id) / "parity"
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    session["last_parity_at"] = utc_now_iso()
    session["last_parity_manifest"] = str(manifest_path.relative_to(REPO_ROOT))
    session["snapshot_sha256"] = manifest.get("snapshot_sha256")
    upsert_session_record(session)
    return emit(
        {
            "status": "ok",
            "session_id": session_id,
            "snapshot_sha256": manifest.get("snapshot_sha256"),
            "remote_session_root": session.get("remote_session_root"),
            "artifacts": [str(manifest_path.relative_to(REPO_ROOT))],
            "next": "motor-k8s-deploy plan/apply with PYTHONPATH injection",
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
