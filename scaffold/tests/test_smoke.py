from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
REPO_ROOT = SCAFFOLD.parent
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_local_state import WorkspaceStateError  # noqa: E402
from mws_run_state import create_config_bundle, digest_json, write_run  # noqa: E402
from mws_smoke import (  # noqa: E402
    discover_coordinator_services,
    resolve_model_name,
    resolve_smoke_context,
    validate_non_stream_response,
    validate_stream_response,
    wait_for_readiness,
)
from mws_state import atomic_write_json  # noqa: E402


def _machine() -> dict:
    return {
        "alias": "dev1",
        "host": "1.2.3.4",
        "port": 22,
        "user": "root",
        "kube_context": "ctx-a",
    }


@pytest.fixture()
def local_state_root(tmp_path, monkeypatch):
    monkeypatch.setattr("mws_local_state.LOCAL_ROOT", tmp_path, raising=False)
    monkeypatch.setattr("mws_local_state.ROOT", REPO_ROOT, raising=False)
    monkeypatch.setattr("mws_local_state.INVENTORY_PATH", tmp_path / "machine-inventory.json", raising=False)
    monkeypatch.setattr("mws_run_state.LOCAL_ROOT", tmp_path, raising=False)
    monkeypatch.setattr("mws_run_state.ROOT", REPO_ROOT, raising=False)
    monkeypatch.setattr("mws_run_state.CONFIG_BUNDLES_DIR", tmp_path / "config-bundles", raising=False)
    monkeypatch.setattr("mws_run_state.VALIDATION_RUNS_DIR", tmp_path / "validation-runs", raising=False)
    return tmp_path


def _user_config(*, model: str = "qwen-smoke") -> dict:
    return {
        "motor_deploy_config": {
            "job_id": "ns1",
            "tls_config": {
                "mgmt_tls_config": {"enable_tls": False},
                "infer_tls_config": {"enable_tls": False},
            },
        },
        "motor_coordinator_config": {
            "api_key_config": {
                "enable_api_key": True,
                "valid_keys": ["must-not-leak"],
                "header_name": "X-API-Key",
                "key_prefix": "Token ",
            }
        },
        "motor_engine_prefill_config": {
            "engine_type": "vllm",
            "engine_config": {"served_model_name": model},
        },
        "motor_engine_decode_config": {
            "engine_type": "vllm",
            "engine_config": {"served-model-name": model},
        },
    }


def _write_ready_chain(local_root: Path) -> str:
    atomic_write_json(
        local_root / "machine-inventory.json",
        {
            "schema_version": 1,
            "machines": {
                "dev1": {
                    "alias": "dev1",
                    "host": "1.2.3.4",
                    "port": 22,
                    "user": "root",
                    "mount_root": "/mnt",
                    "kube_context": "ctx-a",
                    "remote_workspace_root": "/mnt/motor-workspace",
                }
            },
        },
    )
    source = local_root / "bundle-source"
    source.mkdir()
    user_config = source / "user_config.json"
    user_config.write_text(json.dumps(_user_config()), encoding="utf-8")
    manifest = source / "demo.yaml"
    manifest.write_text("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: demo\n", encoding="utf-8")
    bundle = create_config_bundle(
        config_fingerprint=digest_json({"job_id": "ns1", "smoke": True}),
        bundle_files={"user_config.json": user_config, "manifests/demo.yaml": manifest},
        metadata={"namespace": "ns1", "job_id": "ns1", "workload_names": ["deployment/demo"]},
    )
    write_run(
        "deploy-config-ready",
        "cfg-smoke",
        {
            "status": "ready",
            "workflow_run_id": "wf-smoke",
            "namespace": "ns1",
            "bundle_dir": bundle["bundle_dir"],
            "bundle_digest": bundle["bundle_digest"],
        },
    )
    write_run(
        "deploy-complete",
        "deploy-smoke",
        {
            "status": "ready",
            "workflow_run_id": "wf-smoke",
            "machine": "dev1",
            "config_run_id": "cfg-smoke",
            "namespace": "ns1",
            "bundle_dir": bundle["bundle_dir"],
            "bundle_digest": bundle["bundle_digest"],
        },
    )
    return "deploy-smoke"


def test_resolve_smoke_context_uses_bundle_model_without_secret(local_state_root) -> None:
    deploy_run_id = _write_ready_chain(local_state_root)
    context = resolve_smoke_context(machine_alias="dev1", deploy_run_id=deploy_run_id)
    assert context["model"] == "qwen-smoke"
    assert context["api_key_enabled"] is True
    assert context["api_key_header"] == "X-API-Key"
    assert "valid_keys" not in context
    assert "must-not-leak" not in json.dumps(context)


def test_resolve_smoke_context_rejects_failed_deploy(local_state_root) -> None:
    write_run(
        "deploy-complete",
        "deploy-failed",
        {"status": "failed", "machine": "dev1"},
    )
    with pytest.raises(WorkspaceStateError, match="not ready"):
        resolve_smoke_context(machine_alias="dev1", deploy_run_id="deploy-failed")


def test_resolve_model_name_rejects_role_mismatch() -> None:
    config = _user_config()
    config["motor_engine_decode_config"]["engine_config"]["served-model-name"] = "other"
    with pytest.raises(WorkspaceStateError, match="different model names"):
        resolve_model_name(config)


def test_discover_coordinator_services_ignores_controller_mgmt(monkeypatch) -> None:
    services = {
        "items": [
            {
                "metadata": {"name": "mindie-motor-service", "labels": {"app": "mindie-motor-controller"}},
                "spec": {"ports": [{"port": 1026, "targetPort": 1026}], "selector": {"app": "mindie-motor-controller"}},
            },
            {
                "metadata": {"name": "mindie-motor-coordinator-mgmt", "labels": {"app": "mindie-motor-coordinator"}},
                "spec": {"ports": [{"port": 1026, "targetPort": 1026}], "selector": {"app": "mindie-motor-coordinator"}},
            },
            {
                "metadata": {"name": "mindie-motor-coordinator-infer", "labels": {"app": "mindie-motor-coordinator"}},
                "spec": {"ports": [{"port": 1025, "targetPort": 1025}], "selector": {"app": "mindie-motor-coordinator"}},
            },
        ]
    }
    def fake_kubectl(*args):
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout=json.dumps(services), stderr=""
        )

    resolved = discover_coordinator_services(
        machine=_machine(),
        kube_context="ctx-a",
        namespace="ns1",
        kubectl=fake_kubectl,
    )
    assert resolved["infer"]["name"] == "mindie-motor-coordinator-infer"
    assert resolved["mgmt"]["name"] == "mindie-motor-coordinator-mgmt"


def test_wait_for_readiness_requires_ready_true() -> None:
    responses = iter(
        [
            {"status": 200, "body": '{"status":"ok","ready":false}'},
            {"status": 200, "body": '{"status":"ok","ready":true}'},
        ]
    )
    result = wait_for_readiness(lambda: next(responses), timeout=1, interval=0)
    assert result["json"]["ready"] is True


def test_validate_non_stream_response_requires_generated_output() -> None:
    summary = validate_non_stream_response(
        {
            "status": 200,
            "body": json.dumps(
                {"choices": [{"text": "MOTOR_SMOKE_OK"}], "usage": {"completion_tokens": 3}}
            ),
        }
    )
    assert summary["output_chars"] == len("MOTOR_SMOKE_OK")
    with pytest.raises(WorkspaceStateError, match="no generated output"):
        validate_non_stream_response({"status": 200, "body": '{"choices":[{"text":""}]}'})


def test_validate_stream_response_requires_output_and_done() -> None:
    body = (
        'data: {"choices":[{"text":"MOTOR"}]}\n\n'
        'data: {"choices":[{"text":"_SMOKE_OK"}]}\n\n'
        "data: [DONE]\n\n"
    )
    summary = validate_stream_response({"status": 200, "body": body})
    assert summary == {"events": 2, "choices_seen": 2, "output_chars": 14, "done": True}
    with pytest.raises(WorkspaceStateError, match="missing data"):
        validate_stream_response({"status": 200, "body": body.replace("data: [DONE]", "")})


def test_smoke_script_writes_ready_validation_run(tmp_path, monkeypatch) -> None:
    script = SCAFFOLD / ".agents/skills/motor-smoke/scripts/smoke_run.py"
    spec = importlib.util.spec_from_file_location("smoke_run_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    context = {
        "machine": "dev1",
        "deploy_run_id": "deploy-1",
        "config_run_id": "cfg-1",
        "workflow_run_id": "wf-1",
        "bundle_dir": "bundle",
        "bundle_digest": "abc",
        "namespace": "ns1",
        "kube_context": "ctx-a",
        "model": "qwen-smoke",
        "mgmt_tls_enabled": False,
        "infer_tls_enabled": False,
        "api_key_enabled": False,
        "api_key_header": "Authorization",
        "api_key_prefix": "Bearer ",
    }
    services = {
        "mgmt": {"name": "mindie-motor-coordinator-mgmt", "port": 1026},
        "infer": {"name": "mindie-motor-coordinator-infer", "port": 1025},
    }

    class FakeForward:
        def __init__(self, *args, **kwargs):
            self.local_port = 18000 + int(args[4])
            self.log = "closed"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.log = "closed"

    responses = iter(
        [
            {
                "status": 200,
                "body": json.dumps(
                    {"choices": [{"text": "MOTOR_SMOKE_OK"}], "usage": {"completion_tokens": 3}}
                ),
            },
            {
                "status": 200,
                "body": 'data: {"choices":[{"text":"MOTOR_SMOKE_OK"}]}\n\ndata: [DONE]\n\n',
            },
        ]
    )
    monkeypatch.setattr(module, "resolve_smoke_context", lambda **kwargs: context)
    monkeypatch.setattr(module, "get_machine", lambda alias: _machine())
    monkeypatch.setattr(module, "discover_coordinator_services", lambda **kwargs: services)
    monkeypatch.setattr(
        module,
        "ensure_service_endpoints",
        lambda **kwargs: {
            "mgmt": {"service": services["mgmt"]["name"], "ready_addresses": 1},
            "infer": {"service": services["infer"]["name"], "ready_addresses": 1},
        },
    )
    monkeypatch.setattr(module, "PortForward", FakeForward)
    monkeypatch.setattr(
        module,
        "wait_for_readiness",
        lambda *args, **kwargs: {
            "status": 200,
            "body": '{"status":"ok","ready":true}',
            "json": {"status": "ok", "ready": True},
        },
    )
    monkeypatch.setattr(module, "request_json", lambda **kwargs: next(responses))
    out_dir = tmp_path / "validation-runs" / "smoke-test"
    out_dir.mkdir(parents=True)
    monkeypatch.setattr(module, "validation_run_dir", lambda run_id: out_dir)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            "--machine",
            "dev1",
            "--deploy-run-id",
            "deploy-1",
            "--smoke-run-id",
            "smoke-test",
        ],
    )
    captured = {}
    monkeypatch.setattr(module, "emit_result", lambda payload: captured.setdefault("payload", payload) and 0)

    assert module.main() == 0
    assert captured["payload"]["status"] == "ready"
    assert [check["name"] for check in captured["payload"]["checks"]] == [
        "deploy_context",
        "coordinator_services",
        "motor_readiness",
        "non_stream_inference",
        "stream_inference",
    ]
    saved = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
    assert saved["status"] == "ready"
    assert (out_dir / "non-stream-response.json").exists()
    assert (out_dir / "stream-response.sse").exists()
