#!/usr/bin/env python3
"""Remote transport abstraction for parity sync."""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import tarfile
import tempfile
import time
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

    def git(self, repo_dir: str, *args: str) -> subprocess.CompletedProcess[str]:
        """Run `git -C <repo_dir> <args...>` on the remote host."""
        raise NotImplementedError

    def kubectl(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run `kubectl <args...>` on the remote host, using its own kubeconfig."""
        raise NotImplementedError

    def mkdir(self, remote_path: str) -> None:
        path = validate_remote_posix_path(remote_path, label="remote_path")
        result = self.run(f"mkdir -p {shell_quote(path)}")
        if result.returncode:
            raise WorkspaceStateError(
                f"remote mkdir failed for {path}: {result.stderr.strip() or result.stdout.strip()}"
            )

    def acquire_parity_lock(
        self,
        lock_path: str,
        *,
        stale_seconds: int = 3600,
    ) -> None:
        script = "\n".join(
            [
                "set -eo pipefail",
                f"lock={shell_quote(validate_remote_posix_path(lock_path, label='lock_path'))}",
                f"stale_seconds={int(stale_seconds)}",
                'mkdir -p "$(dirname "$lock")"',
                'if mkdir "$lock" 2>/dev/null; then',
                '  printf "pid=%s\\n" "$$" >"$lock/owner"',
                "  exit 0",
                "fi",
                'if [ -d "$lock" ]; then',
                '  now="$(date +%s 2>/dev/null || echo 0)"',
                '  mtime="$(stat -c %Y "$lock" 2>/dev/null || echo 0)"',
                '  age="$((now - mtime))"',
                '  if [ "$age" -ge "$stale_seconds" ]; then',
                '    rm -rf "$lock"',
                '    if mkdir "$lock" 2>/dev/null; then',
                '      printf "pid=%s\\n" "$$" >"$lock/owner"',
                "      exit 0",
                "    fi",
                "  fi",
                "fi",
                'echo "lock exists: $lock" >&2',
                "exit 1",
            ]
        )
        result = self.run(script)
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "lock busy"
            raise WorkspaceStateError(
                f"could not acquire remote parity lock {lock_path}: {detail}"
            )

    def release_parity_lock(self, lock_path: str) -> None:
        path = validate_remote_posix_path(lock_path, label="lock_path")
        self.run(f"rm -rf {shell_quote(path)} >/dev/null 2>&1 || true")

    def directory_file_hashes(self, remote_dir: str) -> dict[str, str]:
        path = validate_remote_posix_path(remote_dir, label="remote_dir")
        script = "\n".join(
            [
                "set -eo pipefail",
                f"root={shell_quote(path.rstrip('/'))}",
                'if [ ! -d "$root" ]; then exit 0; fi',
                'find "$root" -type f -print0 | sort -z | while IFS= read -r -d "" file; do',
                '  rel="${file#"$root"/}"',
                '  hash="$(sha256sum "$file" | awk \'{print $1}\')"',
                '  printf "%s\\t%s\\n" "$rel" "$hash"',
                "done",
            ]
        )
        result = self.run(script)
        if result.returncode:
            raise WorkspaceStateError(
                f"remote digest scan failed for {path}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        hashes: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if not line.strip() or "\t" not in line:
                continue
            rel, digest = line.split("\t", 1)
            hashes[rel] = digest.strip()
        return hashes


class SshScpTransport(RemoteTransport):
    SSH_COMMAND_TIMEOUT_SECONDS = 60
    # sshd on some hosts throttles new auth during fail2ban backoff or flood
    # windows; retry transport-level timeouts so command execution survives.
    SSH_RUN_ATTEMPTS = 4
    SSH_RETRY_BACKOFF_SECONDS = 1.5

    def __init__(self, machine: dict[str, Any]) -> None:
        host = require_hostname(str(machine["host"]), label="host")
        self.user = str(machine.get("user", "root"))
        self.port = int(machine.get("port", 22))
        self.host = host
        self.target = f"{self.user}@{self.host}"
        self._safe_registered: set[str] = set()

    def ssh_argv(
        self,
        remote_command: str,
        *,
        local_forward: tuple[int, str, int] | None = None,
    ) -> list[str]:
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "GSSAPIAuthentication=no",
            "-o",
            "PreferredAuthentications=publickey",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "ConnectTimeout=10",
            "-p",
            str(self.port),
        ]
        if local_forward is not None:
            local_port, remote_host, remote_port = local_forward
            command.extend(
                [
                    "-o",
                    "ExitOnForwardFailure=yes",
                    "-L",
                    f"127.0.0.1:{local_port}:{remote_host}:{remote_port}",
                ]
            )
        command.extend(
            [
                self.target,
                "bash",
                "-c",
                shell_quote(remote_command),
            ]
        )
        return command

    def _ssh(self, remote_command: str) -> list[str]:
        return self.ssh_argv(remote_command)

    def run(self, remote_command: str) -> subprocess.CompletedProcess[str]:
        command = self._ssh(remote_command)
        last: subprocess.CompletedProcess[str] | None = None
        for attempt in range(self.SSH_RUN_ATTEMPTS):
            try:
                result = subprocess.run(
                    command,
                    check=False,
                    text=True,
                    capture_output=True,
                    timeout=self.SSH_COMMAND_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired as exc:
                result = subprocess.CompletedProcess(
                    args=command,
                    returncode=124,
                    stdout=exc.stdout or "",
                    stderr=(
                        f"SSH command timed out after {self.SSH_COMMAND_TIMEOUT_SECONDS}s"
                    ),
                )
            last = result
            # Timeout/transport failures are retried; business-level non-zero
            # exit codes are returned to the caller untouched.
            if result.returncode != 124:
                return result
            if attempt < self.SSH_RUN_ATTEMPTS - 1:
                time.sleep(self.SSH_RETRY_BACKOFF_SECONDS * (2**attempt))
        assert last is not None
        return last

    def upload_file(self, local_path: str, remote_path: str) -> None:
        data = Path(local_path).read_bytes()
        self.upload_bytes(remote_path, data)

    def _scp_argv(self, local_path: str, remote_path: str) -> list[str]:
        command = [
            "scp",
            "-o",
            "BatchMode=yes",
            "-o",
            "GSSAPIAuthentication=no",
            "-o",
            "PreferredAuthentications=publickey",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "ConnectTimeout=10",
            "-P",
            str(self.port),
            local_path,
            f"{self.target}:{remote_path}",
        ]
        return command

    def _scp_upload(self, data: bytes, remote_path: str) -> None:
        """Upload bytes via a scp subprocess.

        scp transfers over its own file channel and does not depend on local ssh
        stdin EOF forwarding. Retry counts mirror SSH_RUN_ATTEMPTS so throttled
        auth windows do not abort the transfer.
        """
        local_tmp = (
            f"/tmp/mws-upload-{hashlib.sha256(remote_path.encode()).hexdigest()[:12]}.in"
        )
        Path(local_tmp).write_bytes(data)
        upload_timeout = max(
            self.SSH_COMMAND_TIMEOUT_SECONDS,
            self.SSH_COMMAND_TIMEOUT_SECONDS + len(data) // (256 * 1024),
        )
        try:
            for attempt in range(self.SSH_RUN_ATTEMPTS):
                try:
                    result = subprocess.run(
                        self._scp_argv(local_tmp, remote_path),
                        check=False,
                        capture_output=True,
                        timeout=upload_timeout,
                    )
                except subprocess.TimeoutExpired:
                    if attempt < self.SSH_RUN_ATTEMPTS - 1:
                        time.sleep(self.SSH_RETRY_BACKOFF_SECONDS * (2**attempt))
                        continue
                    raise WorkspaceStateError(
                        f"scp upload timed out after {upload_timeout}s for {remote_path}"
                    )
                if result.returncode == 0:
                    return
                stderr = result.stderr.decode(errors="replace") if result.stderr else ""
                stdout = result.stdout.decode(errors="replace") if result.stdout else ""
                if attempt >= self.SSH_RUN_ATTEMPTS - 1:
                    raise WorkspaceStateError(
                        f"scp upload failed: {stderr.strip() or stdout.strip()}"
                    )
                time.sleep(self.SSH_RETRY_BACKOFF_SECONDS * (2**attempt))
        finally:
            try:
                Path(local_tmp).unlink()
            except OSError:
                pass

    def upload_bytes(self, remote_path: str, data: bytes) -> None:
        path = validate_remote_posix_path(remote_path, label="remote_path")
        remote_tmp = f"/tmp/mws-upload-{hashlib.sha256(path.encode()).hexdigest()[:12]}.bin"
        self._scp_upload(data, remote_tmp)
        move = self.run(
            f"mkdir -p {shell_quote(str(PurePosixPath(path).parent))} && "
            f"mv {shell_quote(remote_tmp)} {shell_quote(path)}"
        )
        if move.returncode:
            raise WorkspaceStateError(
                f"remote mv failed: {move.stderr.strip() or move.stdout.strip()}"
            )

    def read_bytes(self, remote_path: str) -> bytes:
        path = validate_remote_posix_path(remote_path, label="remote_path")
        result = self.run(f"cat {shell_quote(path)}")
        if result.returncode:
            raise WorkspaceStateError(
                f"remote read failed for {path}: {result.stderr.strip() or result.stdout.strip()}"
            )
        return result.stdout.encode()

    def git(self, repo_dir: str, *args: str) -> subprocess.CompletedProcess[str]:
        repo = validate_remote_posix_path(repo_dir, label="repo_dir")
        argv = " ".join(shell_quote(arg) for arg in args)
        # Old backported git (e.g. 2.33) only honors safe.directory from the
        # remote global config, not -c/command-line flags. Register the concrete
        # repo path once per transport so dubious-ownership failures on the
        # shared mount root are avoided across all git invocations.
        if repo not in self._safe_registered:
            self.run(
                f"git config --global --add safe.directory {shell_quote(repo)} "
                ">/dev/null 2>&1 || true"
            )
            self._safe_registered.add(repo)
        return self.run(f"git -C {shell_quote(repo)} {argv}")

    def kubectl(self, *args: str) -> subprocess.CompletedProcess[str]:
        argv = " ".join(shell_quote(arg) for arg in args)
        return self.run(f"kubectl {argv}")


class FakeRemoteTransport(RemoteTransport):
    """In-memory fake remote filesystem for unit tests."""

    _shared_parity_locks: set[str] = set()

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
                archive_obj.extractall(dest_path, filter="data")
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

    def acquire_parity_lock(
        self,
        lock_path: str,
        *,
        stale_seconds: int = 3600,
    ) -> None:
        del stale_seconds
        path = validate_remote_posix_path(lock_path, label="lock_path")
        if path in self._shared_parity_locks:
            raise WorkspaceStateError(f"could not acquire remote parity lock {path}: lock busy")
        self._shared_parity_locks.add(path)
        lock_dir = self._local(path)
        lock_dir.mkdir(parents=True, exist_ok=True)
        (lock_dir / "owner").write_text("pid=fake\n", encoding="utf-8")

    def release_parity_lock(self, lock_path: str) -> None:
        path = validate_remote_posix_path(lock_path, label="lock_path")
        self._shared_parity_locks.discard(path)
        local = self._local(path)
        if local.is_dir():
            import shutil

            shutil.rmtree(local)
        elif local.exists():
            local.unlink()

    def directory_file_hashes(self, remote_dir: str) -> dict[str, str]:
        local_root = self._local(remote_dir.rstrip("/"))
        if not local_root.exists():
            return {}
        hashes: dict[str, str] = {}
        for path in sorted(local_root.rglob("*")):
            if path.is_file():
                rel = str(path.relative_to(local_root))
                hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        return hashes

    def git(self, repo_dir: str, *args: str) -> subprocess.CompletedProcess[str]:
        local = self._local(repo_dir)
        local.mkdir(parents=True, exist_ok=True)
        rewritten = [
            str(self._local(arg)) if arg.startswith("/") else arg for arg in args
        ]
        result = subprocess.run(
            ["git", "-C", str(local), *rewritten],
            check=False,
            text=True,
            capture_output=True,
        )
        return subprocess.CompletedProcess(
            args=result.args,
            returncode=result.returncode,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
        )


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
