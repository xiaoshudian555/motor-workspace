from __future__ import annotations

import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_execution import (  # noqa: E402
    NativeExecutionAdapter,
    ServiceTarget,
    SshExecutionAdapter,
    execution_adapter_for_machine,
)
from mws_transport import NativeTransport, SshScpTransport  # noqa: E402


def _machine(**overrides) -> dict:
    record = {
        "alias": "dev-native",
        "host": "npu-host-01",
        "user": "root",
        "port": 22,
        "mount_root": "/mnt",
        "remote_workspace_root": "/mnt/motor-workspace",
        "executor": "native",
    }
    record.update(overrides)
    return record


def _ssh_machine(**overrides) -> dict:
    return _machine(executor="ssh", **overrides)


def test_execution_adapter_factory_selects_native() -> None:
    adapter = execution_adapter_for_machine(_machine())
    assert isinstance(adapter, NativeExecutionAdapter)


def test_execution_adapter_factory_selects_ssh() -> None:
    adapter = execution_adapter_for_machine(_ssh_machine())
    assert isinstance(adapter, SshExecutionAdapter)


# ---------------------------------------------------------------------------
# NativeExecutionAdapter: real local execution
# ---------------------------------------------------------------------------


def test_native_adapter_run_and_write_read_roundtrip(tmp_path: Path) -> None:
    adapter = NativeExecutionAdapter(_machine(), NativeTransport(_machine()))
    target = tmp_path / "adapter.bin"
    adapter.write_bytes(str(target), b"\xde\xad\xbe\xef")
    assert adapter.read_bytes(str(target)) == b"\xde\xad\xbe\xef"
    result = adapter.run("echo native-adapter-ok")
    assert result.returncode == 0
    assert "native-adapter-ok" in result.stdout


def test_native_adapter_upload_file_and_hashes(tmp_path: Path) -> None:
    src = tmp_path / "src.yaml"
    dst_dir = tmp_path / "nested"
    src.write_text("kind: ConfigMap\n", encoding="utf-8")
    adapter = NativeExecutionAdapter(_machine(), NativeTransport(_machine()))
    adapter.upload_file(str(src), str(dst_dir / "dst.yaml"))
    adapter.mkdir(str(tmp_path / "hash-dir"))
    (tmp_path / "hash-dir" / "a.txt").write_bytes(b"a")
    hashes = adapter.directory_file_hashes(str(tmp_path / "hash-dir"))
    assert set(hashes) == {"a.txt"}


def test_native_adapter_git(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    adapter = NativeExecutionAdapter(_machine(), NativeTransport(_machine()))
    result = adapter.git(str(repo), "init")
    assert result.returncode == 0
    assert (repo / ".git").is_dir()


def test_native_adapter_kubectl_uses_local_binary(monkeypatch) -> None:
    adapter = NativeExecutionAdapter(_machine(), NativeTransport(_machine()))
    calls: list[str] = []

    def fake_run(command: str):
        calls.append(command)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(adapter.transport, "run", fake_run)
    result = adapter.kubectl("get", "pods", "-n", "ns1")
    assert result.returncode == 0
    assert calls == ["kubectl get pods -n ns1"]


def test_native_adapter_stage_files_roundtrip(tmp_path: Path) -> None:
    manifest = tmp_path / "demo.yaml"
    manifest.write_text("kind: ConfigMap\n", encoding="utf-8")
    adapter = NativeExecutionAdapter(_machine(), NativeTransport(_machine()))
    with adapter.stage_files([manifest], prefix="mws-adapter") as staged:
        remote_path = staged[manifest]
        assert remote_path.startswith("/tmp/mws-adapter-")
        assert remote_path.endswith("/000-demo.yaml")
        assert Path(remote_path).read_text(encoding="utf-8") == "kind: ConfigMap\n"
    assert not Path(remote_path).parent.exists()


def test_native_adapter_port_forward_with_cluster_ip_is_direct() -> None:
    machine = _machine()
    adapter = NativeExecutionAdapter(machine, NativeTransport(machine))
    target = ServiceTarget(
        namespace="ns1",
        service_name="mindie-motor-coordinator-mgmt",
        service_port=1026,
        kube_context="ctx-a",
        cluster_ip="10.107.213.17",
    )
    handle = adapter.port_forward(target)
    assert handle.target_host == "10.107.213.17"
    assert handle.local_port == 1026
    handle.close()


def test_native_adapter_host_port_forward_maps_local_to_remote() -> None:
    adapter = NativeExecutionAdapter(_machine(), NativeTransport(_machine()))
    handle = adapter.host_port_forward(3200)
    assert handle.target_host == "127.0.0.1"
    assert handle.local_port == 3200
    handle.close()


# ---------------------------------------------------------------------------
# SshExecutionAdapter: delegate to SshScpTransport, SSH-tunneled port-forward
# ---------------------------------------------------------------------------


def test_ssh_adapter_run_delegates_to_transport(monkeypatch) -> None:
    transport = SshScpTransport(_ssh_machine())
    adapter = SshExecutionAdapter(_ssh_machine(), transport)
    calls: list[str] = []

    def fake_run(command: str):
        calls.append(command)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="ssh-ok", stderr="")

    monkeypatch.setattr(transport, "run", fake_run)
    result = adapter.run("echo hello")
    assert result.returncode == 0
    assert calls == ["echo hello"]


def test_ssh_adapter_write_uses_upload_bytes(monkeypatch) -> None:
    transport = SshScpTransport(_ssh_machine())
    adapter = SshExecutionAdapter(_ssh_machine(), transport)
    uploads: list[tuple[str, bytes]] = []

    def fake_upload_bytes(path: str, data: bytes) -> None:
        uploads.append((path, data))

    monkeypatch.setattr(transport, "upload_bytes", fake_upload_bytes)
    adapter.write_bytes("/mnt/x.bin", b"\x01\x02")
    assert uploads == [("/mnt/x.bin", b"\x01\x02")]


def test_ssh_adapter_port_forward_builds_tunnel(monkeypatch) -> None:
    transport = SshScpTransport(_ssh_machine())
    monkeypatch.setattr(
        transport,
        "run",
        lambda command: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="19090\n", stderr=""
        ),
    )
    adapter = SshExecutionAdapter(_ssh_machine(), transport)
    monkeypatch.setattr("mws_execution._allocate_local_port", lambda: 18080)
    monkeypatch.setattr(
        "mws_execution.socket.create_connection",
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

    monkeypatch.setattr("mws_execution.subprocess.Popen", fake_popen)

    target = ServiceTarget(
        namespace="ns1",
        service_name="coordinator-infer",
        service_port=1025,
        kube_context="ctx-a",
    )
    with adapter.port_forward(target) as handle:
        assert handle.local_port == 18080

    command = captured["command"]
    assert isinstance(command, list)
    assert command[0] == "ssh"
    assert "127.0.0.1:18080:127.0.0.1:19090" in command
    assert "exec kubectl --context ctx-a port-forward" in command[-1]
    assert "19090:1025" in command[-1]


def test_ssh_adapter_stage_files_uses_run_and_upload(monkeypatch, tmp_path: Path) -> None:
    manifest = tmp_path / "demo.yaml"
    manifest.write_text("kind: ConfigMap\n", encoding="utf-8")
    transport = SshScpTransport(_ssh_machine())
    adapter = SshExecutionAdapter(_ssh_machine(), transport)
    commands: list[str] = []
    uploads: list[tuple[str, str]] = []

    def fake_run(command: str):
        commands.append(command)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    def fake_upload_file(local_path: str, remote_path: str) -> None:
        uploads.append((local_path, remote_path))

    monkeypatch.setattr(transport, "run", fake_run)
    monkeypatch.setattr(transport, "upload_file", fake_upload_file)

    with adapter.stage_files([manifest], prefix="mws-ssh") as staged:
        remote_path = staged[manifest]
        assert remote_path.startswith("/tmp/mws-ssh-")
        assert remote_path.endswith("/000-demo.yaml")

    assert uploads == [(str(manifest), remote_path)]
    assert commands[0].startswith("mkdir -p /tmp/mws-ssh-")
    assert commands[-1].startswith("rm -rf /tmp/mws-ssh-")


def test_ssh_adapter_host_port_forward_uses_no_command_tunnel(monkeypatch) -> None:
    transport = SshScpTransport(_ssh_machine())
    adapter = SshExecutionAdapter(_ssh_machine(), transport)
    monkeypatch.setattr("mws_execution._allocate_local_port", lambda: 18081)
    monkeypatch.setattr(
        "mws_execution.socket.create_connection",
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

    monkeypatch.setattr("mws_execution.subprocess.Popen", fake_popen)

    with adapter.host_port_forward(3200) as handle:
        assert handle.local_port == 18081

    command = captured["command"]
    assert isinstance(command, list)
    assert "127.0.0.1:18081:127.0.0.1:3200" in command
    assert "-N" in command
