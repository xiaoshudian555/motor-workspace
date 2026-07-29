#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LIB = ROOT / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_local_state import redact_secrets  # noqa: E402
from mws_session_state import load_session_index  # noqa: E402
from mws_result import emit  # noqa: E402


def main() -> int:
    index = load_session_index()
    return emit(
        {
            "status": "ok",
            "sessions": redact_secrets(index.get("sessions", {})),
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
