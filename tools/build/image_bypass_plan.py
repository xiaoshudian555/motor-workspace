#!/usr/bin/env python3
"""Optional image build bypass — non-default development path."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_result import emit  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", default="")
    args = parser.parse_args()
    return emit(
        {
            "status": "warning",
            "bypass": "image-build",
            "message": "Image build bypass scaffold. Use only for release/delivery or when mount parity is unavailable.",
            "session_id": args.session_id or None,
            "reference": "motor/docker/mindie-motor-vllm",
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
