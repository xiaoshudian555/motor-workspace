from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
REPO_ROOT = SCAFFOLD.parent
LIB = SCAFFOLD / ".agents" / "lib"
SKILL_SCRIPTS = SCAFFOLD / ".agents" / "skills" / "repo-init" / "scripts"

sys.path.insert(0, str(LIB))
sys.path.insert(0, str(SKILL_SCRIPTS))

from mws_run_state import run_record_path  # noqa: E402


@pytest.fixture
def repo_init_env(tmp_path, monkeypatch):
    state_root = tmp_path / "state"
    state_root.mkdir()
    monkeypatch.setattr("mws_local_state.LOCAL_ROOT", state_root)
    monkeypatch.setattr("mws_run_state.LOCAL_ROOT", state_root)
    return state_root


def test_probe_writes_workspace_ready_run(repo_init_env, monkeypatch) -> None:
    import repo_init_probe

    monkeypatch.setattr(
        repo_init_probe,
        "verify_lock",
        lambda **_: {"status": "ok", "errors": [], "warnings": []},
    )
    monkeypatch.setattr(sys, "argv", ["repo_init_probe.py", "--compact"])
    rc = repo_init_probe.main()
    assert rc in {0, 1}
    # stdout not captured; verify run file via side effect
    runs = list((repo_init_env / "workspace-runs").glob("*/run.json"))
    assert runs
    import json

    record = json.loads(runs[0].read_text(encoding="utf-8"))
    assert record["schema_version"] == "mws.result.v1"
    assert record["kind"] == "workspace-ready"
    assert record["run_id"]


def test_apply_init_submodules_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    child = tmp_path / "child"
    child.mkdir()
    (child / "child.txt").write_text("child\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=str(child), check=True, capture_output=True)
    subprocess.run(["git", "add", "child.txt"], cwd=str(child), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(child), check=True, capture_output=True)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "root.txt").write_text("root\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=str(workspace), check=True, capture_output=True)
    subprocess.run(["git", "add", "root.txt"], cwd=str(workspace), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(workspace), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "protocol.file.allow", "always"],
        cwd=str(workspace),
        check=True,
        capture_output=True,
    )
    bare_child = tmp_path / "child.git"
    subprocess.run(["git", "clone", "--bare", str(child), str(bare_child)], check=True, capture_output=True)
    add = subprocess.run(
        ["git", "-c", "protocol.file.allow=always", "submodule", "add", "../child.git", "sources/child"],
        cwd=str(workspace),
        capture_output=True,
        text=True,
    )
    assert add.returncode == 0, add.stderr

    import repo_init_apply

    def _git_run(args: list[str]):
        return subprocess.run(
            ["git", "-c", "safe.directory=*", "-c", "protocol.file.allow=always", *args],
            cwd=str(workspace),
            check=False,
            text=True,
            capture_output=True,
        )

    monkeypatch.setattr(repo_init_apply, "REPO_ROOT", workspace)
    monkeypatch.setattr(repo_init_apply, "_git_run", _git_run)
    first = repo_init_apply.init_submodules()
    second = repo_init_apply.init_submodules()
    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert (workspace / "sources" / "child" / "child.txt").exists()
