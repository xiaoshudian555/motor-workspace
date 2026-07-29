#!/usr/bin/env python3
"""JSON result contract for motor-workspace scripts."""

from __future__ import annotations

import json
import sys
from typing import Any


def progress(message: str, *, sentinel: str = "__MWS_PROGRESS__") -> None:
    print(f"{sentinel}={json.dumps({'message': message}, ensure_ascii=False)}", file=sys.stderr)


def emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    status = payload.get("status")
    if status in {"ok", "warning"}:
        return 0
    return 1


def emit_error(message: str, **extra: Any) -> int:
    payload = {"status": "error", "errors": [message], **extra}
    return emit(payload)
