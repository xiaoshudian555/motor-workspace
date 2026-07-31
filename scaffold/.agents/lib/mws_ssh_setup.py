#!/usr/bin/env python3
"""One-time SSH public-key bootstrap for machine-management."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from mws_local_state import WorkspaceStateError, get_machine
from mws_validate import require_hostname, require_safe_id

DEFAULT_KEY_CANDIDATES = ("id_ed25519", "id_rsa")


def default_identity_paths() -> tuple[Path, Path]:
    ssh_dir = Path.home() / ".ssh"
    for name in DEFAULT_KEY_CANDIDATES:
        private_key = ssh_dir / name
        public_key = ssh_dir / f"{name}.pub"
        if private_key.is_file() and public_key.is_file():
            return private_key, public_key
    raise WorkspaceStateError(
        "no default SSH key pair found under ~/.ssh (expected id_ed25519 or id_rsa)"
    )


def read_public_key(path: Path) -> str:
    if not path.is_file():
        raise WorkspaceStateError(f"public key not found: {path}")
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise WorkspaceStateError(f"public key file is empty or invalid: {path}")
    line = lines[0]
    if line.startswith("#"):
        raise WorkspaceStateError(f"public key file is empty or invalid: {path}")
    return line


def verify_batchmode_ssh(
    *,
    host: str,
    port: int,
    user: str,
    identity_file: Path,
    connect_timeout: int = 10,
) -> subprocess.CompletedProcess[str]:
    require_hostname(host, label="host")
    if not identity_file.is_file():
        raise WorkspaceStateError(f"private key not found: {identity_file}")
    return subprocess.run(
        [
            "ssh",
            "-i",
            str(identity_file),
            "-p",
            str(port),
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={int(connect_timeout)}",
            "-o",
            "StrictHostKeyChecking=accept-new",
            f"{user}@{host}",
            "echo ok",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def batchmode_ready(
    *,
    host: str,
    port: int,
    user: str,
    identity_file: Path,
    connect_timeout: int = 10,
) -> bool:
    result = verify_batchmode_ssh(
        host=host,
        port=port,
        user=user,
        identity_file=identity_file,
        connect_timeout=connect_timeout,
    )
    return result.returncode == 0 and result.stdout.strip() == "ok"


def _remote_authorized_keys_append_command(public_key_line: str) -> str:
    quoted_key = shlex.quote(public_key_line)
    return "\n".join(
        [
            "set -e",
            'auth="$HOME/.ssh/authorized_keys"',
            'mkdir -p "$HOME/.ssh"',
            'chmod 700 "$HOME/.ssh"',
            f'grep -qxF {quoted_key} "$auth" 2>/dev/null || printf "%s\\n" {quoted_key} >>"$auth"',
            'chmod 600 "$auth"',
        ]
    )


def install_public_key_via_sshpass(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    public_key_line: str,
    connect_timeout: int = 10,
) -> None:
    sshpass = shutil.which("sshpass")
    if not sshpass:
        raise WorkspaceStateError("sshpass is not installed")
    if not password:
        raise WorkspaceStateError("password is required for sshpass bootstrap")
    remote_command = _remote_authorized_keys_append_command(public_key_line)
    result = subprocess.run(
        [
            sshpass,
            "-p",
            password,
            "ssh",
            "-p",
            str(port),
            "-o",
            "PreferredAuthentications=password",
            "-o",
            "PubkeyAuthentication=no",
            "-o",
            f"ConnectTimeout={int(connect_timeout)}",
            "-o",
            "StrictHostKeyChecking=accept-new",
            f"{user}@{host}",
            remote_command,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "sshpass bootstrap failed"
        raise WorkspaceStateError(detail)


def install_public_key_via_paramiko(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    public_key_line: str,
    connect_timeout: int = 10,
) -> None:
    try:
        import paramiko
    except ImportError as exc:
        raise WorkspaceStateError(
            "paramiko is required for password bootstrap when sshpass is unavailable; "
            "install with: python3 -m pip install paramiko"
        ) from exc
    if not password:
        raise WorkspaceStateError("password is required for paramiko bootstrap")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=port,
            username=user,
            password=password,
            timeout=connect_timeout,
            allow_agent=False,
            look_for_keys=False,
        )
        remote_command = _remote_authorized_keys_append_command(public_key_line)
        _, stdout, stderr = client.exec_command(remote_command, timeout=connect_timeout)
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            detail = stderr.read().decode("utf-8", errors="replace").strip()
            raise WorkspaceStateError(detail or "paramiko bootstrap failed")
    finally:
        client.close()


def install_public_key_with_password(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    public_key_line: str,
    connect_timeout: int = 10,
) -> str:
    if shutil.which("sshpass"):
        install_public_key_via_sshpass(
            host=host,
            port=port,
            user=user,
            password=password,
            public_key_line=public_key_line,
            connect_timeout=connect_timeout,
        )
        return "sshpass"
    install_public_key_via_paramiko(
        host=host,
        port=port,
        user=user,
        password=password,
        public_key_line=public_key_line,
        connect_timeout=connect_timeout,
    )
    return "paramiko"


def setup_passwordless_ssh(
    *,
    host: str,
    port: int = 22,
    user: str = "root",
    password: str | None = None,
    public_key_path: Path | None = None,
    private_key_path: Path | None = None,
    connect_timeout: int = 10,
) -> dict[str, Any]:
    if private_key_path and public_key_path:
        identity_private = private_key_path
        identity_public = public_key_path
    elif private_key_path or public_key_path:
        raise WorkspaceStateError("public_key_path and private_key_path must be provided together")
    else:
        identity_private, identity_public = default_identity_paths()

    public_key_line = read_public_key(identity_public)
    checks: list[dict[str, str]] = []
    bootstrap_method = ""

    if batchmode_ready(
        host=host,
        port=port,
        user=user,
        identity_file=identity_private,
        connect_timeout=connect_timeout,
    ):
        checks.append({"name": "batchmode_before", "status": "ok", "message": "already configured"})
    else:
        if not password:
            raise WorkspaceStateError(
                "passwordless SSH is not configured; provide --password-stdin or --password-env"
            )
        bootstrap_method = install_public_key_with_password(
            host=host,
            port=port,
            user=user,
            password=password,
            public_key_line=public_key_line,
            connect_timeout=connect_timeout,
        )
        checks.append(
            {
                "name": "bootstrap",
                "status": "ok",
                "message": bootstrap_method,
            }
        )

    verify = verify_batchmode_ssh(
        host=host,
        port=port,
        user=user,
        identity_file=identity_private,
        connect_timeout=connect_timeout,
    )
    if verify.returncode != 0 or verify.stdout.strip() != "ok":
        detail = verify.stderr.strip() or verify.stdout.strip() or "BatchMode verification failed"
        raise WorkspaceStateError(detail)

    checks.append(
        {
            "name": "batchmode_after",
            "status": "ok",
            "message": f"{user}@{host}:{port}",
        }
    )
    return {
        "host": host,
        "port": port,
        "user": user,
        "public_key": str(identity_public),
        "private_key": str(identity_private),
        "bootstrap_method": bootstrap_method or "existing",
        "checks": checks,
    }


def resolve_machine_ssh_target(
    *,
    alias: str = "",
    host: str = "",
    port: int = 0,
    user: str = "",
) -> dict[str, Any]:
    if alias:
        alias = require_safe_id(alias, label="alias")
        machine = get_machine(alias)
        return {
            "alias": alias,
            "host": machine["host"],
            "port": int(machine.get("port") or 22),
            "user": str(machine.get("user") or "root"),
        }
    if not host:
        raise WorkspaceStateError("either --alias or --host is required")
    require_hostname(host, label="host")
    return {
        "alias": "",
        "host": host,
        "port": int(port or 22),
        "user": user or "root",
    }


def read_password(*, password_env: str, password_stdin: bool) -> str | None:
    if password_stdin:
        import sys

        value = sys.stdin.read().strip()
        if not value:
            raise WorkspaceStateError("stdin password is empty")
        return value
    if password_env:
        value = os.environ.get(password_env, "").strip()
        if not value:
            raise WorkspaceStateError(f"environment variable {password_env!r} is empty or unset")
        return value
    return None
