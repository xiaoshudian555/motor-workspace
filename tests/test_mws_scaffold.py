from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_local_state import load_profile, save_profile  # noqa: E402
from mws_parity import build_source_manifest, repo_manifest  # noqa: E402
from mws_session_state import build_session_paths, pythonpath_for_session  # noqa: E402
from mws_validate import ValidationError, normalize_mount_root, require_safe_id  # noqa: E402


def test_normalize_mount_root_default() -> None:
    assert normalize_mount_root(None) == "/mnt"
    assert normalize_mount_root("/data/shared") == "/data/shared"


def test_require_safe_id_rejects_traversal() -> None:
    try:
        require_safe_id("../evil", label="id")
        raise AssertionError("expected ValidationError")
    except ValidationError:
        pass


def test_build_session_paths() -> None:
    paths = build_session_paths(
        workspace_id="mws-deadbeef",
        session_id="sess-test123",
        mount_root="/mnt",
    )
    assert paths["remote_session_root"] == "/mnt/motor-workspace/mws-deadbeef/sess-test123"
    assert paths["motor_source"].endswith("/motor")


def test_pythonpath_order() -> None:
    session = {
        "paths": build_session_paths(
            workspace_id="mws-a",
            session_id="sess-b",
            mount_root="/mnt",
        )
    }
    value = pythonpath_for_session(session)
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
    session = {
        "session_id": "sess-x",
        "paths": build_session_paths(
            workspace_id="mws-x",
            session_id="sess-x",
            mount_root="/mnt",
        ),
    }
    manifest = build_source_manifest(session)
    assert manifest["schema_version"] == 1
    assert "snapshot_sha256" in manifest
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
