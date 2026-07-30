#!/usr/bin/env python3
"""Compute PYTHONPATH and per-role env injection for a registered machine."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_machine_target import build_fixed_source_paths, pythonpath_for_machine, resolve_machine  # noqa: E402
from mws_result import emit  # noqa: E402
from mws_validate import require_safe_id  # noqa: E402

ROLE_MATRIX = {
    "controller": ["motor"],
    "coordinator": ["motor"],
    "prefill": ["motor", "vllm", "vllm_ascend"],
    "decode": ["motor", "vllm", "vllm_ascend"],
    "hybrid": ["motor", "vllm", "vllm_ascend"],
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", required=True)
    args = parser.parse_args()
    alias = require_safe_id(args.machine, label="machine")
    machine = resolve_machine(alias)
    paths = build_fixed_source_paths(machine)
    pythonpath = pythonpath_for_machine(machine)
    per_role = {}
    for role, repos in ROLE_MATRIX.items():
        ordered = []
        for repo in repos:
            key = f"{repo}_source" if repo != "vllm_ascend" else "vllm_ascend_source"
            if paths.get(key):
                ordered.append(paths[key])
        overlay = paths.get("python_overlay")
        if overlay:
            ordered.append(overlay)
        per_role[role] = ":".join(ordered)
    return emit(
        {
            "status": "ok",
            "machine": alias,
            "pythonpath": pythonpath,
            "per_role_pythonpath": per_role,
            "activation": "parity + PYTHONPATH + restart affected pods",
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
