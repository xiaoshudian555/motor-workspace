from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from git_fixtures import init_repo  # noqa: E402
from machine_ready_fixtures import write_valid_machine_ready_run  # noqa: E402
from mws_local_state import upsert_machine  # noqa: E402
from mws_parity import load_machine_ready_evidence, prove_identity_parity  # noqa: E402


def _machine() -> dict:
    return {
        "alias": "dev-native",
        "host": "npu-host-01",
        "user": "root",
        "port": 22,
        "mount_root": "/mnt",
        "remote_workspace_root": "/mnt/motor-workspace",
        "executor": "native",
        "parity_backend": "shared-hostpath",
    }


def _setup_state(monkeypatch, state_root: Path) -> None:
    inventory_path = state_root / "machine-inventory.json"
    lock_path = inventory_path.with_name(inventory_path.name + ".lock")
    monkeypatch.setattr("mws_local_state.LOCAL_ROOT", state_root)
    monkeypatch.setattr("mws_local_state.INVENTORY_PATH", inventory_path)
    monkeypatch.setattr("mws_local_state.INVENTORY_LOCK_PATH", lock_path)
    monkeypatch.setattr("mws_parity.LOCAL_ROOT", state_root)
    monkeypatch.setattr("mws_parity.PARITY_STATE_DIR", state_root / "parity-state")
    monkeypatch.setattr("mws_parity.MACHINE_RUNS_DIR", state_root / "machine-runs")
    monkeypatch.setattr("mws_run_state.LOCAL_ROOT", state_root)
    upsert_machine(_machine())


def _machine_ready(monkeypatch, state_root: Path) -> dict:
    write_valid_machine_ready_run(_machine(), run_id="machine-native-1")
    return load_machine_ready_evidence("dev-native", machine_run_id="machine-native-1")


def _remote_native_fixture(tmp_path: Path, monkeypatch) -> dict[str, str]:
    """Build a remote-native layout using the real NativeTransport.

    Fixed source dirs live under tmp_path/workspace/{motor,vllm,vllm-ascend};
    REPO_DIRS point at those same dirs, and `build_fixed_source_paths` returns
    those same paths (identity: local working tree == fixed source paths).
    """
    import mws_parity

    workspace = tmp_path / "workspace"
    fixed: dict[str, str] = {}
    for name in ("motor", "vllm", "vllm_ascend"):
        repo_path = workspace / name
        init_repo(repo_path, files={f"{name}.py": f"{name}\n"})
        fixed[name] = str(repo_path)

    paths = {
        "mount_root": str(tmp_path / "mnt"),
        "remote_workspace_root": str(workspace),
        "motor_source": fixed["motor"],
        "vllm_source": fixed["vllm"],
        "vllm_ascend_source": fixed["vllm_ascend"],
        "python_overlay": str(workspace / "python-overlay"),
    }

    monkeypatch.setitem(mws_parity.REPO_DIRS, "motor", Path(fixed["motor"]))
    monkeypatch.setitem(mws_parity.REPO_DIRS, "vllm", Path(fixed["vllm"]))
    monkeypatch.setitem(mws_parity.REPO_DIRS, "vllm_ascend", Path(fixed["vllm_ascend"]))
    monkeypatch.setattr(
        "mws_parity.build_fixed_source_paths", lambda machine: dict(paths)
    )
    monkeypatch.setattr(
        "mws_parity.transport_for_machine",
        lambda machine, **kwargs: __import__("mws_transport").NativeTransport(machine),
    )
    return paths


def test_identity_parity_ready_when_local_repos_are_fixed_dirs(
    tmp_path: Path, monkeypatch
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    _setup_state(monkeypatch, state_root)

    paths = _remote_native_fixture(tmp_path, monkeypatch)

    ready = _machine_ready(monkeypatch, state_root)
    manifest = prove_identity_parity(_machine(), machine_ready=ready)

    assert manifest["status"] == "ready"
    assert manifest["source_mode"] == "identity"
    assert manifest["sync_mode"] == "identity"
    assert manifest["kind"] == "parity-complete"
    assert manifest["remote_content_digest"]
    assert set(manifest["content_digests"]) == {"motor", "vllm", "vllm_ascend"}
    assert manifest["remote_workspace_root"] == paths["remote_workspace_root"]
    assert manifest["source_dirs"]["motor"] == paths["motor_source"]


def test_identity_parity_fails_closed_when_local_repo_is_not_fixed_dir(
    tmp_path: Path, monkeypatch
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    _setup_state(monkeypatch, state_root)

    paths = _remote_native_fixture(tmp_path, monkeypatch)

    # local sources live in a different tree, not the fixed workspace
    import mws_parity

    stray_root = tmp_path / "elsewhere"
    for name in ("motor", "vllm", "vllm_ascend"):
        init_repo(stray_root / name, files={f"{name}.py": f"{name}\n"})
    monkeypatch.setitem(mws_parity.REPO_DIRS, "motor", stray_root / "motor")
    monkeypatch.setitem(mws_parity.REPO_DIRS, "vllm", stray_root / "vllm")
    monkeypatch.setitem(mws_parity.REPO_DIRS, "vllm_ascend", stray_root / "vllm-ascend")
    del paths

    ready = _machine_ready(monkeypatch, state_root)
    with pytest.raises(Exception) as exc:
        prove_identity_parity(_machine(), machine_ready=ready)
    assert "does not resolve to fixed source dir" in str(exc.value)


def test_identity_parity_fails_closed_on_missing_fixed_dir(
    tmp_path: Path, monkeypatch
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    _setup_state(monkeypatch, state_root)

    paths = _remote_native_fixture(tmp_path, monkeypatch)
    shutil.rmtree(paths["vllm_source"])

    ready = _machine_ready(monkeypatch, state_root)
    with pytest.raises(Exception) as exc:
        prove_identity_parity(_machine(), machine_ready=ready)
    assert "fixed source dir missing" in str(exc.value)
