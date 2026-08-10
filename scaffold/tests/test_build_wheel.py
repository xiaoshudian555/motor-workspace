from __future__ import annotations

import json
import os
import shutil
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
    build_wheel_run_envelope,
    reconcile_motor_wheel_override,
)
from mws_local_state import WorkspaceStateError  # noqa: E402


BOOT_SH = SCAFFOLD.parent / "sources/motor/examples/deployer/startup/boot.sh"


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
        if "BOOT_WHEEL_DIR_HARDCODED" in command or "MWS_MOTOR_WHEEL_DIR_BEGIN" in command:
            return subprocess.CompletedProcess(
                [], 0, "BOOT_WHEEL_DIR_HARDCODED=/mnt/motor-workspace/motor-wheel-builds/x/dist\n", ""
            )
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
    assert any("MWS_MOTOR_WHEEL_DIR_BEGIN" in cmd for cmd in fake.commands)
    assert result["boot_sh_path"].endswith("examples/deployer/startup/boot.sh")


def test_build_wheel_reuses_when_marker_present(monkeypatch) -> None:
    class _ReuseAdapter(_FakeAdapter):
        def run(self, command: str) -> subprocess.CompletedProcess[str]:
            self.commands.append(command)
            if "test -f" in command and "wheel.sha256" in command:
                return subprocess.CompletedProcess([], 0, "WHEEL_OK\n", "")
            if "MWS_MOTOR_WHEEL_DIR_BEGIN" in command:
                return subprocess.CompletedProcess(
                    [], 0, "BOOT_WHEEL_DIR_HARDCODED=/mnt/x\n", ""
                )
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
    assert any("MWS_MOTOR_WHEEL_DIR_BEGIN" in cmd for cmd in fake.commands)


def test_build_wheel_rebuilds_by_default_when_marker_present(monkeypatch) -> None:
    class _ExistingWheelAdapter(_FakeAdapter):
        def run(self, command: str) -> subprocess.CompletedProcess[str]:
            self.commands.append(command)
            if "test -f" in command and "wheel.sha256" in command:
                return subprocess.CompletedProcess([], 0, "WHEEL_OK\n", "")
            if "command -v docker" in command:
                return subprocess.CompletedProcess([], 0, "OK\nDOCKER_OK\n", "")
            if command.startswith("docker run"):
                return subprocess.CompletedProcess([], 0, "BUILD_DONE\n", "")
            if "MWS_MOTOR_WHEEL_DIR_BEGIN" in command:
                return subprocess.CompletedProcess(
                    [], 0, "BOOT_WHEEL_DIR_HARDCODED=/mnt/x\n", ""
                )
            return subprocess.CompletedProcess([], 0, "", "")

    fake = _ExistingWheelAdapter()
    monkeypatch.setattr(
        "mws_build.execution_adapter_for_machine", lambda machine: fake
    )
    result = build_motor_wheel_in_docker(
        machine=_machine(),
        base_image_ref="mindie-motor-vllm:3.0.0",
        source_sha="abcdef1234567890",
    )
    assert result["reused"] is False
    assert any(cmd.startswith("docker run") for cmd in fake.commands)


def test_build_wheel_run_envelope_ready() -> None:
    result = {
        "status": "ok",
        "source_sha": "abcdef123456",
        "base_image_ref": "img:1",
        "wheel_dir": "/mnt/wheels/dist",
        "build_dir": "/mnt/wheels",
        "reused": False,
        "machine": "dev1",
        "boot_sh_path": "/mnt/motor-workspace/motor/examples/deployer/startup/boot.sh",
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
    assert env["boot_sh_path"].endswith("examples/deployer/startup/boot.sh")


def _run_hardcoded_boot(
    tmp_path: Path,
    *,
    wheel_count: int,
    pip_exit: int = 0,
) -> subprocess.CompletedProcess[str]:
    startup = tmp_path / "startup"
    startup.mkdir(parents=True)
    boot = startup / "boot.sh"
    shutil.copy2(BOOT_SH, boot)
    (startup / "common.sh").write_text("set_common_env() { :; }\n", encoding="utf-8")
    (startup / "engine.sh").write_text("echo role-started\n", encoding="utf-8")

    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    for index in range(wheel_count):
        (wheel_dir / f"motor-{index}.whl").touch()

    text = boot.read_text(encoding="utf-8")
    needle = 'if [ -n "${MOTOR_WHEEL_DIR:-}" ]; then'
    text = text.replace(
        needle,
        f'# >>> MWS_MOTOR_WHEEL_DIR_BEGIN\nMOTOR_WHEEL_DIR="{wheel_dir}"\n'
        f'# <<< MWS_MOTOR_WHEEL_DIR_END\n{needle}',
        1,
    )
    boot.write_text(text, encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        "#!/bin/bash\nprintf '%s\\n' \"$@\" > \"$PIP_ARGS_FILE\"\n"
        "exit \"$FAKE_PIP_EXIT\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "ROLE": "prefill",
            "PIP_ARGS_FILE": str(tmp_path / "pip-args.txt"),
            "FAKE_PIP_EXIT": str(pip_exit),
        }
    )
    return subprocess.run(
        ["bash", str(boot)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_hardcoded_boot_installs_exactly_one_motor_wheel(tmp_path) -> None:
    result = _run_hardcoded_boot(tmp_path, wheel_count=1)

    assert result.returncode == 0
    assert "motor wheel override installed" in result.stdout
    assert "role-started" in result.stdout
    assert (tmp_path / "pip-args.txt").read_text(encoding="utf-8").splitlines() == [
        "-m",
        "pip",
        "install",
        "--no-cache-dir",
        "--no-deps",
        "--force-reinstall",
        "--no-index",
        str(tmp_path / "wheels/motor-0.whl"),
    ]


def test_hardcoded_boot_rejects_missing_or_ambiguous_wheel(tmp_path) -> None:
    missing = _run_hardcoded_boot(tmp_path / "missing", wheel_count=0)
    ambiguous = _run_hardcoded_boot(tmp_path / "ambiguous", wheel_count=2)

    assert missing.returncode == 1
    assert ambiguous.returncode == 1
    assert "must contain exactly one motor-*.whl" in missing.stderr
    assert "must contain exactly one motor-*.whl" in ambiguous.stderr


def test_hardcoded_boot_stops_when_pip_install_fails(tmp_path) -> None:
    result = _run_hardcoded_boot(tmp_path, wheel_count=1, pip_exit=1)

    assert result.returncode == 1
    assert "motor wheel override install failed" in result.stderr
    assert "role-started" not in result.stdout


class _BootShAdapter:
    """Execute the reconcile heredoc for real against a local boot.sh file."""

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


def _write_source_tree_boot(tmp_path: Path) -> tuple[str, Path]:
    source_root = tmp_path / "motor"
    boot = source_root / "examples" / "deployer" / "startup" / "boot.sh"
    boot.parent.mkdir(parents=True)
    shutil.copy2(BOOT_SH, boot)
    return str(source_root), boot


def test_reconcile_motor_wheel_writes_and_verifies_block(tmp_path) -> None:
    source_root, boot = _write_source_tree_boot(tmp_path)
    adapter = _BootShAdapter(boot)

    reconcile_motor_wheel_override(
        adapter, source_root=source_root, wheel_dir="/mnt/wheels/abc/dist"
    )

    content = boot.read_text(encoding="utf-8")
    assert "# >>> MWS_MOTOR_WHEEL_DIR_BEGIN" in content
    assert 'MOTOR_WHEEL_DIR="/mnt/wheels/abc/dist"' in content
    assert 'if [ -n "${MOTOR_WHEEL_DIR:-}" ]; then' in content


def test_reconcile_image_removes_block_idempotently(tmp_path) -> None:
    source_root, boot = _write_source_tree_boot(tmp_path)
    adapter = _BootShAdapter(boot)
    original = boot.read_text(encoding="utf-8")

    reconcile_motor_wheel_override(adapter, source_root=source_root, wheel_dir=None)
    assert boot.read_text(encoding="utf-8") == original

    reconcile_motor_wheel_override(
        adapter, source_root=source_root, wheel_dir="/mnt/wheels/abc/dist"
    )
    assert "MWS_MOTOR_WHEEL_DIR_BEGIN" in boot.read_text(encoding="utf-8")

    reconcile_motor_wheel_override(adapter, source_root=source_root, wheel_dir=None)
    assert boot.read_text(encoding="utf-8") == original


def test_reconcile_detects_content_mismatch(tmp_path) -> None:
    source_root, boot = _write_source_tree_boot(tmp_path)

    class _CorruptAdapter(_BootShAdapter):
        def run(self, command: str) -> subprocess.CompletedProcess[str]:
            result = super().run(command)
            if "python3 - <<'PY'" in command and "MWS_MOTOR_WHEEL_DIR_BEGIN" in command:
                self.boot.write_text("# corrupted\n", encoding="utf-8")
            return result

    import pytest

    with pytest.raises(WorkspaceStateError, match="does not match bundle wheel_dir"):
        reconcile_motor_wheel_override(
            _CorruptAdapter(boot), source_root=source_root, wheel_dir="/mnt/wheels/abc/dist"
        )
