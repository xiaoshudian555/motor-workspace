from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
LIB = SCAFFOLD / ".agents" / "lib"

sys.path.insert(0, str(LIB))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mws_local_state import WorkspaceStateError, upsert_machine  # noqa: E402
from mws_parity import load_machine_ready_evidence, sync_workspace_to_remote  # noqa: E402
from mws_run_state import run_record_path  # noqa: E402
from mws_state import atomic_write_json, load_json  # noqa: E402
from mws_transport import FakeRemoteTransport  # noqa: E402
from test_machine_management import (  # noqa: E402
    MockReadyTransport,
    _capture_script_main,
    _load_script_module,
    _machine,
)


def _fake_git_factory(repo: Path):
    def fake_git(args: list[str], path: Path):
        if args[:2] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, "deadbeef\n", "")
        if args[:2] == ["ls-files", "--error-unmatch"]:
            rel = args[2]
            tracked = not rel.startswith("untracked-")
            return subprocess.CompletedProcess(args, 0 if tracked else 1, "", "")
        if args[:1] == ["status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:1] == ["diff"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:1] == ["ls-files"]:
            names = sorted(
                p.name for p in path.iterdir() if p.is_file() and p.name != ".git"
            )
            payload = "\0".join(names) + ("\0" if names else "")
            return subprocess.CompletedProcess(args, 0, payload, "")
        return subprocess.CompletedProcess(args, 0, "", "")

    return fake_git


@pytest.fixture
def machine_chain_env(tmp_path: Path, monkeypatch):
    state_root = tmp_path / "state"
    state_root.mkdir()
    inventory_path = state_root / "machine-inventory.json"
    lock_path = inventory_path.with_name(inventory_path.name + ".lock")
    monkeypatch.setattr("mws_local_state.LOCAL_ROOT", state_root)
    monkeypatch.setattr("mws_local_state.INVENTORY_PATH", inventory_path)
    monkeypatch.setattr("mws_local_state.INVENTORY_LOCK_PATH", lock_path)
    monkeypatch.setattr("mws_parity.LOCAL_ROOT", state_root)
    monkeypatch.setattr("mws_parity.MACHINE_RUNS_DIR", state_root / "machine-runs")
    monkeypatch.setattr("mws_parity.PARITY_STATE_DIR", state_root / "parity-state")
    monkeypatch.setattr("mws_parity.OVERLAY_ROOT", state_root / "python-overlay")
    monkeypatch.setattr("mws_run_state.LOCAL_ROOT", state_root)
    FakeRemoteTransport._shared_parity_locks.clear()
    yield state_root
    FakeRemoteTransport._shared_parity_locks.clear()


def _setup_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "main.py").write_text("print('ok')\n", encoding="utf-8")
    return repo


def _bind_repos(monkeypatch, repo: Path) -> None:
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "motor", repo)
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "vllm", repo)
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "vllm_ascend", repo)


def _run_verify(monkeypatch, *, profile_context: str = "ctx-a") -> tuple[int, dict]:
    monkeypatch.setattr("mws_transport.transport_for_machine", lambda _: MockReadyTransport())
    monkeypatch.setattr(
        "mws_deploy.load_profile",
        lambda _path: {"kubernetes": {"context": profile_context}},
    )
    module = _load_script_module("machine_verify")
    return _capture_script_main(module, ["--alias", "dev1"])


def test_verify_produces_consumer_ready_run(machine_chain_env, monkeypatch) -> None:
    upsert_machine(_machine(host="1.2.3.4", kube_context="ctx-a"))
    exit_code, payload = _run_verify(monkeypatch, profile_context="ctx-a")
    assert exit_code == 0
    assert payload["schema_version"] == "mws.result.v1"
    assert payload["kind"] == "machine-ready"
    assert payload["status"] == "ready"
    machine_run_id = payload["run_id"]
    run_path = run_record_path("machine-ready", machine_run_id)
    assert run_path.exists()
    stored = load_json(run_path)
    assert stored["status"] == "ready"

    evidence = load_machine_ready_evidence("dev1", machine_run_id=machine_run_id)
    assert evidence["machine_run_id"] == machine_run_id
    assert evidence["endpoint"]["host"] == "1.2.3.4"


def test_verify_to_parity_longitudinal(
    machine_chain_env, tmp_path: Path, monkeypatch
) -> None:
    upsert_machine(_machine(host="dev1", kube_context="ctx-a"))
    exit_code, payload = _run_verify(monkeypatch, profile_context="ctx-a")
    assert exit_code == 0
    machine_run_id = payload["run_id"]

    repo = _setup_repo(tmp_path)
    monkeypatch.setattr("mws_parity._git", _fake_git_factory(repo))
    _bind_repos(monkeypatch, repo)

    evidence = load_machine_ready_evidence("dev1", machine_run_id=machine_run_id)
    manifest = sync_workspace_to_remote(
        _machine(host="dev1"),
        transport=FakeRemoteTransport(tmp_path / "remote"),
        machine_ready=evidence,
        skip_fast_path=True,
    )
    assert manifest["status"] == "ok"
    assert manifest["machine_ready"]["machine_run_id"] == machine_run_id


def test_verify_failure_run_not_consumable(machine_chain_env, monkeypatch) -> None:
    upsert_machine(_machine())

    class BrokenTransport(MockReadyTransport):
        def run(self, remote_command: str) -> subprocess.CompletedProcess[str]:
            if remote_command.strip() == "echo ok":
                return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="denied")
            return super().run(remote_command)

    monkeypatch.setattr("mws_transport.transport_for_machine", lambda _: BrokenTransport())
    module = _load_script_module("machine_verify")
    exit_code, payload = _capture_script_main(module, ["--alias", "dev1"])
    assert exit_code != 0
    assert payload["status"] == "failed"
    machine_run_id = payload["run_id"]
    run_path = run_record_path("machine-ready", machine_run_id)
    assert run_path.exists()
    stored = load_json(run_path)
    assert stored["status"] == "failed"
    assert stored["checks"]
    with pytest.raises(WorkspaceStateError, match="not ready"):
        load_machine_ready_evidence("dev1", machine_run_id=machine_run_id)


def test_verify_warning_continues_and_publishes_ready(machine_chain_env, monkeypatch) -> None:
    upsert_machine(_machine(kube_context="ctx-a"))
    exit_code, payload = _run_verify(monkeypatch, profile_context="")
    assert exit_code == 0
    assert payload["status"] == "ready"
    assert payload["warnings"]
    machine_run_id = payload["run_id"]
    evidence = load_machine_ready_evidence("dev1", machine_run_id=machine_run_id)
    assert any(item["status"] == "warning" for item in evidence["checks"])


def test_verify_unavailable_short_circuits(machine_chain_env, monkeypatch) -> None:
    upsert_machine(_machine())

    class UnavailableTransport(MockReadyTransport):
        def run(self, remote_command: str) -> subprocess.CompletedProcess[str]:
            if remote_command.strip() == "echo ok":
                raise OSError("host unreachable")
            return super().run(remote_command)

    monkeypatch.setattr("mws_transport.transport_for_machine", lambda _: UnavailableTransport())
    module = _load_script_module("machine_verify")
    exit_code, payload = _capture_script_main(module, ["--alias", "dev1"])
    assert exit_code != 0
    assert payload["status"] == "failed"
    assert payload["checks"][0]["status"] == "unavailable"
    assert payload["checks"][0]["name"] == "ssh"
    assert len(payload["checks"]) == 1


def test_immutable_run_rejected_on_second_write(machine_chain_env, monkeypatch) -> None:
    upsert_machine(_machine(kube_context="ctx-a"))
    exit_code, payload = _run_verify(monkeypatch, profile_context="ctx-a")
    assert exit_code == 0
    machine_run_id = payload["run_id"]
    module = _load_script_module("machine_verify")
    with patch.object(
        sys,
        "argv",
        [module.__file__, "--alias", "dev1", "--machine-run-id", machine_run_id],
    ):
        with pytest.raises(WorkspaceStateError, match="immutable"):
            module.main()


def test_consumer_requires_explicit_machine_run_id(machine_chain_env) -> None:
    upsert_machine(_machine())
    with pytest.raises(WorkspaceStateError, match="machine_run_id is required"):
        load_machine_ready_evidence("dev1")


@pytest.mark.parametrize(
    "mutator,match",
    [
        (lambda rec: rec.update({"schema_version": "legacy.v0"}), "schema_version"),
        (lambda rec: rec.update({"kind": "parity-complete"}), "kind mismatch"),
        (lambda rec: rec.update({"run_id": "other-id"}), "run_id mismatch"),
        (lambda rec: rec.update({"status": "failed"}), "not ready"),
        (lambda rec: rec.update({"alias": "other", "machine": "other"}), "for 'other'"),
        (lambda rec: rec["endpoint"].update({"host": "evil"}), "endpoint does not match"),
        (
            lambda rec: rec["checks"].append({"name": "extra", "status": "error", "message": "x"}),
            "invalid status",
        ),
    ],
)
def test_consumer_fail_closed_on_tampered_run(
    machine_chain_env, monkeypatch, mutator, match
) -> None:
    upsert_machine(_machine(kube_context="ctx-a"))
    exit_code, payload = _run_verify(monkeypatch, profile_context="ctx-a")
    assert exit_code == 0
    machine_run_id = payload["run_id"]
    run_path = run_record_path("machine-ready", machine_run_id)
    record = load_json(run_path)
    mutator(record)
    atomic_write_json(run_path, record)
    with pytest.raises(WorkspaceStateError, match=match):
        load_machine_ready_evidence("dev1", machine_run_id=machine_run_id)
