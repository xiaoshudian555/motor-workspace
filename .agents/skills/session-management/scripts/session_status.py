#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LIB = ROOT / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_session_state import load_session, pythonpath_for_session  # noqa: E402
from mws_result import emit  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    session_id = require_safe_id(args.session_id, label="session-id")
    lookup = load_session(session_id)
    session = lookup.session
    return emit(
        {
            "status": "ok",
            "session_id": session_id,
            "machine": session.get("machine"),
            "namespace": session.get("namespace"),
            "remote_session_root": session.get("remote_session_root"),
            "pythonpath": pythonpath_for_session(session),
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
