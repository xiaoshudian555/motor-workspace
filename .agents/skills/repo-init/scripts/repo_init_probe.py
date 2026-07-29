#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LIB = ROOT / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_lock import verify_lock  # noqa: E402
from mws_local_state import load_profile, save_profile  # noqa: E402
from mws_result import emit, progress  # noqa: E402


def probe_submodules() -> dict[str, str]:
    status: dict[str, str] = {}
    for name in ("motor", "vllm", "vllm-ascend"):
        path = ROOT / name
        if not (path / ".git").exists() and not (ROOT / ".git" / "modules" / name.replace("-", "_")).exists():
            status[name] = "missing"
            continue
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=False,
            text=True,
            capture_output=True,
        )
        status[name] = result.stdout.strip() if result.returncode == 0 else "error"
    return status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    progress("probing workspace")
    profile = load_profile()
    lock = verify_lock(require_base_image=False)
    submodules = probe_submodules()
    payload = {
        "status": lock["status"],
        "workspace": str(ROOT),
        "workspace_id": profile.get("workspace_id"),
        "submodules": submodules,
        "lock": lock,
        "next": "run repo_init_apply.py --submodules if submodules are missing",
    }
    if args.compact:
        payload = {
            "status": payload["status"],
            "workspace_id": payload["workspace_id"],
            "submodules": submodules,
            "errors": lock.get("errors", []),
        }
    return emit(payload)


if __name__ == "__main__":
    raise SystemExit(main())
