#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[4]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from repo_paths import REPO_ROOT, SCAFFOLD_ROOT  # noqa: E402

from mws_result import emit, progress  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--submodules", action="store_true")
    args = parser.parse_args()
    if not args.submodules:
        return emit({"status": "error", "errors": ["no apply action selected"]})
    progress("initializing submodules")
    result = subprocess.run(
        ["git", "submodule", "update", "--init", "--recursive"],
        cwd=str(REPO_ROOT),
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        return emit(
            {
                "status": "error",
                "errors": [result.stderr.strip() or result.stdout.strip()],
            }
        )
    return emit({"status": "ok", "action": "submodules_init"})


if __name__ == "__main__":
    raise SystemExit(main())
