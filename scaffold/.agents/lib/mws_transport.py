#!/usr/bin/env python3
"""Remote transport abstraction for parity sync."""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import tarfile
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    SSH_STDIN_CHUNK_BYTES = 2048
    SSH_CHUNK_UPLOAD_WORKERS = 16

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
            self.target,
            "bash",
            "-c",
            shell_quote(remote_command),
        ]

    def run(self, remote_command: str) -> subprocess.CompletedProcess[str]:
        command = self._ssh(remote_command)
        try:
            return subprocess.run(
                command,
                check=False,
                text=True,
                capture_output=True,
                timeout=self.SSH_COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(
                args=command,
                returncode=124,
                stdout=exc.stdout or "",
                stderr=(
                    f"SSH command timed out after {self.SSH_COMMAND_TIMEOUT_SECONDS}s"
                ),
            )

    def upload_file(self, local_path: str, remote_path: str) -> None:
        data = Path(local_path).read_bytes()
        self.upload_bytes(remote_path, data)

    def upload_bytes(self, remote_path: str, data: bytes) -> None:
        path = validate_remote_posix_path(remote_path, label="remote_path")
        remote_tmp = f"/tmp/mws-upload-{hashlib.sha256(path.encode()).hexdigest()[:12]}.bin"
        upload_timeout = max(
            self.SSH_COMMAND_TIMEOUT_SECONDS,
            self.SSH_COMMAND_TIMEOUT_SECONDS + len(data) // (256 * 1024),
        )
        if len(data) > self.SSH_STDIN_CHUNK_BYTES:
            self._upload_chunked(
                remote_tmp=remote_tmp,
                data=data,
                timeout=upload_timeout,
            )
            move = self.run(
                f"mkdir -p {shell_quote(str(PurePosixPath(path).parent))} && "
                f"mv {shell_quote(remote_tmp)} {shell_quote(path)}"
            )
            if move.returncode:
                raise WorkspaceStateError(
                    f"remote mv failed: {move.stderr.strip() or move.stdout.strip()}"
                )
            return
        result = subprocess.run(
            self._ssh(f"head -c {len(data)} > {shell_quote(remote_tmp)}"),
            input=data,
            check=False,
            capture_output=True,
            timeout=upload_timeout,
        )
        if result.returncode:
            stderr = (result.stderr or b"").decode(errors="replace")
            stdout = (result.stdout or b"").decode(errors="replace")
            raise WorkspaceStateError(
                f"SSH streaming upload failed: {stderr.strip() or stdout.strip()}"
            )
        move = self.run(
            f"mkdir -p {shell_quote(str(PurePosixPath(path).parent))} && "
            f"mv {shell_quote(remote_tmp)} {shell_quote(path)}"
        )
        if move.returncode:
            raise WorkspaceStateError(
                f"remote mv failed: {move.stderr.strip() or move.stdout.strip()}"
            )

    def _upload_chunked(self, *, remote_tmp: str, data: bytes, timeout: float) -> None:
        chunk_size = self.SSH_STDIN_CHUNK_BYTES
        chunks = [
            (index, data[offset : offset + chunk_size])
            for index, offset in enumerate(range(0, len(data), chunk_size))
        ]

        def upload_chunk(item: tuple[int, bytes]) -> None:
            index, chunk = item
            part_path = f"{remote_tmp}.part-{index:08d}"
            result = subprocess.run(
                self._ssh(f"head -c {len(chunk)} > {shell_quote(part_path)}"),
                input=chunk,
                check=False,
                capture_output=True,
                timeout=timeout,
            )
            if result.returncode:
                stderr = (result.stderr or b"").decode(errors="replace")
                stdout = (result.stdout or b"").decode(errors="replace")
                raise WorkspaceStateError(
                    f"SSH chunk upload failed for chunk {index}: "
                    f"{stderr.strip() or stdout.strip()}"
                )

        try:
            with ThreadPoolExecutor(
                max_workers=min(self.SSH_CHUNK_UPLOAD_WORKERS, len(chunks))
            ) as executor:
                futures = [executor.submit(upload_chunk, item) for item in chunks]
                for future in as_completed(futures):
                    future.result()
        except Exception:
            self.run(f"rm -f {shell_quote(remote_tmp)}.part-* {shell_quote(remote_tmp)}")
            raise

        assemble = self.run(
            f"cat {shell_quote(remote_tmp)}.part-* > {shell_quote(remote_tmp)} && "
            f"rm -f {shell_quote(remote_tmp)}.part-*"
        )
        if assemble.returncode:
            self.run(f"rm -f {shell_quote(remote_tmp)}.part-* {shell_quote(remote_tmp)}")
            raise WorkspaceStateError(
                f"remote chunk assembly failed: "
                f"{assemble.stderr.strip() or assemble.stdout.strip()}"
            )

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
