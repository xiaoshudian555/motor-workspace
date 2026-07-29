#!/usr/bin/env python3
"""Session-id helpers for motor-workspace parallel agent sessions."""

from __future__ import annotations

import hashlib
import re
import secrets

from mws_local_state import STATE_DIR, WorkspaceStateError, utc_now_iso

SESSION_ID_PATTERN = re.compile(r"[^a-z0-9._-]+")
MULTI_DASH_PATTERN = re.compile(r"-+")
MAX_SESSION_ID_LENGTH = 64
SESSION_ID_HASH_LENGTH = 8


def normalize_session_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = SESSION_ID_PATTERN.sub("-", value.strip().lower())
    normalized = MULTI_DASH_PATTERN.sub("-", normalized).strip(".-_")
    if not normalized:
        return None
    if len(normalized) > MAX_SESSION_ID_LENGTH:
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:SESSION_ID_HASH_LENGTH]
        keep = MAX_SESSION_ID_LENGTH - len(digest) - 1
        normalized = f"{normalized[:keep].rstrip('.-_')}-{digest}"
    if len(normalized) < 3:
        return None
    return normalized


def generate_session_id() -> str:
    stamp = utc_now_iso().replace("-", "").replace(":", "").replace("T", "-").replace("Z", "")
    token = secrets.token_hex(3)
    return f"sess-{stamp}-{token}"


def require_session_id(value: str) -> str:
    normalized = normalize_session_id(value)
    if normalized is None:
        raise WorkspaceStateError(f"invalid session id: {value!r}")
    return normalized


def current_session_path() -> str:
    return str(STATE_DIR / "current-session.json")
