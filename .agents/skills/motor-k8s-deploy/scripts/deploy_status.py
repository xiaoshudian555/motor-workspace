#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LIB = ROOT / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_deploy import load_profile, pod_readiness_probe  # noqa: E402
from mws_result import emit  # noqa: E402
from mws_session_state import load_session  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--profile", default="profiles/a2-dev.yaml")
    args = parser.parse_args()
    session_id = require_safe_id(args.session_id, label="session-id")
    lookup = load_session(session_id)
    profile = load_profile(ROOT / args.profile)
    namespace = lookup.session.get("namespace", "")
    pods = pod_readiness_probe(profile, namespace)
    return emit(
        {
            "status": "ok" if pods.get("ready") else "warning",
            "session_id": session_id,
            "namespace": namespace,
            "pods": pods,
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
