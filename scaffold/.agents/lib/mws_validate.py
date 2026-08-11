#!/usr/bin/env python3
"""Validation helpers used by the parity backend."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$")
HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,253}$")
# POSIX path segment chars that are safe to embed in a shell command after quoting
# is verified. Reject shell metacharacters at the validation layer so a missed
# shell_quote() at a call site cannot become a command injection.
REMOTE_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._+@%=-]+$")


class ValidationError(ValueError):
    """Raised for deterministic user-input validation failures."""


def require_safe_id(value: str, *, label: str = "id") -> str:
    if not isinstance(value, str) or not SAFE_ID_RE.fullmatch(value):
        raise ValidationError(
            f"invalid {label}: use 3-64 chars from A-Z a-z 0-9 _ . -; "
            "no slashes, spaces, path traversal, or absolute paths"
        )
    return value


def require_hostname(value: str, *, label: str = "host") -> str:
    if not isinstance(value, str) or not HOSTNAME_RE.fullmatch(value.strip()):
        raise ValidationError(f"invalid {label}: {value!r}")
    return value.strip()


def normalize_mount_root(value: str | None) -> str:
    if not value or not str(value).strip():
        return "/mnt"
    root = str(value).strip().rstrip("/")
    if not root.startswith("/"):
        raise ValidationError("mount_root must be an absolute POSIX path")
    if ".." in PurePosixPath(root).parts:
        raise ValidationError("mount_root must not contain '..'")
    return root or "/mnt"


def validate_remote_posix_path(value: str, *, label: str = "path") -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValidationError(f"{label} must be an absolute POSIX path")
    parts = PurePosixPath(value).parts
    if ".." in parts:
        raise ValidationError(f"{label} must not contain '..'")
    for segment in parts:
        if segment == "/":
            continue
        if not REMOTE_PATH_SEGMENT_RE.fullmatch(segment):
            raise ValidationError(
                f"{label} segment {segment!r} contains characters that are unsafe "
                "for remote shell use (allowed: A-Z a-z 0-9 . _ + @ % = -)"
            )
    return value.rstrip("/") or "/"


def validate_remote_workspace_in_mount(
    mount_root: str | None,
    remote_workspace_root: str,
    *,
    label: str = "remote_workspace_root",
) -> str:
    """Ensure remote workspace stays under the configured shared mount root."""
    mount = normalize_mount_root(mount_root)
    remote = validate_remote_posix_path(remote_workspace_root, label=label)
    if remote != mount and not remote.startswith(f"{mount}/"):
        raise ValidationError(
            f"{label} must be {mount!r} or a subdirectory of it; got {remote!r}"
        )
    return remote
