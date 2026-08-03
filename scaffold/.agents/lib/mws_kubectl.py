#!/usr/bin/env python3
"""Remote-only kubectl execution and forwarding helpers."""

from __future__ import annotations

import socket
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from mws_local_state import WorkspaceStateError
from mws_transport import SshScpTransport, shell_quote, transport_for_machine
from mws_validate import require_hostname

KubectlRunner = Callable[..., subprocess.CompletedProcess[str]]


def build_kubectl_runner(
    machine: dict[str, Any],
    *,
    kube_context: str = "",
) -> KubectlRunner:
    """Return a runner that always executes kubectl on the selected machine host."""
    if not machine:
        raise WorkspaceStateError("machine record is required for remote kubectl")
    context_args = ["--context", kube_context] if kube_context else []
    transport = transport_for_machine(machine)

    def remote_runner(*args: str) -> subprocess.CompletedProcess[str]:
        return transport.kubectl(*context_args, *args)

    return remote_runner


def kubectl_available(
    machine: dict[str, Any],
    *,
    kube_context: str = "",
) -> tuple[bool, str]:
    """Probe kubectl on the selected machine host using its kubeconfig."""
    result = build_kubectl_runner(machine, kube_context=kube_context)("version", "--client")
    if result.returncode == 0:
        return True, "remote kubectl available on machine host"
    return False, result.stderr.strip() or "remote kubectl probe failed"


@contextmanager
def stage_remote_files(
    machine: dict[str, Any],
    paths: list[Path],
    *,
    prefix: str,
) -> Iterator[dict[Path, str]]:
    """Upload local files to a unique remote temporary directory and clean it up."""
    transport = transport_for_machine(machine)
    remote_dir = f"/tmp/{prefix}-{uuid.uuid4().hex[:12]}"
    mkdir = transport.run(f"mkdir -p {shell_quote(remote_dir)}")
    if mkdir.returncode:
        raise WorkspaceStateError(
            "could not create remote kubectl staging directory: "
            + (mkdir.stderr.strip() or mkdir.stdout.strip())
        )
    staged: dict[Path, str] = {}
    try:
        for index, path in enumerate(paths):
            remote_path = f"{remote_dir}/{index:03d}-{path.name}"
            transport.upload_file(str(path), remote_path)
            staged[path] = remote_path
        yield staged
    finally:
        transport.run(f"rm -rf {shell_quote(remote_dir)}")


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


class RemoteKubectlPortForward:
    """Run kubectl port-forward remotely and tunnel its listener back locally."""

    def __init__(
        self,
        machine: dict[str, Any],
        kube_context: str,
        namespace: str,
        service_name: str,
        service_port: int,
        *,
        startup_timeout: float = 15.0,
    ) -> None:
        self.machine = machine
        self.kube_context = kube_context
        self.namespace = namespace
        self.service_name = service_name
        self.service_port = service_port
        self.startup_timeout = startup_timeout
        self.process: subprocess.Popen[str] | None = None
        self.local_port = 0
        self.log = ""

    def __enter__(self) -> "RemoteKubectlPortForward":
        transport = transport_for_machine(self.machine)
        context_args = ["--context", self.kube_context] if self.kube_context else []
        self.local_port = _allocate_local_port()
        if isinstance(transport, SshScpTransport):
            remote_listener_port = _allocate_remote_port(transport)
            kubectl_args = [
                *context_args,
                "port-forward",
                "--address",
                "127.0.0.1",
                "-n",
                self.namespace,
                f"service/{self.service_name}",
                f"{remote_listener_port}:{self.service_port}",
            ]
            remote_command = "exec kubectl " + " ".join(shell_quote(arg) for arg in kubectl_args)
            command = transport.ssh_argv(
                remote_command,
                local_forward=(self.local_port, "127.0.0.1", remote_listener_port),
            )
            self.process = subprocess.Popen(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        else:
            kubectl_args = [
                *context_args,
                "port-forward",
                "--address",
                "127.0.0.1",
                "-n",
                self.namespace,
                f"service/{self.service_name}",
                f"{self.local_port}:{self.service_port}",
            ]
            self.process = subprocess.Popen(
                ["kubectl", *kubectl_args],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                stdout, stderr = self.process.communicate(timeout=1)
                self.log = f"{stdout}\n{stderr}".strip()
                raise WorkspaceStateError(
                    f"remote kubectl port-forward for {self.service_name} exited early: "
                    f"{self.log[-1000:]}"
                )
            try:
                with socket.create_connection(("127.0.0.1", self.local_port), timeout=0.2):
                    return self
            except OSError:
                time.sleep(0.1)
        self.close()
        raise WorkspaceStateError(
            f"timed out starting remote kubectl port-forward for {self.service_name}"
        )

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
        self.log = f"{stdout}\n{stderr}".strip()
        self.process = None

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


class RemoteHostPortForward:
    """Tunnel a TCP port on the selected remote host back to localhost."""

    def __init__(
        self,
        machine: dict[str, Any],
        remote_port: int,
        *,
        remote_host: str = "127.0.0.1",
        startup_timeout: float = 15.0,
    ) -> None:
        self.machine = machine
        self.remote_port = remote_port
        self.remote_host = require_hostname(remote_host, label="remote_host")
        self.startup_timeout = startup_timeout
        self.process: subprocess.Popen[str] | None = None
        self.local_port = 0
        self.log = ""

    def __enter__(self) -> "RemoteHostPortForward":
        if not 1 <= self.remote_port <= 65535:
            raise WorkspaceStateError("remote host forward port must be between 1 and 65535")
        transport = transport_for_machine(self.machine)
        if not isinstance(transport, SshScpTransport):
            self.local_port = self.remote_port
            return self

        self.local_port = _allocate_local_port()
        command = transport.ssh_argv(
            "",
            local_forward=(self.local_port, self.remote_host, self.remote_port),
            no_command=True,
        )
        self.process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                stdout, stderr = self.process.communicate(timeout=1)
                self.log = f"{stdout}\n{stderr}".strip()
                raise WorkspaceStateError(
                    f"remote host port forward for {self.remote_host}:{self.remote_port} "
                    f"exited early: {self.log[-1000:]}"
                )
            try:
                with socket.create_connection(("127.0.0.1", self.local_port), timeout=0.2):
                    return self
            except OSError:
                time.sleep(0.1)
        self.close()
        raise WorkspaceStateError(
            f"timed out starting remote host port forward for "
            f"{self.remote_host}:{self.remote_port}"
        )

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
        self.log = f"{stdout}\n{stderr}".strip()
        self.process = None

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
