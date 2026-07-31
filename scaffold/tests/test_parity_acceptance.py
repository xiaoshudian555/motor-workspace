from __future__ import annotations

import json
import subprocess
import sys
import tarfile
from io import BytesIO
from pathlib import Path

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
REPO_ROOT = SCAFFOLD.parent
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from machine_ready_fixtures import write_valid_machine_ready_run  # noqa: E402
from mws_local_state import upsert_machine  # noqa: E402
from mws_parity import (  # noqa: E402
    PARITY_STATE_DIR,
    build_source_manifest,
    load_machine_ready_evidence,
    load_parity_state,
    save_parity_state,
    sync_workspace_to_remote,
)
from mws_transport import FakeRemoteTransport  # noqa: E402


def _machine() -> dict:
    return {
        "alias": "dev1",
        "host": "dev1",
        "user": "root",
        "port": 22,
        "mount_root": "/mnt",
        "remote_workspace_root": "/mnt/motor-workspace",
        "kube_context": "ctx-a",
        "parity_backend": "shared-hostpath",
    }


def _machine_ready(alias: str = "dev1", run_id: str = "machine-test-1") -> dict:
    write_valid_machine_ready_run(_machine(), run_id=run_id)
    return load_machine_ready_evidence(alias, machine_run_id=run_id)


@pytest.fixture
def parity_env(tmp_path: Path, monkeypatch):
    state_root = tmp_path / "state"
    state_root.mkdir()
    inventory_path = state_root / "machine-inventory.json"
    lock_path = inventory_path.with_name(inventory_path.name + ".lock")
    monkeypatch.setattr("mws_local_state.LOCAL_ROOT", state_root)
    monkeypatch.setattr("mws_local_state.INVENTORY_PATH", inventory_path)
    monkeypatch.setattr("mws_local_state.INVENTORY_LOCK_PATH", lock_path)
    monkeypatch.setattr("mws_parity.LOCAL_ROOT", state_root)
    monkeypatch.setattr("mws_parity.PARITY_STATE_DIR", state_root / "parity-state")
    monkeypatch.setattr("mws_parity.MACHINE_RUNS_DIR", state_root / "machine-runs")
    monkeypatch.setattr("mws_parity.OVERLAY_ROOT", state_root / "python-overlay")
    monkeypatch.setattr("mws_run_state.LOCAL_ROOT", state_root)
    upsert_machine(_machine())
    FakeRemoteTransport._shared_parity_locks.clear()
    yield state_root
    FakeRemoteTransport._shared_parity_locks.clear()


def _fake_git_factory(repo: Path):
    def fake_git(args: list[str], path: Path):
        if args[:2] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, "deadbeef\n", "")
        if args[:2] == ["ls-files", "--error-unmatch"]:
            rel = args[2]
            tracked = not rel.startswith("untracked-")
            return subprocess.CompletedProcess(args, 0 if tracked else 1, "", "")
        if args[:1] == ["status"]:
            porcelain = ""
            for child in sorted(path.iterdir()):
                if child.name == ".git":
                    continue
                if child.is_file() and child.name.startswith("dirty-"):
                    porcelain += f" M {child.name}\n"
            return subprocess.CompletedProcess(args, 0, porcelain, "")
        if args[:1] == ["diff"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:1] == ["ls-files"]:
            names = sorted(
                p.name
                for p in path.iterdir()
                if p.is_file() and p.name != ".git"
            )
            payload = "\0".join(names) + ("\0" if names else "")
            return subprocess.CompletedProcess(args, 0, payload, "")
        return subprocess.CompletedProcess(args, 0, "", "")

    return fake_git


def _bind_repos(monkeypatch, repo: Path) -> None:
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "motor", repo)
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "vllm", repo)
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "vllm_ascend", repo)


def _setup_repo(tmp_path: Path, *, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    for name, content in files.items():
        (repo / name).write_text(content, encoding="utf-8")
    return repo


def _sync(
    monkeypatch,
    tmp_path: Path,
    parity_env: Path,
    *,
    repo_files: dict[str, str],
    machine_ready: dict,
    transport: FakeRemoteTransport | None = None,
    skip_fast_path: bool = False,
) -> dict:
    repo = _setup_repo(tmp_path, files=repo_files)
    monkeypatch.setattr("mws_parity._git", _fake_git_factory(repo))
    _bind_repos(monkeypatch, repo)
    fake_root = tmp_path / "remote"
    tx = transport or FakeRemoteTransport(fake_root)
    return sync_workspace_to_remote(
        _machine(),
        transport=tx,
        machine_ready=machine_ready,
        skip_fast_path=skip_fast_path,
    )


def test_unauthorized_overwrite_rejected() -> None:
    script = SCAFFOLD / ".agents/skills/remote-code-parity/scripts/parity_sync.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--machine", "dev1"],
        cwd=str(SCAFFOLD),
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    payload = json.loads(proc.stdout)
    assert payload["status"] == "failed"
    assert payload["schema_version"] == "mws.result.v1"
    assert "approved-overwrite" in payload["errors"][0]


def test_manifest_records_local_remote_digests(
    monkeypatch, tmp_path: Path, parity_env: Path
) -> None:
    ready = _machine_ready()
    manifest = _sync(
        monkeypatch,
        tmp_path,
        parity_env,
        repo_files={"clean.py": "print('clean')\n"},
        machine_ready=ready,
    )
    assert manifest["status"] == "ok"
    assert manifest["local_content_digest"]
    assert manifest["remote_content_digest"]
    assert manifest["source_dirs"]["motor"].endswith("/motor")
    assert len(manifest["repositories"]) == 3
    assert manifest["remote_proof"]
    assert all(item["verified"] for item in manifest["remote_proof"])


def test_dirty_and_untracked_files_sync(
    monkeypatch, tmp_path: Path, parity_env: Path
) -> None:
    ready = _machine_ready()
    manifest = _sync(
        monkeypatch,
        tmp_path,
        parity_env,
        repo_files={
            "dirty-tracked.py": "changed\n",
            "untracked-new.py": "new\n",
        },
        machine_ready=ready,
    )
    motor_repo = manifest["repositories"][0]
    assert motor_repo["dirty"] is True
    assert "untracked-new.py" in motor_repo["untracked_files"]
    assert manifest["sync_mode"] == "full-sync"


def test_delete_removes_remote_file(
    monkeypatch, tmp_path: Path, parity_env: Path
) -> None:
    ready = _machine_ready()
    repo = _setup_repo(tmp_path, files={"keep.py": "keep\n", "drop.py": "drop\n"})
    monkeypatch.setattr("mws_parity._git", _fake_git_factory(repo))
    _bind_repos(monkeypatch, repo)
    fake_root = tmp_path / "remote"
    tx = FakeRemoteTransport(fake_root)

    sync_workspace_to_remote(_machine(), transport=tx, machine_ready=ready)
    motor_dir = fake_root / "mnt/motor-workspace/motor"
    assert (motor_dir / "drop.py").exists()

    (repo / "drop.py").unlink()
    (repo / "new.py").write_text("new\n", encoding="utf-8")
    manifest = sync_workspace_to_remote(
        _machine(),
        transport=tx,
        machine_ready=ready,
        skip_fast_path=True,
    )
    assert (motor_dir / "keep.py").exists()
    assert (motor_dir / "new.py").exists()
    assert not (motor_dir / "drop.py").exists()
    assert manifest["status"] == "ok"


def test_no_change_fast_path_skips_resync(
    monkeypatch, tmp_path: Path, parity_env: Path
) -> None:
    ready = _machine_ready()
    repo = _setup_repo(tmp_path, files={"stable.py": "stable\n"})
    monkeypatch.setattr("mws_parity._git", _fake_git_factory(repo))
    _bind_repos(monkeypatch, repo)
    fake_root = tmp_path / "remote"
    tx = FakeRemoteTransport(fake_root)

    first = sync_workspace_to_remote(_machine(), transport=tx, machine_ready=ready)
    assert first["sync_mode"] == "full-sync"
    upload_count = len(tx.uploads)

    second = sync_workspace_to_remote(_machine(), transport=tx, machine_ready=ready)
    assert second["sync_mode"] == "no-change-fast-path"
    assert len(tx.uploads) == upload_count


def test_remote_drift_forces_resync(
    monkeypatch, tmp_path: Path, parity_env: Path
) -> None:
    ready = _machine_ready()
    repo = _setup_repo(tmp_path, files={"stable.py": "stable\n"})
    monkeypatch.setattr("mws_parity._git", _fake_git_factory(repo))
    _bind_repos(monkeypatch, repo)
    fake_root = tmp_path / "remote"
    tx = FakeRemoteTransport(fake_root)

    sync_workspace_to_remote(_machine(), transport=tx, machine_ready=ready)
    drifted = fake_root / "mnt/motor-workspace/motor/stable.py"
    drifted.write_text("drift\n", encoding="utf-8")

    manifest = sync_workspace_to_remote(_machine(), transport=tx, machine_ready=ready)
    assert manifest["sync_mode"] == "full-sync"
    assert drifted.read_text(encoding="utf-8") == "stable\n"


def test_partial_failure_does_not_publish_complete_state(
    monkeypatch, tmp_path: Path, parity_env: Path
) -> None:
    ready = _machine_ready()
    repo = _setup_repo(tmp_path, files={"file.py": "x\n"})
    monkeypatch.setattr("mws_parity._git", _fake_git_factory(repo))
    _bind_repos(monkeypatch, repo)
    fake_root = tmp_path / "remote"

    class FailSecondRepo(FakeRemoteTransport):
        def upload_file(self, local_path: str, remote_path: str) -> None:
            if remote_path.startswith("/tmp/mws-parity-vllm-"):
                raise RuntimeError("upload failed for vllm")
            super().upload_file(local_path, remote_path)

    tx = FailSecondRepo(fake_root)
    with pytest.raises(Exception):
        sync_workspace_to_remote(_machine(), transport=tx, machine_ready=ready)
    assert load_parity_state("dev1") is None


def test_concurrent_sync_second_fails_on_lock(
    monkeypatch, tmp_path: Path, parity_env: Path
) -> None:
    ready = _machine_ready()
    repo = _setup_repo(tmp_path, files={"file.py": "x\n"})
    monkeypatch.setattr("mws_parity._git", _fake_git_factory(repo))
    _bind_repos(monkeypatch, repo)

    lock_path = "/mnt/motor-workspace/.parity-sync.lock"
    FakeRemoteTransport._shared_parity_locks.add(lock_path)
    errors: list[str] = []
    try:
        sync_workspace_to_remote(
            _machine(),
            transport=FakeRemoteTransport(tmp_path / "remote"),
            machine_ready=ready,
            skip_fast_path=True,
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))
    assert errors
    assert any("lock" in msg.lower() for msg in errors)


def test_mid_failure_manifest_not_ok(
    monkeypatch, tmp_path: Path, parity_env: Path
) -> None:
    ready = _machine_ready()
    repo = _setup_repo(tmp_path, files={"file.py": "x\n"})
    monkeypatch.setattr("mws_parity._git", _fake_git_factory(repo))
    _bind_repos(monkeypatch, repo)

    class BrokenTransport(FakeRemoteTransport):
        def upload_file(self, local_path: str, remote_path: str) -> None:
            raise RuntimeError("upload failed")

    with pytest.raises(Exception):
        sync_workspace_to_remote(
            _machine(),
            transport=BrokenTransport(tmp_path / "remote"),
            machine_ready=ready,
        )


def test_machine_ready_missing(parity_env: Path) -> None:
    with pytest.raises(Exception, match="no successful machine-ready run found"):
        load_machine_ready_evidence("dev1")


def test_create_repo_tarball_includes_tracked_and_untracked(
    monkeypatch, tmp_path: Path
) -> None:
    from mws_parity import create_repo_tarball  # noqa: E402

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "tracked.py").write_text("t\n", encoding="utf-8")
    (repo / "untracked.py").write_text("u\n", encoding="utf-8")

    def fake_git(args: list[str], path: Path):
        if args[:1] == ["ls-files"]:
            return subprocess.CompletedProcess(args, 0, "tracked.py\0untracked.py\0", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("mws_parity._git", fake_git)
    data = create_repo_tarball(repo)
    with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as archive:
        names = set(archive.getnames())
    assert names == {"tracked.py", "untracked.py"}


def test_build_source_manifest_has_content_digest(monkeypatch, tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path, files={"a.py": "a\n"})
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "motor", repo)
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "vllm", repo)
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "vllm_ascend", repo)
    monkeypatch.setattr("mws_parity._git", _fake_git_factory(repo))
    manifest = build_source_manifest(_machine())
    assert manifest["local_content_digest"]
    assert manifest["local_content_digests"]["motor"]
    assert "snapshot_sha256" not in manifest
