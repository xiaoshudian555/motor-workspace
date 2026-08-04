from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCAFFOLD = Path(__file__).resolve().parents[1]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_build import (  # noqa: E402
    detect_build_gaps,
    motor_source_root,
    build_output_root,
    build_motor_wheel_in_docker,
    render_wheel_replace_manifest,
    build_wheel_run_envelope,
)
from mws_local_state import WorkspaceStateError  # noqa: E402


def _machine():
    return {
        "alias": "dev1",
        "host": "1.2.3.4",
        "port": 22,
        "user": "root",
        "mount_root": "/mnt",
        "remote_workspace_root": "/mnt/motor-workspace",
        "kube_context": "ctx-a",
    }


class _FakeAdapter:
    """Minimal ExecutionAdapter stub that responds to the wheel-build commands."""

    def __init__(self, *, docker_ok: bool = True, build_ok: bool = True) -> None:
        self.docker_ok = docker_ok
        self.build_ok = build_ok
        self.commands: list[str] = []

    def run(self, command: str) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if "test -f" in command and "command -v docker" in command:
            parts = ["OK", "DOCKER_OK"] if self.docker_ok else ["OK"]
            return subprocess.CompletedProcess([], 0, "\n".join(parts) + "\n", "")
        if "test -f" in command and "wheel.sha256" in command:
            return subprocess.CompletedProcess([], 1, "", "")
        if command.startswith("docker run"):
            if self.build_ok:
                return subprocess.CompletedProcess([], 0, "BUILD_DONE\n", "")
            return subprocess.CompletedProcess([], 1, "", "cargo build failed")
        if command.startswith("mkdir -p"):
            return subprocess.CompletedProcess([], 0, "", "")
        return subprocess.CompletedProcess([], 0, "", "")

    def mkdir(self, path: str) -> None:
        self.commands.append(f"mkdir -p {path}")

    def read_bytes(self, path: str) -> bytes:
        return b""


def test_detect_build_gaps_no_gaps(tmp_path) -> None:
    (tmp_path / "build.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (tmp_path / "motor").mkdir()
    (tmp_path / "motor" / "kv_conductor" / "bin").mkdir(parents=True)
    (tmp_path / "motor" / "kv_conductor" / "bin" / "kv-conductor").write_bytes(b"\x7fELF")
    result = detect_build_gaps(str(tmp_path))
    assert result["build_required"] is False
    assert result["missing"] == []


def test_detect_build_gaps_missing_pb2_and_rust(tmp_path) -> None:
    (tmp_path / "build.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (tmp_path / "motor").mkdir()
    (tmp_path / "motor" / "common" / "proto").mkdir(parents=True)
    (tmp_path / "motor" / "common" / "proto" / "kv.proto").write_text(
        "syntax = 'proto3';\n", encoding="utf-8"
    )
    result = detect_build_gaps(str(tmp_path))
    assert result["build_required"] is True
    kinds = {item["artifact"] for item in result["missing"]}
    assert "protobuf-generated" in kinds
    assert "kv-conductor" in kinds


def test_motor_paths_derive_from_machine() -> None:
    assert motor_source_root(_machine()) == "/mnt/motor-workspace/motor"
    assert build_output_root(_machine()) == "/mnt/motor-workspace/motor-wheel-builds"


def test_build_wheel_requires_base_image() -> None:
    import pytest

    with pytest.raises(WorkspaceStateError, match="base_image_ref"):
        build_motor_wheel_in_docker(
            machine=_machine(),
            base_image_ref="",
            source_sha="abcdef123456",
        )


def test_build_wheel_runs_docker_and_records_artifacts(monkeypatch) -> None:
    fake = _FakeAdapter(docker_ok=True, build_ok=True)
    monkeypatch.setattr(
        "mws_build.execution_adapter_for_machine", lambda machine: fake
    )
    result = build_motor_wheel_in_docker(
        machine=_machine(),
        base_image_ref="mindie-motor-vllm:3.0.0",
        source_sha="abcdef1234567890",
    )
    assert result["status"] == "ok"
    assert result["wheel_dir"] == (
        "/mnt/motor-workspace/motor-wheel-builds/abcdef1234567890/dist"
    )
    assert result["reused"] is False
    assert any(cmd.startswith("docker run") for cmd in fake.commands)
    assert any("build.sh" in cmd for cmd in fake.commands)


def test_build_wheel_reuses_when_marker_present(monkeypatch) -> None:
    class _ReuseAdapter(_FakeAdapter):
        def run(self, command: str) -> subprocess.CompletedProcess[str]:
            self.commands.append(command)
            if "test -f" in command and "wheel.sha256" in command:
                return subprocess.CompletedProcess([], 0, "WHEEL_OK\n", "")
            return subprocess.CompletedProcess([], 0, "", "")

    fake = _ReuseAdapter()
    monkeypatch.setattr(
        "mws_build.execution_adapter_for_machine", lambda machine: fake
    )
    result = build_motor_wheel_in_docker(
        machine=_machine(),
        base_image_ref="mindie-motor-vllm:3.0.0",
        source_sha="abcdef1234567890",
        reuse=True,
    )
    assert result["reused"] is True
    assert not any(cmd.startswith("docker run") for cmd in fake.commands)


def test_render_wheel_replace_manifest() -> None:
    manifest = render_wheel_replace_manifest(
        wheel_dir="/mnt/motor-workspace/motor-wheel-builds/abcdef/dist",
        namespace="ns1",
        container="wheel-replace",
        image="mindie-motor-vllm:3.0.0",
        replace_path="/mnt/wheels",
    )
    assert manifest["kind"] == "Job"
    assert manifest["metadata"]["namespace"] == "ns1"
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["command"][:2] == ["bash", "-c"]
    assert "pip install" in container["command"][2]
    volume = manifest["spec"]["template"]["spec"]["volumes"][0]
    assert volume["hostPath"]["path"] == (
        "/mnt/motor-workspace/motor-wheel-builds/abcdef/dist"
    )


def test_build_wheel_run_envelope_ready() -> None:
    result = {
        "status": "ok",
        "source_sha": "abcdef123456",
        "base_image_ref": "img:1",
        "wheel_dir": "/mnt/wheels/dist",
        "build_dir": "/mnt/wheels",
        "reused": False,
        "machine": "dev1",
    }
    env = build_wheel_run_envelope(
        run_id="wheel-1",
        workflow_run_id="wf-1",
        build_result=result,
        started_at="2026-01-01T00:00:00Z",
    )
    assert env["kind"] == "motor-wheel-build"
    assert env["status"] == "ready"
    assert env["artifacts"][0]["name"] == "motor-wheel"
    assert env["artifacts"][0]["path"] == "/mnt/wheels/dist"
