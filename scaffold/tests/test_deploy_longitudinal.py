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

from machine_ready_fixtures import write_valid_machine_ready_run  # noqa: E402

from mws_deploy import configure_deploy_bundle, compute_config_fingerprint, normalize_native_config  # noqa: E402
from mws_local_state import WorkspaceStateError  # noqa: E402
from mws_result import CheckRunner, aggregate_result_status  # noqa: E402
from mws_run_state import (  # noqa: E402
    create_config_bundle,
    digest_json,
    load_run,
    new_run_id,
    validate_upstream_refs,
    write_run,
)
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


@pytest.fixture()
def local_state_root(tmp_path, monkeypatch):
    monkeypatch.setattr("mws_local_state.LOCAL_ROOT", tmp_path, raising=False)
    monkeypatch.setattr("mws_local_state.ROOT", REPO_ROOT, raising=False)
    monkeypatch.setattr("mws_run_state.LOCAL_ROOT", tmp_path, raising=False)
    monkeypatch.setattr("mws_run_state.ROOT", REPO_ROOT, raising=False)
    monkeypatch.setattr("mws_run_state.CONFIG_BUNDLES_DIR", tmp_path / "config-bundles", raising=False)
    return tmp_path


def test_warning_continue_still_ready() -> None:
    runner = CheckRunner()
    runner.append({"name": "a", "status": "ok", "message": ""})
    runner.append({"name": "b", "status": "warning", "message": "soft"})
    assert runner.continue_ok
    assert aggregate_result_status(runner.checks) == "ready"


def test_upstream_missing_fail_closed(local_state_root) -> None:
    with pytest.raises(WorkspaceStateError, match="missing run record"):
        load_run("deploy-config-ready", "missing-config")


def test_environment_run_wrong_workflow(local_state_root) -> None:
    env_id = new_run_id("env")
    write_run(
        "deploy-environment-ready",
        env_id,
        {"status": "ready", "workflow_run_id": "wf-a", "kind": "deploy-environment-ready"},
    )
    record = load_run("deploy-environment-ready", env_id)
    assert record["workflow_run_id"] == "wf-a"
    with pytest.raises(WorkspaceStateError, match="another workflow"):
        validate_upstream_refs(
            [{"kind": "deploy-environment-ready", "run_id": env_id}],
            workflow_run_id="wf-b",
        )


def test_config_reuse_same_fingerprint(tmp_path, local_state_root) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "user_config.json").write_text(
        json.dumps({"motor_deploy_config": {"job_id": "ns1", "image_name": "img:tag"}}),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("apiVersion: v1\nkind: ConfigMap\n", encoding="utf-8")
    fingerprint = digest_json({"job_id": "ns1"})
    bundle_root = local_state_root / "config-bundles" / fingerprint
    manifests = bundle_root / "manifests"
    manifests.mkdir(parents=True)
    manifest_copy = manifests / "demo.yaml"
    manifest_copy.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    create_config_bundle(
        config_fingerprint=fingerprint,
        bundle_files={"manifests/demo.yaml": manifest_copy},
        metadata={
            "namespace": "ns1",
            "manifest_files": ["manifests/demo.yaml"],
            "machine_paths": _machine_paths(),
        },
    )
    result = configure_deploy_bundle(
        machine=_machine(),
        config_dir=config_dir,
        run_dir=tmp_path / "run",
        kube_context="ctx-a",
        base_image_ref="img:tag",
        parity_path_refs={},
        reuse_bundle_dir=bundle_root,
    )
    assert result["ready"] is True
    assert result["reused"] is True


def test_config_reuse_rejects_path_change(tmp_path, local_state_root) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "user_config.json").write_text(
        json.dumps({"motor_deploy_config": {"job_id": "ns1"}}),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("apiVersion: v1\nkind: ConfigMap\n", encoding="utf-8")
    fingerprint = digest_json({"job_id": "ns1"})
    bundle_root = local_state_root / "config-bundles" / fingerprint
    manifests = bundle_root / "manifests"
    manifests.mkdir(parents=True)
    manifest_copy = manifests / "demo.yaml"
    manifest_copy.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    stored_paths = _machine_paths()
    create_config_bundle(
        config_fingerprint=fingerprint,
        bundle_files={"manifests/demo.yaml": manifest_copy},
        metadata={"namespace": "ns1", "machine_paths": stored_paths},
    )
    changed_machine = _machine()
    changed_machine["remote_workspace_root"] = "/mnt/other-workspace"
    with pytest.raises(WorkspaceStateError, match="path mapping"):
        configure_deploy_bundle(
            machine=changed_machine,
            config_dir=config_dir,
            run_dir=tmp_path / "run2",
            kube_context="ctx-a",
            base_image_ref="img:tag",
            parity_path_refs={},
            reuse_bundle_dir=bundle_root,
        )


def test_bundle_tamper_rejected(local_state_root, tmp_path) -> None:
    src = tmp_path / "manifest.yaml"
    src.write_text("apiVersion: v1\nkind: ConfigMap\n", encoding="utf-8")
    fingerprint = digest_json({"env": 1})
    first = create_config_bundle(
        config_fingerprint=fingerprint,
        bundle_files={"manifest.yaml": src},
        metadata={"injector_version": "v1"},
    )
    bundle_root = local_state_root / "config-bundles" / fingerprint
    tampered = bundle_root / "manifest.yaml"
    tampered.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(WorkspaceStateError, match="modified"):
        create_config_bundle(
            config_fingerprint=fingerprint,
            bundle_files={"manifest.yaml": src},
            metadata={"injector_version": "v1"},
        )


def test_fingerprint_excludes_code_digest() -> None:
    config_dir = Path("/tmp/unused")
    native = {"user_config.json": {"motor_deploy_config": {"job_id": "ns1"}}, "env.json": {}}
    fp = compute_config_fingerprint(
        native_config=native,
        machine_paths=_machine_paths(),
        deployer_version="v1",
    )
    fp_same = compute_config_fingerprint(
        native_config=native,
        machine_paths=_machine_paths(),
        deployer_version="v1",
    )
    assert fp == fp_same
    assert "parity" not in fp


def test_deploy_configure_reuse_cli(tmp_path, local_state_root, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "user_config.json").write_text(
        json.dumps({"motor_deploy_config": {"job_id": "ns1", "image_name": "img:tag"}}),
        encoding="utf-8",
    )
    env_id = new_run_id("env")
    parity_id = new_run_id("parity")
    write_run(
        "deploy-environment-ready",
        env_id,
        {"status": "ready", "workflow_run_id": "wf-1", "kube_context": "ctx-a"},
    )
    write_run(
        "parity-complete",
        parity_id,
        {"status": "ready", "workflow_run_id": "wf-1", "machine": "dev1", "alias": "dev1"},
    )
    inv = {"schema_version": 1, "machines": {"dev1": _machine()}}
    atomic_write_json(local_state_root / "machine-inventory.json", inv)
    write_valid_machine_ready_run(_machine(), run_id="machine-1", workflow_run_id="wf-1")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("apiVersion: v1\nkind: ConfigMap\n", encoding="utf-8")
    native = normalize_native_config(config_dir)
    from mws_deploy import deployer_version_token  # noqa: E402

    fingerprint = compute_config_fingerprint(
        native_config=native,
        machine_paths=_machine_paths(),
        deployer_version=deployer_version_token(),
    )
    bundle_root = local_state_root / "config-bundles" / fingerprint
    manifests = bundle_root / "manifests"
    manifests.mkdir(parents=True)
    manifest_copy = manifests / "demo.yaml"
    manifest_copy.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    create_config_bundle(
        config_fingerprint=fingerprint,
        bundle_files={"manifests/demo.yaml": manifest_copy},
        metadata={"namespace": "ns1", "machine_paths": _machine_paths()},
    )

    inv = {"schema_version": 1, "machines": {"dev1": _machine()}}
    atomic_write_json(local_state_root / "machine-inventory.json", inv)
    monkeypatch.setattr("mws_local_state.LOCAL_ROOT", local_state_root, raising=False)
    monkeypatch.setattr("mws_local_state.INVENTORY_PATH", local_state_root / "machine-inventory.json", raising=False)
    monkeypatch.setattr("mws_run_state.LOCAL_ROOT", local_state_root, raising=False)
    monkeypatch.setattr("mws_parity.MACHINE_RUNS_DIR", local_state_root / "machine-runs", raising=False)
    monkeypatch.setattr("mws_lock.load_lock", lambda: {}, raising=False)
    monkeypatch.setattr("mws_lock.verify_lock", lambda **kwargs: {}, raising=False)

    import importlib.util

    script = SCAFFOLD / ".agents/skills/motor-deploy-configure/scripts/deploy_configure.py"
    spec = importlib.util.spec_from_file_location("deploy_configure_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            "--machine",
            "dev1",
            "--environment-run-id",
            env_id,
            "--parity-run-id",
            parity_id,
            "--config-dir",
            str(config_dir),
            "--workflow-run-id",
            "wf-1",
            "--machine-run-id",
            "machine-1",
            "--reuse",
        ],
    )
    captured: dict = {}
    with patch("mws_result.emit", side_effect=lambda payload: captured.update(payload=payload) or 0):
        spec.loader.exec_module(module)
        module.main()
    payload = captured["payload"]
    assert payload["status"] == "ready", payload
    assert payload.get("reused") is True


def test_upstream_dry_run_failure_stops(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "user_config.json").write_text(
        json.dumps({"motor_deploy_config": {"job_id": "ns1"}}),
        encoding="utf-8",
    )
    with patch(
        "mws_deploy.verify_namespace_exists",
        return_value={"name": "namespace_exists", "status": "ok", "message": "ok"},
    ):
        with patch(
            "mws_deploy.run_deploy_dry_run",
            return_value={"status": "error", "stderr_tail": "boom"},
        ):
            result = configure_deploy_bundle(
                machine=_machine(),
                config_dir=config_dir,
                run_dir=tmp_path / "run",
                kube_context="ctx-a",
                base_image_ref="img:tag",
                parity_path_refs={},
            )
    assert result["ready"] is False
    assert result["stopped_at"] == "upstream_dry_run"
