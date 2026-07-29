#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LIB = ROOT / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_deploy import kubectl_base, load_profile  # noqa: E402
from mws_local_state import LOCAL_ROOT, utc_now_iso  # noqa: E402
from mws_result import emit, progress  # noqa: E402
from mws_session_state import load_session, session_dir  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--profile", default="profiles/a2-dev.yaml")
    args = parser.parse_args()
    session_id = require_safe_id(args.session_id, label="session-id")
    lookup = load_session(session_id)
    namespace = lookup.session.get("namespace", "")
    profile = load_profile(ROOT / args.profile)
    kubectl = kubectl_base(profile)
    run_id = f"diag-{uuid.uuid4().hex[:8]}"
    out_dir = session_dir(session_id) / "diagnosis" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    progress("collecting pod and event evidence")
    for name, cmd in {
        "pods": [*kubectl, "get", "pods", "-n", namespace, "-o", "json"],
        "events": [*kubectl, "get", "events", "-n", namespace, "--sort-by=.lastTimestamp", "-o", "json"],
    }.items():
        result = subprocess.run(cmd, check=False, text=True, capture_output=True)
        (out_dir / f"{name}.json").write_text(result.stdout or result.stderr, encoding="utf-8")
    payload = {
        "status": "ok",
        "session_id": session_id,
        "run_id": run_id,
        "artifacts": [str(p.relative_to(ROOT)) for p in out_dir.iterdir()],
        "collected_at": utc_now_iso(),
        "phase": "P3",
    }
    (out_dir / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return emit(payload)


if __name__ == "__main__":
    raise SystemExit(main())
