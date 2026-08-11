from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_local_state import WorkspaceStateError  # noqa: E402
from mws_run_state import RUN_KINDS, new_run_id, write_run  # noqa: E402


@pytest.fixture()
def local_state_root(tmp_path, monkeypatch):
    monkeypatch.setattr("mws_run_state.LOCAL_ROOT", tmp_path)
    return tmp_path


def test_only_retained_executable_workflows_have_run_kinds() -> None:
    assert RUN_KINDS == {"parity-complete", "motor-wheel-build"}


def test_write_run_is_immutable(local_state_root) -> None:
    run_id = new_run_id("parity")
    write_run("parity-complete", run_id, {"status": "ready"})
    with pytest.raises(WorkspaceStateError, match="immutable"):
        write_run("parity-complete", run_id, {"status": "ready"})
