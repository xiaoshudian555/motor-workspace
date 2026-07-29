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

from mws_deploy import load_profile, render_plan  # noqa: E402
from mws_lock import verify_lock  # noqa: E402
from mws_local_state import LOCAL_ROOT, utc_now_iso  # noqa: E402
from mws_result import emit, progress  # noqa: E402
from mws_session_state import load_session, pythonpath_for_session  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402


def run_parity(session_id: str) -> dict:
    script = ROOT / ".agents/skills/remote-code-parity/scripts/parity_sync.py"
    result = subprocess.run(
        [sys.executable, str(script), "--session-id", session_id],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        return {"status": "error", "errors": [result.stderr.strip() or result.stdout.strip()]}
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--profile", default="profiles/a2-dev.yaml")
    parser.add_argument("--config-dir", default="")
    args = parser.parse_args()
    session_id = require_safe_id(args.session_id, label="session-id")
    lock = verify_lock(require_base_image=True)
    if lock["status"] != "ok":
        return emit(lock)
    progress("running parity before plan")
    parity = run_parity(session_id)
    if parity.get("status") != "ok":
        return emit(parity)
    lookup = load_session(session_id)
    session = lookup.session
    profile = load_profile(ROOT / args.profile)
    config_dir = Path(args.config_dir) if args.config_dir else ROOT / "motor/examples/infer_engines/vllm"
    run_id = f"deploy-{utc_now_iso().replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:8]}"
    run_dir = LOCAL_ROOT / "runs" / run_id / "deploy"
    progress("rendering deploy plan")
    plan = render_plan(session=session, profile=profile, config_dir=config_dir, run_dir=run_dir)
    payload = {
        "status": "ok" if plan["returncode"] == 0 else "warning",
        "run_id": run_id,
        "session_id": session_id,
        "namespace": session.get("namespace"),
        "pythonpath": pythonpath_for_session(session),
        "base_image_ref": lock["runtime"].get("base_image_ref"),
        "plan": plan,
        "read_only": True,
        "next": "review plan output then deploy_apply.py --approved-by-user",
    }
    (run_dir.parent / "run.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return emit(payload)


if __name__ == "__main__":
    raise SystemExit(main())
