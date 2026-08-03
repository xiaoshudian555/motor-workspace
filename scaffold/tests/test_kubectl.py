from __future__ import annotations

import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[1]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_kubectl import (  # noqa: E402
    RemoteHostPortForward,
    RemoteKubectlPortForward,
    build_kubectl_runner,
    stage_remote_files,
)
from mws_transport import NativeTransport, SshScpTransport  # noqa: E402


def _machine() -> dict:
    return {
        "alias": "dev1",
        "host": "1.2.3.4",
        "port": 22,
        "user": "root",
        "kube_context": "ctx-a",
    }


def test_kubectl_runner_always_uses_remote_transport(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    class FakeTransport:
        def kubectl(self, *args: str) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("mws_kubectl.transport_for_machine", lambda machine: FakeTransport())
    result = build_kubectl_runner(_machine(), kube_context="ctx-a")(
        "get", "pods", "-n", "ns1"
    )

    assert result.returncode == 0
    assert calls == [("--context", "ctx-a", "get", "pods", "-n", "ns1")]


def test_stage_remote_files_uploads_and_cleans_unique_directory(monkeypatch, tmp_path) -> None:
    manifest = tmp_path / "demo.yaml"
    manifest.write_text("kind: ConfigMap\n", encoding="utf-8")
    commands: list[str] = []
    uploads: list[tuple[str, str]] = []

    class FakeTransport:
        def run(self, command: str) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        def upload_file(self, local_path: str, remote_path: str) -> None:
            uploads.append((local_path, remote_path))

    monkeypatch.setattr("mws_kubectl.transport_for_machine", lambda machine: FakeTransport())
    with stage_remote_files(_machine(), [manifest], prefix="mws-test") as staged:
        remote_path = staged[manifest]
        assert remote_path.startswith("/tmp/mws-test-")
        assert remote_path.endswith("/000-demo.yaml")

    assert uploads == [(str(manifest), remote_path)]
    assert commands[0].startswith("mkdir -p /tmp/mws-test-")
    assert commands[-1].startswith("rm -rf /tmp/mws-test-")


def test_port_forward_runs_remote_kubectl_behind_ssh_tunnel(monkeypatch) -> None:
    transport = SshScpTransport(_machine())
    monkeypatch.setattr(
        transport,
        "run",
        lambda command: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="19090\n", stderr=""
        ),
    )
    monkeypatch.setattr("mws_kubectl.transport_for_machine", lambda machine: transport)
    monkeypatch.setattr("mws_kubectl._allocate_local_port", lambda: 18080)
    monkeypatch.setattr(
        "mws_kubectl.socket.create_connection",
        lambda *args, **kwargs: nullcontext(),
    )

    captured: dict[str, object] = {}

    class FakeProcess:
        def __init__(self) -> None:
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self) -> None:
            self.running = False

        def kill(self) -> None:
            self.running = False

        def communicate(self, timeout=None):
            return "", ""

    def fake_popen(command, **kwargs):
        captured["command"] = command
        return FakeProcess()

    monkeypatch.setattr("mws_kubectl.subprocess.Popen", fake_popen)

    with RemoteKubectlPortForward(
        _machine(), "ctx-a", "ns1", "coordinator-infer", 1025
    ) as forward:
        assert forward.local_port == 18080

    command = captured["command"]
    assert isinstance(command, list)
    assert command[0] == "ssh"
    assert "127.0.0.1:18080:127.0.0.1:19090" in command
    assert "exec kubectl --context ctx-a port-forward" in command[-1]
    assert "19090:1025" in command[-1]


def test_port_forward_native_runs_local_kubectl_without_ssh(monkeypatch) -> None:
    machine = {**_machine(), "executor": "native"}
    captured: dict[str, object] = {}

    class FakeProcess:
        def __init__(self) -> None:
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self) -> None:
            self.running = False

        def kill(self) -> None:
            self.running = False

        def communicate(self, timeout=None):
            return "", ""

    def fake_popen(command, **kwargs):
        captured["command"] = command
        return FakeProcess()

    monkeypatch.setattr("mws_kubectl.transport_for_machine", lambda m: NativeTransport(m))
    monkeypatch.setattr("mws_kubectl._allocate_local_port", lambda: 18082)
    monkeypatch.setattr(
        "mws_kubectl.socket.create_connection",
        lambda *args, **kwargs: nullcontext(),
    )
    monkeypatch.setattr("mws_kubectl.subprocess.Popen", fake_popen)

    with RemoteKubectlPortForward(
        machine, "ctx-a", "ns1", "coordinator-infer", 1025
    ) as forward:
        assert forward.local_port == 18082

    command = captured["command"]
    assert isinstance(command, list)
    assert command[0] == "kubectl"
    assert "port-forward" in command
    assert "18082:1025" in command


def test_remote_host_port_forward_native_maps_local_to_remote(monkeypatch) -> None:
    machine = {**_machine(), "executor": "native"}
    monkeypatch.setattr("mws_kubectl.transport_for_machine", lambda m: NativeTransport(m))

    with RemoteHostPortForward(machine, 3200) as forward:
        # native: no tunnel, local port is the remote port directly
        assert forward.local_port == 3200


def test_remote_host_port_forward_uses_ssh_no_command_tunnel(monkeypatch) -> None:
    transport = SshScpTransport(_machine())
    monkeypatch.setattr("mws_kubectl.transport_for_machine", lambda machine: transport)
    monkeypatch.setattr("mws_kubectl._allocate_local_port", lambda: 18081)
    monkeypatch.setattr(
        "mws_kubectl.socket.create_connection",
        lambda *args, **kwargs: nullcontext(),
    )
    captured: dict[str, object] = {}

    class FakeProcess:
        def __init__(self) -> None:
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self) -> None:
            self.running = False

        def kill(self) -> None:
            self.running = False

        def communicate(self, timeout=None):
            return "", ""

    def fake_popen(command, **kwargs):
        captured["command"] = command
        return FakeProcess()

    monkeypatch.setattr("mws_kubectl.subprocess.Popen", fake_popen)

    with RemoteHostPortForward(_machine(), 3200) as forward:
        assert forward.local_port == 18081

    command = captured["command"]
    assert isinstance(command, list)
    assert "127.0.0.1:18081:127.0.0.1:3200" in command
    assert "-N" in command
