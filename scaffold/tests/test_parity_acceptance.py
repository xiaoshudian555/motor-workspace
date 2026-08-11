from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCAFFOLD / ".agents" / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from git_fixtures import init_repo  # noqa: E402
from mws_local_state import save_inventory  # noqa: E402
from mws_parity import load_parity_state, sync_workspace_to_remote  # noqa: E402
from mws_transport import FakeRemoteTransport  # noqa: E402


def _machine() -> dict:
    return {
        "alias": "dev1",
        "host": "dev1",
        "user": "root",
        "port": 22,
        "mount_root": "/mnt",
        "remote_workspace_root": "/mnt/motor-workspace",
        "parity_backend": "shared-hostpath",
    }


@pytest.fixture
def parity_env(tmp_path: Path, monkeypatch) -> Path:
    state_root = tmp_path / "state"
    state_root.mkdir()
    inventory_path = state_root / "machine-inventory.json"
    monkeypatch.setattr("mws_local_state.LOCAL_ROOT", state_root)
    monkeypatch.setattr("mws_local_state.INVENTORY_PATH", inventory_path)
    monkeypatch.setattr(
        "mws_local_state.INVENTORY_LOCK_PATH",
        inventory_path.with_name(inventory_path.name + ".lock"),
    )
    monkeypatch.setattr("mws_parity.LOCAL_ROOT", state_root)
    monkeypatch.setattr("mws_parity.PARITY_STATE_DIR", state_root / "parity-state")
    monkeypatch.setattr("mws_parity.OVERLAY_ROOT", state_root / "python-overlay")
    save_inventory({"schema_version": 1, "machines": {"dev1": _machine()}})
    FakeRemoteTransport._shared_parity_locks.clear()
    yield state_root
    FakeRemoteTransport._shared_parity_locks.clear()


def _repo(tmp_path: Path, monkeypatch, files: dict[str, str]) -> Path:
    repo = init_repo(tmp_path / "repo", files=files)
    for name in ("motor", "vllm", "vllm_ascend"):
        monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, name, repo)
    return repo


def test_dirty_and_untracked_content_reaches_fixed_remote_tree(
    monkeypatch, tmp_path: Path, parity_env: Path
) -> None:
    repo = _repo(tmp_path, monkeypatch, {"base.py": "base\n"})
    (repo / "base.py").write_text("changed\n", encoding="utf-8")
    (repo / "new.py").write_text("new\n", encoding="utf-8")
    remote = tmp_path / "remote"

    manifest = sync_workspace_to_remote(
        _machine(), transport=FakeRemoteTransport(remote)
    )

    assert manifest["status"] == "ok"
    assert all(item["verified"] for item in manifest["remote_proof"])
    assert (remote / "mnt/motor-workspace/motor/new.py").read_text(encoding="utf-8") == "new\n"


def test_deleted_local_file_is_removed_remotely(
    monkeypatch, tmp_path: Path, parity_env: Path
) -> None:
    repo = _repo(tmp_path, monkeypatch, {"keep.py": "keep\n", "drop.py": "drop\n"})
    remote = tmp_path / "remote"
    transport = FakeRemoteTransport(remote)
    sync_workspace_to_remote(_machine(), transport=transport)
    (repo / "drop.py").unlink()

    sync_workspace_to_remote(_machine(), transport=transport, skip_fast_path=True)

    assert not (remote / "mnt/motor-workspace/motor/drop.py").exists()


def test_remote_drift_is_repaired(
    monkeypatch, tmp_path: Path, parity_env: Path
) -> None:
    _repo(tmp_path, monkeypatch, {"stable.py": "stable\n"})
    remote = tmp_path / "remote"
    transport = FakeRemoteTransport(remote)
    sync_workspace_to_remote(_machine(), transport=transport)
    drifted = remote / "mnt/motor-workspace/motor/stable.py"
    drifted.write_text("drift\n", encoding="utf-8")

    sync_workspace_to_remote(_machine(), transport=transport)

    assert drifted.read_text(encoding="utf-8") == "stable\n"


def test_partial_failure_does_not_publish_complete_state(
    monkeypatch, tmp_path: Path, parity_env: Path
) -> None:
    _repo(tmp_path, monkeypatch, {"file.py": "x\n"})

    class BrokenTransport(FakeRemoteTransport):
        def upload_file(self, local_path: str, remote_path: str) -> None:
            if remote_path.startswith("/tmp/mws-parity-vllm-"):
                raise RuntimeError("upload failed")
            super().upload_file(local_path, remote_path)

    with pytest.raises(RuntimeError, match="upload failed"):
        sync_workspace_to_remote(
            _machine(), transport=BrokenTransport(tmp_path / "remote")
        )
    assert load_parity_state("dev1") is None


def test_concurrent_sync_is_rejected(
    monkeypatch, tmp_path: Path, parity_env: Path
) -> None:
    _repo(tmp_path, monkeypatch, {"file.py": "x\n"})
    FakeRemoteTransport._shared_parity_locks.add(
        "/mnt/motor-workspace/.parity-sync.lock"
    )

    with pytest.raises(Exception, match="(?i)lock"):
        sync_workspace_to_remote(
            _machine(),
            transport=FakeRemoteTransport(tmp_path / "remote"),
            skip_fast_path=True,
        )
