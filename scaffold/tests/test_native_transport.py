from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_transport import NativeTransport, SshScpTransport, transport_for_machine  # noqa: E402


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


def test_transport_for_machine_returns_native_for_executor_native() -> None:
    transport = transport_for_machine(_machine())
    assert isinstance(transport, NativeTransport)


def test_transport_for_machine_defaults_to_ssh() -> None:
    machine = _machine(executor="ssh")
    transport = transport_for_machine(machine)
    assert isinstance(transport, SshScpTransport)


def test_transport_for_machine_fake_root_still_wins() -> None:
    transport = transport_for_machine(_machine(), fake_root=Path("/tmp/x"))
    assert not isinstance(transport, NativeTransport)


def test_native_run_executes_local_command(tmp_path: Path) -> None:
    target = tmp_path / "probe.txt"
    transport = NativeTransport(_machine())
    result = transport.run(f"echo native-ok > {target}")
    assert result.returncode == 0
    assert target.read_text().strip() == "native-ok"


def test_native_run_reports_nonzero() -> None:
    transport = NativeTransport(_machine())
    result = transport.run("exit 3")
    assert result.returncode == 3


def test_native_upload_and_read_bytes_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "file.bin"
    transport = NativeTransport(_machine())
    transport.upload_bytes(str(target), b"\x00\x01\x02")
    assert transport.read_bytes(str(target)) == b"\x00\x01\x02"


def test_native_upload_file_copies(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    dst = tmp_path / "dir" / "dst.txt"
    src.write_text("hello native", encoding="utf-8")
    transport = NativeTransport(_machine())
    transport.upload_file(str(src), str(dst))
    assert dst.read_text(encoding="utf-8") == "hello native"


def test_native_git_runs_local_git(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    transport = NativeTransport(_machine())
    result = transport.git(str(repo), "init")
    assert result.returncode == 0
    assert (repo / ".git").is_dir()


def test_native_directory_file_hashes_matches_files(tmp_path: Path) -> None:
    root = tmp_path / "dir"
    root.mkdir()
    (root / "a.txt").write_bytes(b"a")
    (root / "b.txt").write_bytes(b"bb")
    transport = NativeTransport(_machine())
    hashes = transport.directory_file_hashes(str(root))
    assert set(hashes) == {"a.txt", "b.txt"}
    assert hashes["a.txt"] != hashes["b.txt"]


def test_native_mkdir_via_base_class(tmp_path: Path) -> None:
    target = tmp_path / "one" / "two"
    transport = NativeTransport(_machine())
    transport.mkdir(str(target))
    assert target.is_dir()


def test_native_parity_lock_acquire_release(tmp_path: Path) -> None:
    lock = tmp_path / "locks" / ".parity-sync.lock"
    transport = NativeTransport(_machine())
    transport.acquire_parity_lock(str(lock))
    assert lock.is_dir()
    with pytest.raises(Exception):
        transport.acquire_parity_lock(str(lock))
    transport.release_parity_lock(str(lock))
    assert not lock.exists()
