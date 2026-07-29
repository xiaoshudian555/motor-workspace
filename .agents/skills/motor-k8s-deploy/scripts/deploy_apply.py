#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LIB = ROOT / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_deploy import apply_deploy, load_profile  # noqa: E402
from mws_lock import verify_lock  # noqa: E402
from mws_result import emit, progress  # noqa: E402
from mws_session_state import load_session  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--profile", default="profiles/a2-dev.yaml")
    parser.add_argument("--config-dir", default="")
    parser.add_argument("--approved-by-user", action="store_true")
    args = parser.parse_args()
    if not args.approved_by_user:
        return emit({"status": "error", "errors": ["apply requires --approved-by-user"]})
    session_id = require_safe_id(args.session_id, label="session-id")
    lock = verify_lock(require_base_image=True)
    if lock["status"] != "ok":
        return emit(lock)
    lookup = load_session(session_id)
    profile = load_profile(ROOT / args.profile)
    config_dir = Path(args.config_dir) if args.config_dir else ROOT / "motor/examples/infer_engines/vllm"
    progress("applying deploy")
    result = apply_deploy(config_dir=config_dir)
    return emit(
        {
            "status": "ok" if result["returncode"] == 0 else "error",
            "session_id": session_id,
            "namespace": lookup.session.get("namespace"),
            "apply": result,
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
