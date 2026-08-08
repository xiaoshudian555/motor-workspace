from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
REPO_ROOT = SCAFFOLD.parent
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from machine_ready_fixtures import build_machine_ready_run_payload  # noqa: E402
from mws_diagnosis import resolve_diagnosis_context  # noqa: E402
from mws_motor_logs import (  # noqa: E402
    collect_remote_log_sessions,
    correlate_new_log_sessions,
    recommend_pymotor_diagnosis_skills,
)
from mws_local_state import WorkspaceStateError  # noqa: E402
from mws_run_state import create_config_bundle, digest_json, write_run  # noqa: E402
from mws_state import atomic_write_json  # noqa: E402


def _machine():
    return {
        "alias": "dev1",
        "host": "1.2.3.4",
        "port": 22,
        "user": "root",
        "mount_root": "/mnt",
        "kube_context": "ctx-a",
        "remote_workspace_root": "/mnt/motor-workspace",
    }


@pytest.fixture()
def local_state_root(tmp_path, monkeypatch):
    monkeypatch.setattr("mws_local_state.LOCAL_ROOT", tmp_path, raising=False)
    monkeypatch.setattr("mws_local_state.ROOT", REPO_ROOT, raising=False)
    monkeypatch.setattr("mws_local_state.INVENTORY_PATH", tmp_path / "machine-inventory.json", raising=False)
    monkeypatch.setattr("mws_run_state.LOCAL_ROOT", tmp_path, raising=False)
    monkeypatch.setattr("mws_run_state.ROOT", REPO_ROOT, raising=False)
    monkeypatch.setattr("mws_run_state.CONFIG_BUNDLES_DIR", tmp_path / "config-bundles", raising=False)
    return tmp_path


def _write_bundle(local_root: Path) -> dict:
    bundle_dir = local_root / "bundle-root"
    manifests = bundle_dir / "manifests"
    manifests.mkdir(parents=True)
    manifest_copy = manifests / "demo.yaml"
    manifest_copy.write_text("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: demo\n", encoding="utf-8")
    fingerprint = digest_json({"job_id": "ns1"})
    meta = create_config_bundle(
        config_fingerprint=fingerprint,
        bundle_files={"manifests/demo.yaml": manifest_copy},
        metadata={
            "namespace": "ns1",
            "job_id": "ns1",
            "manifest_files": ["manifests/demo.yaml"],
            "workload_names": ["deployment/demo"],
        },
    )
    return meta


def _write_deploy_chain(
    local_root: Path,
    *,
    bundle_digest: str,
    bundle_dir: str,
    log_collection: dict | None = None,
) -> str:
    machine = _machine()
    atomic_write_json(
        local_root / "machine-inventory.json",
        {"schema_version": 1, "machines": {"dev1": machine}},
    )
    write_run(
        "machine-ready",
        "machine-1",
        build_machine_ready_run_payload(machine, run_id="machine-1"),
    )
    config_run_id = "cfg-1"
    write_run(
        "deploy-config-ready",
        config_run_id,
        {
            "schema_version": "mws.result.v1",
            "kind": "deploy-config-ready",
            "run_id": config_run_id,
            "status": "ready",
            "workflow_run_id": "wf-1",
            "namespace": "ns1",
            "bundle_dir": bundle_dir,
            "bundle_digest": bundle_digest,
        },
    )
    deploy_run_id = "deploy-1"
    write_run(
        "deploy-complete",
        deploy_run_id,
        {
            "schema_version": "mws.result.v1",
            "kind": "deploy-complete",
            "run_id": deploy_run_id,
            "status": "failed",
            "workflow_run_id": "wf-1",
            "machine": "dev1",
            "config_run_id": config_run_id,
            "namespace": "ns1",
            "bundle_dir": bundle_dir,
            "bundle_digest": bundle_digest,
            "log_collection": log_collection or {},
        },
    )
    return deploy_run_id


def test_diagnosis_resolves_deploy_config_bundle(local_state_root, tmp_path) -> None:
    bundle_meta = _write_bundle(local_state_root)
    deploy_run_id = _write_deploy_chain(
        local_state_root,
        bundle_digest=bundle_meta["bundle_digest"],
        bundle_dir=bundle_meta["bundle_dir"],
    )
    context = resolve_diagnosis_context(machine_alias="dev1", deploy_run_id=deploy_run_id)
    assert context["namespace"] == "ns1"
    assert context["config_run_id"] == "cfg-1"
    assert context["kube_context"] == "ctx-a"
    assert "plan_dir" not in context


def test_diagnosis_resolves_recorded_auto_log_collect_session(local_state_root) -> None:
    bundle_meta = _write_bundle(local_state_root)
    session = "/mnt/motor-workspace/motor/examples/deployer/log_collect/log/20260804_120000"
    deploy_run_id = _write_deploy_chain(
        local_state_root,
        bundle_digest=bundle_meta["bundle_digest"],
        bundle_dir=bundle_meta["bundle_dir"],
        log_collection={"status": "recorded", "session_dirs": [session]},
    )
    context = resolve_diagnosis_context(machine_alias="dev1", deploy_run_id=deploy_run_id)
    assert context["log_collection"]["session_dirs"] == [session]


def test_correlate_new_auto_log_collect_session() -> None:
    root = "/mnt/motor-workspace/motor/examples/deployer/log_collect/log"
    before = {"status": "ok", "remote_log_root": root, "session_dirs": [f"{root}/old"]}
    after = {
        "status": "ok",
        "remote_log_root": root,
        "session_dirs": [f"{root}/old", f"{root}/20260804_120000"],
    }
    result = correlate_new_log_sessions(before, after)
    assert result["status"] == "recorded"
    assert result["session_dirs"] == [f"{root}/20260804_120000"]


def test_collect_remote_logs_and_route_pymotor_skill(tmp_path) -> None:
    session = "/mnt/motor-workspace/motor/examples/deployer/log_collect/log/20260804_120000"
    content = b"PrecisionReporter: threshold reached\nprecision-auto-recover: failed for D\n"

    class FakeAdapter:
        def run(self, command):
            assert session in command
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="controller_node_0.log\n", stderr=""
            )

        def read_bytes(self, remote_path):
            assert remote_path == f"{session}/controller_node_0.log"
            return content

    collected = collect_remote_log_sessions(
        _machine(),
        [session],
        tmp_path / "logs",
        adapter=FakeAdapter(),
    )
    assert collected["status"] == "ok"
    routes = recommend_pymotor_diagnosis_skills(collected["files"])
    assert routes[0]["skill"] == "motor-diagnosis-controller-recovery-terminate"


def test_diagnosis_rejects_missing_config_run(local_state_root) -> None:
    write_run(
        "deploy-complete",
        "deploy-bad",
        {
            "kind": "deploy-complete",
            "run_id": "deploy-bad",
            "status": "failed",
            "machine": "dev1",
        },
    )
    atomic_write_json(
        local_state_root / "machine-inventory.json",
        {"schema_version": 1, "machines": {"dev1": _machine()}},
    )
    with pytest.raises(WorkspaceStateError, match="config_run_id"):
        resolve_diagnosis_context(machine_alias="dev1", deploy_run_id="deploy-bad")


def test_diagnosis_script_collects_artifacts(local_state_root, tmp_path, monkeypatch) -> None:
    bundle_meta = _write_bundle(local_state_root)
    deploy_run_id = _write_deploy_chain(
        local_state_root,
        bundle_digest=bundle_meta["bundle_digest"],
        bundle_dir=bundle_meta["bundle_dir"],
    )
    import importlib.util

    script = SCAFFOLD / ".agents/skills/motor-diagnosis/scripts/diagnosis_collect.py"
    spec = importlib.util.spec_from_file_location("diagnosis_collect_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    monkeypatch.setattr(
        sys,
        "argv",
        [str(script), "--machine", "dev1", "--deploy-run-id", deploy_run_id],
    )
    captured: dict = {}

    def fake_kubectl(*args):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}", stderr="")

    def _capture(payload):
        captured["payload"] = payload
        return 0

    with patch("mws_result.emit_result", side_effect=lambda payload: _capture(payload) or 0):
        spec.loader.exec_module(module)
        monkeypatch.setattr(module, "build_kubectl_runner", lambda *args, **kwargs: fake_kubectl)
        module.main()
    payload = captured["payload"]
    assert payload["kind"] == "deploy-diagnosis"
    assert payload["status"] == "ready"
    assert payload["config_run_id"] == "cfg-1"
