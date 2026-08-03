#!/usr/bin/env python3
"""Kubectl execution and service-access helpers over the execution adapter.

The old module branched on concrete transport classes
(`isinstance(transport, SshScpTransport)`). R1 removed that: every entry point
now builds an `ExecutionAdapter` through `execution_adapter_for_machine` and
delegates to its unified methods. The classes keep their historical signatures
so existing skill scripts and tests keep working.
"""

from __future__ import annotations

import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from mws_execution import (
    ExecutionAdapter,
    KubectlRunner,
    ServiceTarget,
    execution_adapter_for_machine,
)
from mws_local_state import WorkspaceStateError
from mws_validate import require_hostname

__all__ = [
    "build_kubectl_runner",
    "kubectl_available",
    "stage_remote_files",
    "RemoteKubectlPortForward",
    "RemoteHostPortForward",
    "KubectlRunner",
]


def build_kubectl_runner(
    machine: dict[str, Any],
    *,
    kube_context: str = "",
) -> KubectlRunner:
    """Return a runner that always executes kubectl through the machine adapter."""
    if not machine:
        raise WorkspaceStateError("machine record is required for remote kubectl")
    context_args = ["--context", kube_context] if kube_context else []
    adapter = execution_adapter_for_machine(machine)

    def remote_runner(*args: str) -> subprocess.CompletedProcess[str]:
        return adapter.kubectl(*context_args, *args)

    return remote_runner


def kubectl_available(
    machine: dict[str, Any],
    *,
    kube_context: str = "",
) -> tuple[bool, str]:
    """Probe kubectl on the machine host using its kubeconfig."""
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
    """Upload local files to a unique machine temporary directory and clean up."""
    adapter = execution_adapter_for_machine(machine)
    with adapter.stage_files(paths, prefix=prefix) as staged:
        yield staged


class _AdapterBackedForward:
    """Shared lifecycle for adapter-backed port-forward handles.

    Preserves the historical `.local_port` / `.log` attributes and `with`
    support while delegating the actual work to the machine execution adapter.
    """

    def __init__(self, machine: dict[str, Any]) -> None:
        self._machine = machine
        self._adapter: ExecutionAdapter = execution_adapter_for_machine(machine)
        self._handle = None
        self._local_port = 0
        self._log = ""

    @property
    def local_port(self) -> int:
        return self._local_port

    @property
    def log(self) -> str:
        return self._log

    def __enter__(self):
        self._handle = self._enter_adapter(self._adapter)
        self._local_port = self._handle.local_port
        self._log = self._handle.log
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def _enter_adapter(self, adapter: ExecutionAdapter):
        raise NotImplementedError


class RemoteKubectlPortForward(_AdapterBackedForward):
    """Run kubectl port-forward for a Coordinator Service on the machine host."""

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
        super().__init__(machine)
        self.kube_context = kube_context
        self.namespace = namespace
        self.service_name = service_name
        self.service_port = service_port
        self.startup_timeout = startup_timeout

    def _enter_adapter(self, adapter: ExecutionAdapter):
        target = ServiceTarget(
            namespace=self.namespace,
            service_name=self.service_name,
            service_port=self.service_port,
            kube_context=self.kube_context,
        )
        handle = adapter.port_forward(target)
        return handle.__enter__()


class RemoteHostPortForward(_AdapterBackedForward):
    """Tunnel a TCP port on the machine host back to localhost (or map it natively)."""

    def __init__(
        self,
        machine: dict[str, Any],
        remote_port: int,
        *,
        remote_host: str = "127.0.0.1",
        startup_timeout: float = 15.0,
    ) -> None:
        super().__init__(machine)
        self.remote_port = remote_port
        self.remote_host = require_hostname(remote_host, label="remote_host")
        self.startup_timeout = startup_timeout

    def _enter_adapter(self, adapter: ExecutionAdapter):
        handle = adapter.host_port_forward(
            self.remote_port, remote_host=self.remote_host
        )
        return handle.__enter__()
