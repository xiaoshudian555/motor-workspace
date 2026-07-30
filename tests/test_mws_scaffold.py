from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_local_state import load_profile, save_profile  # noqa: E402
from mws_machine_target import build_fixed_source_paths, pythonpath_for_machine  # noqa: E402
from mws_parity import build_source_manifest, repo_manifest  # noqa: E402
from mws_validate import ValidationError, normalize_mount_root, require_safe_id  # noqa: E402


def _machine() -> dict:
    return {
        "alias": "dev1",
        "host": "dev1.example",
        "port": 22,
        "user": "root",
        "mount_root": "/mnt",
    }


def test_normalize_mount_root_default() -> None:
    assert normalize_mount_root(None) == "/mnt"
    assert normalize_mount_root("/data/shared") == "/data/shared"


def test_require_safe_id_rejects_traversal() -> None:
    try:
        require_safe_id("../evil", label="id")
        raise AssertionError("expected ValidationError")
    except ValidationError:
        pass


def test_build_fixed_source_paths() -> None:
    paths = build_fixed_source_paths(_machine())
    root = "/mnt/motor-workspace"
    assert paths["remote_workspace_root"] == root
    assert paths["motor_source"] == f"{root}/motor"
    assert "current" not in paths


def test_pythonpath_order() -> None:
    value = pythonpath_for_machine(_machine())
    parts = value.split(":")
    assert parts[0].endswith("/motor")
    assert parts[1].endswith("/vllm")
    assert parts[2].endswith("/vllm-ascend")


def test_repo_manifest_on_motor_submodule() -> None:
    motor = ROOT / "motor"
    if not motor.exists():
        return
    manifest = repo_manifest("motor", motor)
    assert manifest["name"] == "motor"
    assert "commit" in manifest


def test_build_source_manifest_structure() -> None:
    manifest = build_source_manifest(_machine())
    assert manifest["schema_version"] == 2
    assert "runtime_snapshot_id" not in manifest
    assert len(manifest["repositories"]) == 3


def test_profile_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "mws_local_state.STATE_DIR",
        tmp_path,
        raising=False,
    )
    monkeypatch.setattr(
        "mws_local_state.PROFILE_PATH",
        tmp_path / "machine-profile.json",
        raising=False,
    )
    save_profile({"workspace_id": "mws-test"})
    data = load_profile()
    assert data["workspace_id"] == "mws-test"
