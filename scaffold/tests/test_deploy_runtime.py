from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
REPO_ROOT = SCAFFOLD.parent
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from machine_ready_fixtures import write_valid_machine_ready_run  # noqa: E402

from mws_deploy import (  # noqa: E402
    apply_config_bundle,
    collect_runtime_code_paths,
    pod_readiness_from_context,
    verify_min_service_access,
    verify_runtime_code_paths,
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


def _machine_paths():
    root = "/mnt/motor-workspace"
    return {
        "mount_root": "/mnt",
        "remote_workspace_root": root,
        "motor_source": f"{root}/motor",
        "vllm_source": f"{root}/vllm",
        "vllm_ascend_source": f"{root}/vllm-ascend",
        "python_overlay": f"{root}/python-overlay",
    }


def _write_bundle(local_root: Path, *, machine_paths: dict[str, str] | None = None) -> dict:
    manifest = local_root / "manifest.yaml"
    manifest.write_text("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: demo\n", encoding="utf-8")
    bundle_dir = local_root / "bundle-root"
    manifests = bundle_dir / "manifests"
    manifests.mkdir(parents=True)
    manifest_copy = manifests / "demo.yaml"
    manifest_copy.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    fingerprint = digest_json({"job_id": "ns1"})
    meta = create_config_bundle(
        config_fingerprint=fingerprint,
        bundle_files={"manifests/demo.yaml": manifest_copy},
        metadata={
            "namespace": "ns1",
            "job_id": "ns1",
            "manifest_files": ["manifests/demo.yaml"],
            "workload_names": ["deployment/demo"],
            "machine_paths": machine_paths or _machine_paths(),
        },
    )
    return meta


def _write_config_run(local_root: Path, bundle_meta: dict, *, machine_paths: dict | None = None) -> str:
    run_id = "cfg-test-1"
    record = {
        "kind": "deploy-config-ready",
        "run_id": run_id,
        "status": "ready",
        "workflow_run_id": "wf-1",
        "namespace": "ns1",
        "bundle_dir": bundle_meta["bundle_dir"],
        "bundle_digest": bundle_meta["bundle_digest"],
        "config_fingerprint": bundle_meta["config_fingerprint"],
        "machine_paths": machine_paths or _machine_paths(),
    }
    path = local_root / "config-runs" / run_id / "run.json"
    atomic_write_json(path, record)
    return run_id


@pytest.fixture()
def local_state_root(tmp_path, monkeypatch):
    monkeypatch.setattr("mws_local_state.LOCAL_ROOT", tmp_path, raising=False)
    monkeypatch.setattr("mws_local_state.ROOT", REPO_ROOT, raising=False)
    monkeypatch.setattr("mws_run_state.LOCAL_ROOT", tmp_path, raising=False)
    monkeypatch.setattr("mws_run_state.ROOT", REPO_ROOT, raising=False)
    monkeypatch.setattr("mws_run_state.CONFIG_BUNDLES_DIR", tmp_path / "config-bundles", raising=False)
    return tmp_path


def test_apply_bytes_match_bundle(local_state_root) -> None:
    bundle_meta = _write_bundle(local_state_root)
    bundle_dir = Path(bundle_meta["bundle_dir"])
    if not bundle_dir.is_absolute():
        bundle_dir = REPO_ROOT / bundle_dir
    manifest = bundle_dir / "manifests" / "demo.yaml"
    expected_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()

    def fake_kubectl(*args):
        assert args == ("apply", "-f", "/tmp/mws-test/demo.yaml", "-n", "ns1")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="configured", stderr="")

    @contextmanager
    def fake_stage(*args, **kwargs):
        yield {manifest: "/tmp/mws-test/demo.yaml"}

    with patch("mws_deploy.stage_remote_files", side_effect=fake_stage):
        result = apply_config_bundle(
            bundle_dir=bundle_dir,
            machine=_machine(),
            kube_context="ctx-a",
            namespace="ns1",
            kubectl=fake_kubectl,
        )
    assert result["status"] == "ok"
    assert result["apply_results"][0]["bytes_sha256"] == expected_hash


def test_apply_does_not_call_render_or_dry_run(local_state_root) -> None:
    bundle_meta = _write_bundle(local_state_root)
    bundle_dir = Path(bundle_meta["bundle_dir"])
    if not bundle_dir.is_absolute():
        bundle_dir = REPO_ROOT / bundle_dir

    manifest = bundle_dir / "manifests" / "demo.yaml"

    @contextmanager
    def fake_stage(*args, **kwargs):
        yield {manifest: "/tmp/mws-test/demo.yaml"}

    fake_kubectl = lambda *args: subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
    with patch("mws_deploy.stage_remote_files", side_effect=fake_stage):
        with patch("mws_deploy.run_deploy_dry_run") as dry_run:
            with patch("mws_deploy.configure_deploy_bundle") as configure:
                apply_config_bundle(
                    bundle_dir=bundle_dir,
                    machine=_machine(),
                    kube_context="ctx-a",
                    namespace="ns1",
                    kubectl=fake_kubectl,
                )
    dry_run.assert_not_called()
    configure.assert_not_called()


MACHINE_RUN_ID = "machine-1"


def _write_machine_ready() -> None:
    write_valid_machine_ready_run(_machine(), run_id=MACHINE_RUN_ID)


def _write_inventory(local_root: Path, machine: dict | None = None) -> None:
    record = machine if machine is not None else _machine()
    inv = {
        "schema_version": 1,
        "machines": {record["alias"]: record},
    }
    atomic_write_json(local_root / "machine-inventory.json", inv)


def test_apply_script_bundle_path_mismatch_fails(local_state_root, tmp_path, monkeypatch) -> None:
    other_paths = dict(_machine_paths())
    other_paths["motor_source"] = "/mnt/other/motor"
    bundle_meta = _write_bundle(local_state_root, machine_paths=other_paths)
    config_run_id = _write_config_run(local_state_root, bundle_meta)
    _write_inventory(local_state_root)
    _write_machine_ready()
    monkeypatch.setattr("mws_local_state.LOCAL_ROOT", local_state_root, raising=False)
    monkeypatch.setattr("mws_local_state.INVENTORY_PATH", local_state_root / "machine-inventory.json", raising=False)
    monkeypatch.setattr("mws_run_state.LOCAL_ROOT", local_state_root, raising=False)
    monkeypatch.setattr("mws_parity.MACHINE_RUNS_DIR", local_state_root / "machine-runs", raising=False)

    import importlib.util

    script = SCAFFOLD / ".agents/skills/motor-k8s-deploy/scripts/deploy_apply.py"
    spec = importlib.util.spec_from_file_location("deploy_apply_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            "--machine",
            "dev1",
            "--config-run-id",
            config_run_id,
            "--machine-run-id",
            MACHINE_RUN_ID,
            "--approved-by-user",
        ],
    )
    with patch("mws_result.emit", side_effect=lambda payload: payload):
        spec.loader.exec_module(module)
        payload = module.main()
    assert payload["status"] == "failed"
    assert "path mapping" in payload["errors"][0]


def _run_apply_main(local_state_root, monkeypatch, config_run_id: str, **patches):
    _write_inventory(local_state_root)
    _write_machine_ready()
    monkeypatch.setattr("mws_local_state.LOCAL_ROOT", local_state_root, raising=False)
    monkeypatch.setattr("mws_local_state.INVENTORY_PATH", local_state_root / "machine-inventory.json", raising=False)
    monkeypatch.setattr("mws_run_state.LOCAL_ROOT", local_state_root, raising=False)
    monkeypatch.setattr("mws_parity.MACHINE_RUNS_DIR", local_state_root / "machine-runs", raising=False)
    import importlib.util

    script = SCAFFOLD / ".agents/skills/motor-k8s-deploy/scripts/deploy_apply.py"
    spec = importlib.util.spec_from_file_location("deploy_apply_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            "--machine",
            "dev1",
            "--config-run-id",
            config_run_id,
            "--machine-run-id",
            MACHINE_RUN_ID,
            "--approved-by-user",
        ],
    )
    captured: dict = {}

    def _capture(payload):
        captured["payload"] = payload
        return 0 if payload.get("status") == "ready" else 1

    from contextlib import ExitStack

    with ExitStack() as stack:
        stack.enter_context(patch("mws_result.emit", side_effect=_capture))
        for target, value in patches.items():
            stack.enter_context(patch(target, value))
        spec.loader.exec_module(module)
        module.main()
    return captured["payload"]


def test_ready_failure_blocks_deploy_complete(local_state_root, tmp_path, monkeypatch) -> None:
    bundle_meta = _write_bundle(local_state_root)
    config_run_id = _write_config_run(local_state_root, bundle_meta)
    payload = _run_apply_main(
        local_state_root,
        monkeypatch,
        config_run_id,
        **{
            "mws_deploy.apply_config_bundle": lambda **kwargs: {"status": "ok", "apply_results": []},
            "mws_deploy.pod_readiness_from_context": lambda machine, ctx, ns: {
                "ready": False,
                "pods_total": 1,
                "pods_ready": 0,
            },
        },
    )
    assert payload["status"] == "failed"
    assert any(item["name"] == "pod_readiness" for item in payload["checks"])


def test_min_access_failure_blocks_deploy_complete(local_state_root, tmp_path, monkeypatch) -> None:
    bundle_meta = _write_bundle(local_state_root)
    config_run_id = _write_config_run(local_state_root, bundle_meta)
    payload = _run_apply_main(
        local_state_root,
        monkeypatch,
        config_run_id,
        **{
            "mws_deploy.apply_config_bundle": lambda **kwargs: {"status": "ok", "apply_results": []},
            "mws_deploy.pod_readiness_from_context": lambda machine, ctx, ns: {
                "ready": True,
                "pods_total": 1,
                "pods_ready": 1,
            },
            "mws_deploy.verify_min_service_access": lambda **kwargs: {
                "name": "min_service_access",
                "status": "error",
                "message": "no endpoints",
            },
        },
    )
    assert payload["status"] == "failed"
    assert any(item["name"] == "min_service_access" for item in payload["checks"])


def test_runtime_code_path_mismatch_fails() -> None:
    collected = {
        "status": "ok",
        "paths": {
            "motor": "/opt/motor/__init__.py",
            "vllm": "/mnt/motor-workspace/vllm/vllm/__init__.py",
            "vllm_ascend": "/mnt/motor-workspace/vllm-ascend/vllm_ascend/__init__.py",
        },
    }
    result = verify_runtime_code_paths(collected, _machine_paths())
    assert result["status"] == "error"
    assert "motor" in result["message"]


def test_runtime_code_path_match_ok() -> None:
    paths = _machine_paths()
    collected = {
        "status": "ok",
        "paths": {
            "motor": f"{paths['motor_source']}/motor/__init__.py",
            "vllm": f"{paths['vllm_source']}/vllm/__init__.py",
            "vllm_ascend": f"{paths['vllm_ascend_source']}/vllm_ascend/__init__.py",
        },
    }
    result = verify_runtime_code_paths(collected, paths)
    assert result["status"] == "ok"


def test_restart_recollects_code_paths(local_state_root, monkeypatch) -> None:
    bundle_meta = _write_bundle(local_state_root)
    deploy_run = {
        "kind": "deploy-complete",
        "run_id": "deploy-1",
        "status": "ready",
        "machine": "dev1",
        "namespace": "ns1",
        "bundle_dir": bundle_meta["bundle_dir"],
    }
    run_path = local_state_root / "deploy-runs" / "deploy-1" / "run.json"
    atomic_write_json(run_path, deploy_run)
    _write_inventory(local_state_root)
    monkeypatch.setattr("mws_run_state.LOCAL_ROOT", local_state_root, raising=False)
    monkeypatch.setattr("mws_local_state.LOCAL_ROOT", local_state_root, raising=False)
    monkeypatch.setattr("mws_local_state.INVENTORY_PATH", local_state_root / "machine-inventory.json", raising=False)
    monkeypatch.setattr("mws_parity.MACHINE_RUNS_DIR", local_state_root / "machine-runs", raising=False)

    pods_calls: list[str] = []
    code_calls: list[str] = []

    def fake_pods(machine, ctx, ns):
        pods_calls.append(ns)
        return {"ready": True, "pods_total": 1, "pods_ready": 1}

    def fake_collect(**kwargs):
        code_calls.append(kwargs["namespace"])
        return {
            "status": "ok",
            "paths": {
                "motor": "/mnt/motor-workspace/motor/motor/__init__.py",
                "vllm": "/mnt/motor-workspace/vllm/vllm/__init__.py",
                "vllm_ascend": "/mnt/motor-workspace/vllm-ascend/vllm_ascend/__init__.py",
            },
        }

    import importlib.util

    script = SCAFFOLD / ".agents/skills/motor-k8s-deploy/scripts/deploy_restart.py"
    spec = importlib.util.spec_from_file_location("deploy_restart_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            "--machine",
            "dev1",
            "--deploy-run-id",
            "deploy-1",
            "--skip-parity",
            "--approved-by-user",
        ],
    )
    captured: dict = {}

    with patch("mws_deploy.restart_deploy_workloads_from_context", return_value={"status": "ok", "actions": []}):
        with patch("mws_deploy.pod_readiness_from_context", side_effect=fake_pods):
            with patch("mws_deploy.collect_runtime_code_paths", side_effect=fake_collect):
                with patch("mws_result.emit_result", side_effect=lambda payload: captured.update(payload=payload) or 0):
                    spec.loader.exec_module(module)
                    module.main()
    payload = captured["payload"]
    assert payload["status"] == "ready"
    assert pods_calls == ["ns1"]
    assert code_calls == ["ns1"]
    assert payload["code_paths"]["status"] == "ok"


def test_stop_requires_bundle_dir(local_state_root, monkeypatch) -> None:
    run_path = local_state_root / "deploy-runs" / "deploy-2" / "run.json"
    atomic_write_json(
        run_path,
        {"kind": "deploy-complete", "run_id": "deploy-2", "status": "ready", "machine": "dev1"},
    )
    _write_inventory(local_state_root)
    monkeypatch.setattr("mws_run_state.LOCAL_ROOT", local_state_root, raising=False)
    monkeypatch.setattr("mws_local_state.LOCAL_ROOT", local_state_root, raising=False)
    monkeypatch.setattr("mws_local_state.INVENTORY_PATH", local_state_root / "machine-inventory.json", raising=False)

    import importlib.util

    script = SCAFFOLD / ".agents/skills/motor-k8s-deploy/scripts/deploy_stop.py"
    spec = importlib.util.spec_from_file_location("deploy_stop_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            "--machine",
            "dev1",
            "--deploy-run-id",
            "deploy-2",
            "--approved-by-user",
        ],
    )
    captured: dict = {}
    with patch("mws_result.emit", side_effect=lambda payload: captured.update(payload=payload) or 1):
        spec.loader.exec_module(module)
        module.main()
    assert "bundle_dir missing" in captured["payload"]["errors"][0]


def test_collect_runtime_code_paths_no_pod() -> None:
    fake_kubectl = lambda *args: subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
    with patch("mws_deploy._pick_runtime_pod", return_value=None):
        result = collect_runtime_code_paths(
            machine=_machine(),
            kube_context="ctx-a",
            namespace="ns1",
            kubectl=fake_kubectl,
        )
    assert result["status"] == "error"


def test_verify_min_service_access_no_endpoints() -> None:
    payload = json.dumps({"items": [{"metadata": {"name": "svc"}, "subsets": []}]})

    def fake_kubectl(*args):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=payload, stderr="")

    result = verify_min_service_access(
        machine=_machine(),
        kube_context="ctx-a",
        namespace="ns1",
        kubectl=fake_kubectl,
    )
    assert result["status"] == "error"
