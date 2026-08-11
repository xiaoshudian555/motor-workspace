from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCAFFOLD / ".agents" / "lib"))

from mws_build import (  # noqa: E402
    build_motor_wheel_in_docker,
    detect_build_gaps,
    reconcile_motor_wheel_override,
)
from mws_local_state import WorkspaceStateError  # noqa: E402


def _machine() -> dict:
    return {
        "alias": "dev1",
        "host": "1.2.3.4",
        "port": 22,
        "user": "root",
        "mount_root": "/mnt",
        "remote_workspace_root": "/mnt/motor-workspace",
    }


class _BuildAdapter:
    def __init__(self, *, reusable: bool = False) -> None:
        self.reusable = reusable
        self.commands: list[str] = []

    def run(self, command: str) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if "test -f" in command and "wheel.sha256" in command:
            return subprocess.CompletedProcess([], 0 if self.reusable else 1, "WHEEL_OK\n", "")
        if "command -v docker" in command:
            return subprocess.CompletedProcess([], 0, "OK\nDOCKER_OK\n", "")
        if command.startswith("docker run"):
            return subprocess.CompletedProcess([], 0, "BUILD_DONE\n", "")
        if "MWS_MOTOR_WHEEL_DIR_BEGIN" in command:
            return subprocess.CompletedProcess(
                [], 0, "BOOT_WHEEL_DIR_HARDCODED=/mnt/motor-workspace/wheels/dist\n", ""
            )
        return subprocess.CompletedProcess([], 0, "", "")

    def mkdir(self, path: str) -> None:
        self.commands.append(f"mkdir -p {path}")

    def read_bytes(self, path: str) -> bytes:
        return b""


def test_build_gap_detection_requires_generated_artifacts(tmp_path: Path) -> None:
    (tmp_path / "build.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (tmp_path / "motor/common/proto").mkdir(parents=True)
    (tmp_path / "motor/common/proto/kv.proto").write_text("syntax = 'proto3';\n", encoding="utf-8")

    result = detect_build_gaps(str(tmp_path))

    assert result["build_required"] is True
    assert {item["artifact"] for item in result["missing"]} == {
        "protobuf-generated",
        "kv-conductor",
    }


def test_build_requires_runtime_image() -> None:
    with pytest.raises(WorkspaceStateError, match="base_image_ref"):
        build_motor_wheel_in_docker(
            machine=_machine(), base_image_ref="", source_sha="abcdef123456"
        )


def test_build_runs_docker_and_records_fixed_output(monkeypatch) -> None:
    adapter = _BuildAdapter()
    monkeypatch.setattr("mws_build.execution_adapter_for_machine", lambda machine: adapter)

    result = build_motor_wheel_in_docker(
        machine=_machine(),
        base_image_ref="mindie-motor-vllm:3.0.0",
        source_sha="abcdef1234567890",
    )

    assert result["status"] == "ok"
    assert result["wheel_dir"] == "/mnt/motor-workspace/motor-wheel-builds/abcdef1234567890/dist"
    assert any(command.startswith("docker run") for command in adapter.commands)
    assert any("MWS_MOTOR_WHEEL_DIR_BEGIN" in command for command in adapter.commands)


def test_explicit_reuse_skips_docker(monkeypatch) -> None:
    adapter = _BuildAdapter(reusable=True)
    monkeypatch.setattr("mws_build.execution_adapter_for_machine", lambda machine: adapter)

    result = build_motor_wheel_in_docker(
        machine=_machine(),
        base_image_ref="mindie-motor-vllm:3.0.0",
        source_sha="abcdef1234567890",
        reuse=True,
    )

    assert result["reused"] is True
    assert not any(command.startswith("docker run") for command in adapter.commands)


class _BootAdapter:
    def __init__(self, boot: Path) -> None:
        self.boot = boot

    def run(self, command: str) -> subprocess.CompletedProcess[str]:
        if command.startswith("cat "):
            return subprocess.CompletedProcess([], 0, self.boot.read_text(encoding="utf-8"), "")
        if "python3 - <<'PY'" in command:
            body = command.split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
            proc = subprocess.run(
                [sys.executable, "-c", body], capture_output=True, text=True, check=False
            )
            return subprocess.CompletedProcess([], proc.returncode, proc.stdout, proc.stderr)
        return subprocess.CompletedProcess([], 0, "", "")


def test_wheel_override_round_trip(tmp_path: Path) -> None:
    source_root = tmp_path / "motor"
    boot = source_root / "examples/deployer/startup/boot.sh"
    boot.parent.mkdir(parents=True)
    shutil.copy2(SCAFFOLD.parent / "sources/motor/examples/deployer/startup/boot.sh", boot)
    original = boot.read_text(encoding="utf-8")
    adapter = _BootAdapter(boot)

    reconcile_motor_wheel_override(
        adapter, source_root=str(source_root), wheel_dir="/mnt/wheels/abc/dist"
    )
    assert 'MOTOR_WHEEL_DIR="/mnt/wheels/abc/dist"' in boot.read_text(encoding="utf-8")

    reconcile_motor_wheel_override(adapter, source_root=str(source_root), wheel_dir=None)
    assert boot.read_text(encoding="utf-8") == original
