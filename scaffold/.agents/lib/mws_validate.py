#!/usr/bin/env python3
"""Shared validation helpers for motor-workspace agent scripts."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$")
# Kubernetes DNS-1123 label: lowercase alphanumeric, '-' allowed internally, 1-63 chars
K8S_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,253}$")
# POSIX path segment chars that are safe to embed in a shell command after quoting
# is verified. Reject shell metacharacters at the validation layer so a missed
# shell_quote() at a call site cannot become a command injection.
REMOTE_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._+@%=-]+$")


class ValidationError(ValueError):
    """Raised for deterministic user-input validation failures."""


def require_env_name(name: str) -> str:
    if not isinstance(name, str) or not ENV_NAME_RE.fullmatch(name):
        raise ValidationError(
            f"invalid env var name: {name!r}; use ASCII [A-Za-z_][A-Za-z0-9_]*"
        )
    return name


def require_safe_id(value: str, *, label: str = "id") -> str:
    if not isinstance(value, str) or not SAFE_ID_RE.fullmatch(value):
        raise ValidationError(
            f"invalid {label}: use 3-64 chars from A-Z a-z 0-9 _ . -; "
            "no slashes, spaces, path traversal, or absolute paths"
        )
    return value


def require_k8s_dns_label(value: str, *, label: str = "label") -> str:
    if not isinstance(value, str):
        raise ValidationError(f"invalid {label}: must be a string")
    normalized = value.strip().lower()
    if not normalized or not K8S_DNS_LABEL_RE.fullmatch(normalized):
        raise ValidationError(
            f"invalid {label}: must be a Kubernetes DNS-1123 label "
            "(lowercase alphanumeric or '-', 1-63 chars, start/end alphanumeric)"
        )
    return normalized


def require_hostname(value: str, *, label: str = "host") -> str:
    if not isinstance(value, str) or not HOSTNAME_RE.fullmatch(value.strip()):
        raise ValidationError(f"invalid {label}: {value!r}")
    return value.strip()


def require_remote_leaf(value: str, *, label: str = "id") -> str:
    safe = require_safe_id(value, label=label)
    path = PurePosixPath(safe)
    if path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
        raise ValidationError(f"invalid {label}: must be one remote path segment")
    return safe


def ensure_child_path(root: Path, child: Path) -> Path:
    root_resolved = root.expanduser().resolve()
    child_resolved = child.expanduser().resolve()
    if root_resolved != child_resolved and root_resolved not in child_resolved.parents:
        raise ValidationError(f"path escapes state dir: {child_resolved}")
    return child_resolved


def parse_device_csv(value: str | None, *, label: str = "devices") -> list[int] | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a non-empty comma-separated device list")
    devices: list[int] = []
    seen: set[int] = set()
    for raw in value.split(","):
        token = raw.strip()
        if not token:
            raise ValidationError(f"{label} contains an empty device id")
        try:
            device = int(token, 10)
        except ValueError as exc:
            raise ValidationError(
                f"{label} contains a non-integer device id: {token!r}"
            ) from exc
        if device < 0:
            raise ValidationError(f"{label} contains a negative device id: {device}")
        if device in seen:
            raise ValidationError(f"{label} contains a duplicate device id: {device}")
        seen.add(device)
        devices.append(device)
    return sorted(devices)


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
