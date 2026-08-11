#!/usr/bin/env python3
"""Minimal JSON output helpers for the internal workspace backend."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def progress(message: str, *, sentinel: str = "__MWS_PROGRESS__") -> None:
    print(
        f"{sentinel}={json.dumps({'message': message}, ensure_ascii=False)}",
        file=sys.stderr,
    )


def emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload.get("status") in {"ok", "ready", "warning"} else 1
