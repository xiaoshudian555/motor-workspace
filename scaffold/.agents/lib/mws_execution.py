#!/usr/bin/env python3
"""ExecutionAdapter: unified execution boundary for local-control and remote-native.

The Workflow Core expresses only business actions; it never branches on concrete
transport classes. This module provides:

- `CommandResult`: the single result contract every adapter emits.
- `ServiceTarget` / `PortForwardHandle`: a unified service-access handle.
- `ExecutionAdapter` ABC plus `SshExecutionAdapter` (wraps SshScpTransport) and
  `NativeExecutionAdapter` (wraps NativeTransport).

Roadmap ref: scaffold/docs/remote-native-local-control-roadmap.md R1.
"""

from __future__ import annotations

import socket
import subprocess
import time
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from mws_local_state import WorkspaceStateError
from mws_transport import (
    NativeTransport,
    RemoteTransport,
    SshScpTransport,
    shell_quote,
    transport_for_machine,
)
from mws_validate import require_hostname

# Unified command-result contract. Both adapters emit CompletedProcess[str]
# exactly like the underlying transports, so Workflow Core consumers are
# unchanged.
CommandResult = subprocess.CompletedProcess[str]


@dataclass(frozen=True)
class ServiceTarget:
    namespace: str
    service_name: str
    service_port: int
    kube_context: str = ""
    cluster_ip: str = ""


class PortForwardHandle(ABC):
    """Unified handle exposing a local port and lifecycle close.

    Consumers connect to `target_host:local_port`:
    - SSH tunnel:  target_host is always 127.0.0.1 (local listener).
    - native direct ClusterIP:  target_host is the Service ClusterIP.
    """

    @property
    @abstractmethod
    def target_host(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def local_port(self) -> int:
        raise NotImplementedError

    @property
    @abstractmethod
    def log(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    def __enter__(self) -> "PortForwardHandle":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


class ExecutionAdapter(ABC):
    """Execution boundary shared by local-control (SSH) and remote-native.

    Workflow Core calls only these methods; adapter selection is done once in
    `execution_adapter_for_machine`.
    """

    def __init__(self, machine: dict[str, Any], transport: RemoteTransport) -> None:
        self.machine = machine
        self.transport = transport

    @abstractmethod
    def run(self, command: str) -> CommandResult:
        raise NotImplementedError

    @abstractmethod
    def read_bytes(self, path: str) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def write_bytes(self, path: str, data: bytes) -> None:
        raise NotImplementedError

    @abstractmethod
    def upload_file(self, local_path: str, remote_path: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def git(self, repo_dir: str, *args: str) -> CommandResult:
        raise NotImplementedError

    @abstractmethod
    def kubectl(self, *args: str) -> CommandResult:
        raise NotImplementedError

    @abstractmethod
    def mkdir(self, path: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def directory_file_hashes(self, remote_dir: str) -> dict[str, str]:
        raise NotImplementedError

    @contextmanager
    @abstractmethod
    def stage_files(
        self, paths: list[Path], *, prefix: str
    ) -> Iterator[dict[Path, str]]:
        raise NotImplementedError

    @abstractmethod
    def port_forward(self, target: ServiceTarget) -> PortForwardHandle:
        raise NotImplementedError

    @abstractmethod
    def host_port_forward(
        self, remote_port: int, *, remote_host: str = "127.0.0.1"
    ) -> PortForwardHandle:
        raise NotImplementedError


class SshExecutionAdapter(ExecutionAdapter):
    """Execution on the machine host through the SSH/SCP transport.

    port-forward uses a remote kubectl listener tunneled back over SSH.
    """

    def run(self, command: str) -> CommandResult:
        return self.transport.run(command)

    def read_bytes(self, path: str) -> bytes:
        return self.transport.read_bytes(path)

    def write_bytes(self, path: str, data: bytes) -> None:
        self.transport.upload_bytes(path, data)

    def upload_file(self, local_path: str, remote_path: str) -> None:
        self.transport.upload_file(local_path, remote_path)

    def git(self, repo_dir: str, *args: str) -> CommandResult:
        return self.transport.git(repo_dir, *args)

    def kubectl(self, *args: str) -> CommandResult:
        return self.transport.kubectl(*args)

    def mkdir(self, path: str) -> None:
        self.transport.mkdir(path)

    def directory_file_hashes(self, remote_dir: str) -> dict[str, str]:
        return self.transport.directory_file_hashes(remote_dir)

    @contextmanager
    def stage_files(
        self, paths: list[Path], *, prefix: str
    ) -> Iterator[dict[Path, str]]:
        remote_dir = f"/tmp/{prefix}-{uuid.uuid4().hex[:12]}"
        mkdir = self.run(f"mkdir -p {shell_quote(remote_dir)}")
        if mkdir.returncode:
            raise WorkspaceStateError(
                "could not create remote kubectl staging directory: "
                + (mkdir.stderr.strip() or mkdir.stdout.strip())
            )
        staged: dict[Path, str] = {}
        try:
            for index, path in enumerate(paths):
                remote_path = f"{remote_dir}/{index:03d}-{path.name}"
                self.upload_file(str(path), remote_path)
                staged[path] = remote_path
            yield staged
        finally:
            self.run(f"rm -rf {shell_quote(remote_dir)}")

    def port_forward(self, target: ServiceTarget) -> PortForwardHandle:
        return _SshKubectlPortForward(self, target=target)

    def host_port_forward(
        self, remote_port: int, *, remote_host: str = "127.0.0.1"
    ) -> PortForwardHandle:
        return _SshHostPortForward(
            self, remote_port=remote_port, remote_host=remote_host
        )


class NativeExecutionAdapter(ExecutionAdapter):
    """Execution directly on the current host (remote-native topology).

    port-forward runs a local `kubectl port-forward`; host_port_forward maps the
    remote port to itself since the Agent is already on the target host.
    """

    def run(self, command: str) -> CommandResult:
        return self.transport.run(command)

    def read_bytes(self, path: str) -> bytes:
        return self.transport.read_bytes(path)

    def write_bytes(self, path: str, data: bytes) -> None:
        self.transport.upload_bytes(path, data)

    def upload_file(self, local_path: str, remote_path: str) -> None:
        self.transport.upload_file(local_path, remote_path)

    def git(self, repo_dir: str, *args: str) -> CommandResult:
        return self.transport.git(repo_dir, *args)

    def kubectl(self, *args: str) -> CommandResult:
        return self.transport.kubectl(*args)

    def mkdir(self, path: str) -> None:
        self.transport.mkdir(path)

    def directory_file_hashes(self, remote_dir: str) -> dict[str, str]:
        return self.transport.directory_file_hashes(remote_dir)

    @contextmanager
    def stage_files(
        self, paths: list[Path], *, prefix: str
    ) -> Iterator[dict[Path, str]]:
        remote_dir = f"/tmp/{prefix}-{uuid.uuid4().hex[:12]}"
        mkdir = self.run(f"mkdir -p {shell_quote(remote_dir)}")
        if mkdir.returncode:
            raise WorkspaceStateError(
                "could not create native staging directory: "
                + (mkdir.stderr.strip() or mkdir.stdout.strip())
            )
        staged: dict[Path, str] = {}
        try:
            for index, path in enumerate(paths):
                remote_path = f"{remote_dir}/{index:03d}-{path.name}"
                self.upload_file(str(path), remote_path)
                staged[path] = remote_path
            yield staged
        finally:
            self.run(f"rm -rf {shell_quote(remote_dir)}")

    def port_forward(self, target: ServiceTarget) -> PortForwardHandle:
        if target.cluster_ip:
            return _NativeClusterIPAccess(self, target=target)
        return _NativeKubectlPortForward(self, target=target)

    def host_port_forward(
        self, remote_port: int, *, remote_host: str = "127.0.0.1"
    ) -> PortForwardHandle:
        del remote_host
        return _NativeHostPortForward(self, remote_port=remote_port)


def execution_adapter_for_machine(
    machine: dict[str, Any],
    *,
    fake_root: Path | None = None,
) -> ExecutionAdapter:
    """Build the execution adapter for a machine record (executor-aware)."""
    transport = transport_for_machine(machine, fake_root=fake_root)
    if isinstance(transport, NativeTransport):
        return NativeExecutionAdapter(machine, transport)
    if isinstance(transport, SshScpTransport):
        return SshExecutionAdapter(machine, transport)
    raise WorkspaceStateError(
        f"unsupported transport for execution adapter: {type(transport).__name__}"
    )


def _allocate_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _allocate_remote_port(transport: SshScpTransport) -> int:
    result = transport.run(
        "python3 -c "
        + shell_quote(
            "import socket; s=socket.socket(); s.bind(('127.0.0.1', 0)); "
            "print(s.getsockname()[1]); s.close()"
        )
    )
    try:
        port = int(result.stdout.strip())
    except ValueError as exc:
        raise WorkspaceStateError(
            "could not allocate remote kubectl port-forward port: "
            + (result.stderr.strip() or result.stdout.strip() or "invalid response")
        ) from exc
    if result.returncode or not 1 <= port <= 65535:
        raise WorkspaceStateError(
            "could not allocate remote kubectl port-forward port: "
            + (result.stderr.strip() or result.stdout.strip() or "invalid port")
        )
    return port


def _wait_for_listener(
    process: subprocess.Popen[str],
    local_port: int,
    *,
    label: str,
    startup_timeout: float = 15.0,
) -> None:
    deadline = time.monotonic() + startup_timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            detail = f"{stdout}\n{stderr}".strip()
            raise WorkspaceStateError(f"{label} exited early: {detail[-1000:]}")
        try:
            with socket.create_connection(("127.0.0.1", local_port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    process.terminate()
    try:
        process.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=3)
    raise WorkspaceStateError(f"timed out starting {label}")


class _SshKubectlPortForward(PortForwardHandle):
    def __init__(self, adapter: SshExecutionAdapter, *, target: ServiceTarget) -> None:
        self.adapter = adapter
        self.target = target
        self._local_port = 0
        self._log = ""
        self.process: subprocess.Popen[str] | None = None

    @property
    def target_host(self) -> str:
        return "127.0.0.1"

    @property
    def local_port(self) -> int:
        return self._local_port

    @property
    def log(self) -> str:
        return self._log

    def __enter__(self) -> "_SshKubectlPortForward":
        transport = self.adapter.transport
        assert isinstance(transport, SshScpTransport)
        context_args = (
            ["--context", self.target.kube_context] if self.target.kube_context else []
        )
        self._local_port = _allocate_local_port()
        remote_listener_port = _allocate_remote_port(transport)
        kubectl_args = [
            *context_args,
            "port-forward",
            "--address",
            "127.0.0.1",
            "-n",
            self.target.namespace,
            f"service/{self.target.service_name}",
            f"{remote_listener_port}:{self.target.service_port}",
        ]
        remote_command = "exec kubectl " + " ".join(
            shell_quote(arg) for arg in kubectl_args
        )
        command = transport.ssh_argv(
            remote_command,
            local_forward=(self._local_port, "127.0.0.1", remote_listener_port),
        )
        self.process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _wait_for_listener(
            self.process,
            self._local_port,
            label=f"remote kubectl port-forward for {self.target.service_name}",
        )
        return self

    def close(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                stdout, stderr = self.process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                stdout, stderr = self.process.communicate(timeout=3)
        else:
            stdout, stderr = self.process.communicate(timeout=1)
        self._log = f"{stdout}\n{stderr}".strip()
        self.process = None


class _NativeKubectlPortForward(PortForwardHandle):
    def __init__(
        self, adapter: NativeExecutionAdapter, *, target: ServiceTarget
    ) -> None:
        self.adapter = adapter
        self.target = target
        self._local_port = 0
        self._log = ""
        self.process: subprocess.Popen[str] | None = None

    @property
    def target_host(self) -> str:
        return "127.0.0.1"

    @property
    def local_port(self) -> int:
        return self._local_port

    @property
    def log(self) -> str:
        return self._log

    def __enter__(self) -> "_NativeKubectlPortForward":
        context_args = (
            ["--context", self.target.kube_context] if self.target.kube_context else []
        )
        self._local_port = _allocate_local_port()
        kubectl_args = [
            *context_args,
            "port-forward",
            "--address",
            "127.0.0.1",
            "-n",
            self.target.namespace,
            f"service/{self.target.service_name}",
            f"{self._local_port}:{self.target.service_port}",
        ]
        self.process = subprocess.Popen(
            ["kubectl", *kubectl_args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _wait_for_listener(
            self.process,
            self._local_port,
            label=f"native kubectl port-forward for {self.target.service_name}",
        )
        return self

    def close(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                stdout, stderr = self.process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                stdout, stderr = self.process.communicate(timeout=3)
        else:
            stdout, stderr = self.process.communicate(timeout=1)
        self._log = f"{stdout}\n{stderr}".strip()
        self.process = None


class _NativeClusterIPAccess(PortForwardHandle):
    """remote-native: the Agent can reach the ClusterIP directly through
    kube-proxy on the master host, so no port-forward/tunnel is needed."""

    def __init__(self, adapter: NativeExecutionAdapter, *, target: ServiceTarget) -> None:
        self.adapter = adapter
        self.target = target
        self._local_port = target.service_port
        self._log = f"direct ClusterIP {target.cluster_ip}:{target.service_port}"

    @property
    def target_host(self) -> str:
        return self.target.cluster_ip

    @property
    def local_port(self) -> int:
        return self._local_port

    @property
    def log(self) -> str:
        return self._log

    def close(self) -> None:
        return


class _SshHostPortForward(PortForwardHandle):
    def __init__(
        self,
        adapter: SshExecutionAdapter,
        *,
        remote_port: int,
        remote_host: str,
    ) -> None:
        self.adapter = adapter
        self.remote_port = remote_port
        self.remote_host = require_hostname(remote_host, label="remote_host")
        self._local_port = 0
        self._log = ""
        self.process: subprocess.Popen[str] | None = None

    @property
    def target_host(self) -> str:
        return "127.0.0.1"

    @property
    def local_port(self) -> int:
        return self._local_port

    @property
    def log(self) -> str:
        return self._log

    def __enter__(self) -> "_SshHostPortForward":
        if not 1 <= self.remote_port <= 65535:
            raise WorkspaceStateError("remote host forward port must be between 1 and 65535")
        transport = self.adapter.transport
        assert isinstance(transport, SshScpTransport)
        self._local_port = _allocate_local_port()
        command = transport.ssh_argv(
            "",
            local_forward=(self._local_port, self.remote_host, self.remote_port),
            no_command=True,
        )
        self.process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _wait_for_listener(
            self.process,
            self._local_port,
            label=f"remote host port forward for {self.remote_host}:{self.remote_port}",
        )
        return self

    def close(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                stdout, stderr = self.process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                stdout, stderr = self.process.communicate(timeout=3)
        else:
            stdout, stderr = self.process.communicate(timeout=1)
        self._log = f"{stdout}\n{stderr}".strip()
        self.process = None


class _NativeHostPortForward(PortForwardHandle):
    def __init__(self, adapter: NativeExecutionAdapter, *, remote_port: int) -> None:
        self.adapter = adapter
        self.remote_port = remote_port
        self._local_port = remote_port
        self._log = f"native host port forward {remote_port}"

    @property
    def target_host(self) -> str:
        return "127.0.0.1"

    @property
    def local_port(self) -> int:
        return self._local_port

    @property
    def log(self) -> str:
        return self._log

    def close(self) -> None:
        return


KubectlRunner = Callable[..., CommandResult]
