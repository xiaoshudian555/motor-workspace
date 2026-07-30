#!/usr/bin/env python3
"""Remote transport abstraction for parity sync."""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import tarfile
import tempfile
from abc import ABC, abstractmethod
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any

from mws_local_state import WorkspaceStateError
from mws_validate import require_hostname, validate_remote_posix_path


def shell_quote(value: str) -> str:
    return shlex.quote(value)


class RemoteTransport(ABC):
    @abstractmethod
    def run(self, remote_command: str) -> subprocess.CompletedProcess[str]:
        raise NotImplementedError

    @abstractmethod
    def upload_file(self, local_path: str, remote_path: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_bytes(self, remote_path: str) -> bytes:
        raise NotImplementedError

    def mkdir(self, remote_path: str) -> None:
        path = validate_remote_posix_path(remote_path, label="remote_path")
        result = self.run(f"mkdir -p {shell_quote(path)}")
        if result.returncode:
            raise WorkspaceStateError(
                f"remote mkdir failed for {path}: {result.stderr.strip() or result.stdout.strip()}"
            )


class SshScpTransport(RemoteTransport):
    def __init__(self, machine: dict[str, Any]) -> None:
        host = require_hostname(str(machine["host"]), label="host")
        self.user = str(machine.get("user", "root"))
        self.port = int(machine.get("port", 22))
        self.host = host
        self.target = f"{self.user}@{self.host}"

    def _ssh(self, remote_command: str) -> list[str]:
        return [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "LogLevel=ERROR",
            "-p",
            str(self.port),
            self.target,
            "bash",
            "-c",
            shell_quote(remote_command),
        ]

    def run(self, remote_command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self._ssh(remote_command),
            check=False,
            text=True,
            capture_output=True,
        )

    def upload_file(self, local_path: str, remote_path: str) -> None:
        path = validate_remote_posix_path(remote_path, label="remote_path")
        remote_tmp = f"/tmp/mws-upload-{hashlib.sha256(path.encode()).hexdigest()[:12]}.bin"
        scp = [
            "scp",
            "-P",
            str(self.port),
            local_path,
            f"{self.target}:{remote_tmp}",
        ]
        result = subprocess.run(scp, check=False, text=True, capture_output=True)
        if result.returncode:
            raise WorkspaceStateError(
                f"scp upload failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        move = self.run(
            f"mkdir -p {shell_quote(str(PurePosixPath(path).parent))} && "
            f"mv {shell_quote(remote_tmp)} {shell_quote(path)}"
        )
        if move.returncode:
            raise WorkspaceStateError(
                f"remote mv failed: {move.stderr.strip() or move.stdout.strip()}"
            )

    def upload_bytes(self, remote_path: str, data: bytes) -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as handle:
            handle.write(data)
            local_path = handle.name
        try:
            self.upload_file(local_path, remote_path)
        finally:
            os.unlink(local_path)

    def read_bytes(self, remote_path: str) -> bytes:
        path = validate_remote_posix_path(remote_path, label="remote_path")
        result = self.run(f"cat {shell_quote(path)}")
        if result.returncode:
            raise WorkspaceStateError(
                f"remote read failed for {path}: {result.stderr.strip() or result.stdout.strip()}"
            )
        return result.stdout.encode()


class FakeRemoteTransport(RemoteTransport):
    """In-memory fake remote filesystem for unit tests."""

    def __init__(self, root: Path, *, node: str = "fake-node") -> None:
        self.root = root
        self.node = node
        self.root.mkdir(parents=True, exist_ok=True)
        self.commands: list[str] = []
        self.uploads: list[tuple[str, str]] = []

    def _local(self, remote_path: str) -> Path:
        path = validate_remote_posix_path(remote_path, label="remote_path")
        rel = path.lstrip("/")
        local = (self.root / rel).resolve()
        if self.root.resolve() not in local.parents and local != self.root.resolve():
            raise WorkspaceStateError(f"path escapes fake root: {path}")
        return local

    def _run_part(self, part: str) -> subprocess.CompletedProcess[str]:
        part = part.strip()
        if part.startswith("mkdir -p "):
            target = shlex.split(part[len("mkdir -p ") :])[0]
            self._local(target).mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        if part.startswith("tar -xzf "):
            tokens = shlex.split(part)
            archive = tokens[2]
            dest_flag = tokens.index("-C")
            dest = tokens[dest_flag + 1]
            data = self._local(archive).read_bytes()
            dest_path = self._local(dest)
            dest_path.mkdir(parents=True, exist_ok=True)
            with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as archive_obj:
                archive_obj.extractall(dest_path)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        if part.startswith("rm -rf "):
            target = shlex.split(part[len("rm -rf ") :])[0]
            local = self._local(target)
            if local.is_dir():
                import shutil

                shutil.rmtree(local)
            elif local.exists():
                local.unlink()
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        if part.startswith("rm -f "):
            target = shlex.split(part[len("rm -f ") :])[0]
            local = self._local(target)
            if local.exists():
                local.unlink()
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        if part.startswith("mv "):
            tokens = shlex.split(part)
            src = self._local(tokens[1])
            dst = self._local(tokens[2])
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                if dst.is_dir():
                    import shutil

                    shutil.rmtree(dst)
                else:
                    dst.unlink()
            src.rename(dst)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        if part.startswith("test -f "):
            target = shlex.split(part[len("test -f ") :])[0]
            ok = self._local(target).is_file()
            return subprocess.CompletedProcess(args=[], returncode=0 if ok else 1, stdout="", stderr="")
        if part.startswith("test -d "):
            target = shlex.split(part[len("test -d ") :])[0]
            ok = self._local(target).is_dir()
            return subprocess.CompletedProcess(args=[], returncode=0 if ok else 1, stdout="", stderr="")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    def run(self, remote_command: str) -> subprocess.CompletedProcess[str]:
        self.commands.append(remote_command)
        cmd = remote_command.strip()
        if " && " in cmd:
            for segment in cmd.split(" && "):
                result = self._run_part(segment)
                if result.returncode:
                    return result
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        return self._run_part(cmd)

    def upload_file(self, local_path: str, remote_path: str) -> None:
        self.uploads.append((local_path, remote_path))
        data = Path(local_path).read_bytes()
        local = self._local(remote_path)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)

    def upload_bytes(self, remote_path: str, data: bytes) -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as handle:
            handle.write(data)
            local_path = handle.name
        try:
            self.upload_file(local_path, remote_path)
        finally:
            os.unlink(local_path)

    def read_bytes(self, remote_path: str) -> bytes:
        return self._local(remote_path).read_bytes()


def transport_for_machine(
    machine: dict[str, Any],
    *,
    fake_root: Path | None = None,
    node: str | None = None,
) -> RemoteTransport:
    if fake_root is not None:
        return FakeRemoteTransport(fake_root, node=node or machine.get("host", "fake-node"))
    return SshScpTransport(machine)


def validate_machine_transport_fields(machine: dict[str, Any]) -> None:
    require_hostname(str(machine["host"]), label="host")
    if not str(machine.get("user", "root")).strip():
        raise WorkspaceStateError("machine user must be non-empty")
    port = machine.get("port", 22)
    if not isinstance(port, int) or port <= 0:
        raise WorkspaceStateError("machine port must be a positive integer")
    mount_root = machine.get("mount_root")
    if not mount_root:
        raise WorkspaceStateError("machine mount_root must be configured")
