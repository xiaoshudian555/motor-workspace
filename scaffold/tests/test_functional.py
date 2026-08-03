from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_functional import (  # noqa: E402
    REQUEST_SUCCESS_METRIC,
    compile_validation_spec,
    dispatch_validation_spec,
    load_case_catalog,
    parse_prometheus_samples,
    prometheus_metric_total,
    validate_metrics_response,
    validate_non_stream_response,
    validate_stream_response,
    validate_tempo_trace_response,
    wait_for_metric_increase,
    wait_for_tempo_trace,
    write_validation_spec,
)
from mws_local_state import WorkspaceStateError  # noqa: E402
from mws_result import aggregate_result_status  # noqa: E402


def test_compile_intent_to_feature_defaults() -> None:
    spec = compile_validation_spec(
        user_request="验证 API key：正确 key 能访问，错误或没带 key 要拒绝",
        machine="dev1",
        deploy_run_id="deploy-1",
    )

    assert spec["schema_version"] == "mws.functional.spec.v1"
    assert spec["features"] == ["api-key"]
    assert [case["id"] for case in spec["cases"]] == [
        "api-key.valid",
        "api-key.missing",
        "api-key.invalid",
    ]
    assert spec["pass_policy"] == {"all_selected_cases_must_pass": True}


def test_agent_can_select_the_smallest_case_set() -> None:
    spec = compile_validation_spec(
        user_request="只验证没有 API key 时会被拒绝",
        machine="dev1",
        deploy_run_id="deploy-1",
        selected_features=["api-key"],
        selected_cases=["api-key.missing"],
    )

    assert [case["id"] for case in spec["cases"]] == ["api-key.missing"]


def test_unknown_intent_requires_explicit_catalog_selection() -> None:
    with pytest.raises(WorkspaceStateError, match="select a feature explicitly"):
        compile_validation_spec(
            user_request="验证一个还没进目录的新功能",
            machine="dev1",
            deploy_run_id="deploy-1",
        )


def test_dispatch_uses_adapter_map_and_existing_check_statuses() -> None:
    spec = compile_validation_spec(
        user_request="验证 API key",
        machine="dev1",
        deploy_run_id="deploy-1",
        selected_features=["api-key"],
        selected_cases=["api-key.valid", "api-key.invalid"],
    )
    seen: list[str] = []

    def openai_http(case, run_spec):
        seen.append(case["id"])
        assert run_spec is spec
        return {"status": "ok", "message": "case executed"}

    checks = dispatch_validation_spec(spec, handlers={"openai-http": openai_http})

    assert seen == ["api-key.valid", "api-key.invalid"]
    assert [check["status"] for check in checks] == ["ok", "ok"]
    assert aggregate_result_status(checks) == "ready"


def test_missing_adapter_is_unavailable_not_success() -> None:
    spec = compile_validation_spec(
        user_request="验证 metrics",
        machine="dev1",
        deploy_run_id="deploy-1",
        selected_features=["metrics"],
        selected_cases=["metrics.exposed"],
    )

    checks = dispatch_validation_spec(spec, handlers={})

    assert checks[0]["status"] == "unavailable"
    assert aggregate_result_status(checks) == "failed"


def test_resolved_spec_is_immutable(tmp_path: Path) -> None:
    spec = compile_validation_spec(
        user_request="验证 tracing",
        machine="dev1",
        deploy_run_id="deploy-1",
        selected_features=["tracing"],
    )
    path = tmp_path / "validation-spec.json"

    write_validation_spec(path, spec)
    assert json.loads(path.read_text(encoding="utf-8")) == spec
    with pytest.raises(WorkspaceStateError, match="already exists"):
        write_validation_spec(path, spec)


def test_catalog_cases_have_feature_owners_and_adapters() -> None:
    catalog = load_case_catalog()
    for case_id, case in catalog["cases"].items():
        assert case["feature"] in catalog["features"], case_id
        assert case["adapter"], case_id


def test_validate_non_stream_response_requires_generated_output() -> None:
    summary = validate_non_stream_response(
        {
            "status": 200,
            "body": json.dumps(
                {
                    "choices": [{"text": "MOTOR_FUNCTIONAL_OK"}],
                    "usage": {"completion_tokens": 3},
                }
            ),
        }
    )
    assert summary["output_chars"] == len("MOTOR_FUNCTIONAL_OK")
    with pytest.raises(WorkspaceStateError, match="no generated output"):
        validate_non_stream_response({"status": 200, "body": '{"choices":[{"text":""}]}'})


def test_validate_stream_response_requires_output_and_done() -> None:
    body = (
        'data: {"choices":[{"text":"MOTOR"}]}\n\n'
        'data: {"choices":[{"text":"_FUNCTIONAL_OK"}]}\n\n'
        "data: [DONE]\n\n"
    )
    summary = validate_stream_response({"status": 200, "body": body})
    assert summary == {"events": 2, "choices_seen": 2, "output_chars": 19, "done": True}
    with pytest.raises(WorkspaceStateError, match="missing data"):
        validate_stream_response({"status": 200, "body": body.replace("data: [DONE]", "")})


def _metrics_response(value: float) -> dict:
    return {
        "status": 200,
        "content_type": "text/plain; charset=utf-8",
        "body": (
            "# HELP vllm:request_success_total Successful requests.\n"
            "# TYPE vllm:request_success_total counter\n"
            f'vllm:request_success_total{{finished_reason="stop"}} {value}\n'
            "# HELP motor:active_decode_workers Active decode workers.\n"
            "# TYPE motor:active_decode_workers gauge\n"
            "motor:active_decode_workers 1\n"
        ),
    }


def test_metrics_validation_preserves_motor_metric_names_and_observes_delta() -> None:
    before = _metrics_response(4)
    samples = parse_prometheus_samples(before["body"])
    assert samples[REQUEST_SUCCESS_METRIC] == [4.0]
    assert validate_metrics_response(before)["motor_family_count"] == 2
    assert prometheus_metric_total(before, REQUEST_SUCCESS_METRIC) == 4

    responses = iter([_metrics_response(4), _metrics_response(5)])
    after, total = wait_for_metric_increase(
        lambda: next(responses),
        metric_name=REQUEST_SUCCESS_METRIC,
        before=4,
        timeout=1,
        interval=0,
    )
    assert total == 5
    assert after["status"] == 200


def _tempo_response(trace_id: str) -> dict:
    return {
        "status": 200,
        "content_type": "application/json",
        "body": json.dumps(
            {
                "batches": [
                    {
                        "scopeSpans": [
                            {
                                "spans": [
                                    {
                                        "traceId": trace_id,
                                        "spanId": "1" * 16,
                                        "name": "Inference",
                                        "attributes": [
                                            {
                                                "key": "requestId",
                                                "value": {
                                                    "stringValue": f"{trace_id}-a1b2c3d4"
                                                },
                                            }
                                        ],
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ),
    }


def test_tempo_trace_validation_requires_trace_and_motor_request_correlation() -> None:
    trace_id = "a" * 32
    summary = validate_tempo_trace_response(
        _tempo_response(trace_id), expected_trace_id=trace_id
    )
    assert summary["span_count"] == 1
    assert summary["correlated_request_ids"] == [f"{trace_id}-a1b2c3d4"]

    incomplete = {
        "status": 200,
        "body": json.dumps({"batches": [{"scopeSpans": [{"spans": []}]}]}),
    }
    responses = iter([incomplete, _tempo_response(trace_id)])
    response, waited = wait_for_tempo_trace(
        lambda: next(responses),
        expected_trace_id=trace_id,
        timeout=1,
        interval=0,
    )
    assert response["status"] == 200
    assert waited["trace_id"] == trace_id


def test_functional_run_owns_real_inference_requests(tmp_path, monkeypatch) -> None:
    script = SCAFFOLD / ".agents/skills/motor-functional/scripts/functional_run.py"
    module_spec = importlib.util.spec_from_file_location("functional_run_test", script)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)

    context = {
        "machine": "dev1",
        "deploy_run_id": "deploy-1",
        "config_run_id": "cfg-1",
        "workflow_run_id": "wf-1",
        "bundle_dir": "bundle",
        "bundle_digest": "abc",
        "namespace": "ns1",
        "kube_context": "ctx-a",
        "model": "qwen-functional",
        "mgmt_tls_enabled": False,
        "infer_tls_enabled": False,
        "api_key_enabled": False,
        "api_key_header": "Authorization",
        "api_key_prefix": "Bearer ",
    }
    services = {
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
                    {
                        "choices": [{"text": "MOTOR_FUNCTIONAL_OK"}],
                        "usage": {"completion_tokens": 3},
                    }
                ),
            },
            {
                "status": 200,
                "body": (
                    'data: {"choices":[{"text":"MOTOR_FUNCTIONAL_OK"}]}\n\n'
                    "data: [DONE]\n\n"
                ),
            },
        ]
    )
    monkeypatch.setattr(module, "resolve_validation_context", lambda **kwargs: context)
    monkeypatch.setattr(module, "get_machine", lambda alias: {"alias": alias})
    monkeypatch.setattr(module, "discover_coordinator_services", lambda **kwargs: services)
    monkeypatch.setattr(
        module,
        "ensure_service_endpoints",
        lambda **kwargs: {
            "infer": {"service": services["infer"]["name"], "ready_addresses": 1}
        },
    )
    monkeypatch.setattr(module, "PortForward", FakeForward)
    monkeypatch.setattr(module, "request_json", lambda **kwargs: next(responses))
    out_dir = tmp_path / "validation-runs" / "functional-test"
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
            "--functional-run-id",
            "functional-test",
            "--request",
            "验证推理请求",
            "--feature",
            "inference-request",
        ],
    )
    captured = {}
    monkeypatch.setattr(module, "emit_result", lambda payload: captured.setdefault("payload", payload) and 0)

    assert module.main() == 0
    assert captured["payload"]["status"] == "ready"
    assert [check["name"] for check in captured["payload"]["checks"]] == [
        "deploy_context",
        "inference_service",
        "inference-request.non-stream",
        "inference-request.stream",
    ]
    assert (out_dir / "cases/inference-request.non-stream/response.json").exists()
    assert (out_dir / "cases/inference-request.stream/response.sse").exists()


def test_functional_run_executes_metrics_and_tracing(tmp_path, monkeypatch) -> None:
    script = SCAFFOLD / ".agents/skills/motor-functional/scripts/functional_run.py"
    module_spec = importlib.util.spec_from_file_location("functional_obs_run_test", script)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader is not None
    module_spec.loader.exec_module(module)

    context = {
        "machine": "dev1",
        "deploy_run_id": "deploy-1",
        "config_run_id": "cfg-1",
        "workflow_run_id": "wf-1",
        "bundle_dir": "bundle",
        "bundle_digest": "abc",
        "namespace": "ns1",
        "kube_context": "ctx-a",
        "model": "qwen-functional",
        "mgmt_tls_enabled": False,
        "infer_tls_enabled": False,
        "api_key_enabled": False,
        "api_key_header": "Authorization",
        "api_key_prefix": "Bearer ",
        "tracing_enabled": True,
        "tracing_export_host": "obs.example.com",
        "tracing_remote_parent_sampled": 1.0,
    }
    services = {
        "infer": {"name": "mindie-motor-coordinator-infer", "port": 1025},
        "obs": {"name": "mindie-motor-coordinator-obs", "port": 1027},
    }

    class FakeForward:
        def __init__(self, *args, **kwargs):
            self.local_port = 18000 + int(args[4])
            self.log = ""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.log = "closed"

    tempo_targets = []

    class FakeTempoForward:
        def __init__(self, *args, **kwargs):
            tempo_targets.append((args, kwargs))
            self.local_port = 23200
            self.log = ""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.log = "closed"

    metric_queries = 0
    trace_id = ""

    def fake_request_json(**kwargs):
        nonlocal metric_queries, trace_id
        path = kwargs["path"]
        if path == "/metrics":
            values = [4, 4, 5]
            response = _metrics_response(values[metric_queries])
            metric_queries += 1
            return response
        if path == "/ready":
            return {"status": 200, "body": "ready"}
        if path.startswith("/api/traces/"):
            assert path == f"/api/traces/{trace_id}"
            return _tempo_response(trace_id)
        if path == "/v1/completions":
            traceparent = kwargs.get("headers", {}).get("traceparent", "")
            if traceparent:
                trace_id = traceparent.split("-")[1]
            return {
                "status": 200,
                "body": json.dumps(
                    {
                        "choices": [{"text": "MOTOR_FUNCTIONAL_OK"}],
                        "usage": {"completion_tokens": 3},
                    }
                ),
            }
        raise AssertionError(f"unexpected request path: {path}")

    monkeypatch.setattr(module, "resolve_validation_context", lambda **kwargs: context)
    monkeypatch.setattr(module, "get_machine", lambda alias: {"alias": alias})
    monkeypatch.setattr(module, "discover_coordinator_services", lambda **kwargs: services)
    monkeypatch.setattr(
        module,
        "ensure_service_endpoints",
        lambda **kwargs: {
            role: {"service": service["name"], "ready_addresses": 1}
            for role, service in services.items()
        },
    )
    monkeypatch.setattr(module, "PortForward", FakeForward)
    monkeypatch.setattr(module, "RemoteHostPortForward", FakeTempoForward)
    monkeypatch.setattr(module, "request_json", fake_request_json)
    out_dir = tmp_path / "validation-runs" / "functional-obs-test"
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
            "--functional-run-id",
            "functional-obs-test",
            "--request",
            "验证 metrics 和 tracing",
            "--feature",
            "metrics",
            "--feature",
            "tracing",
        ],
    )
    captured = {}
    monkeypatch.setattr(
        module,
        "emit_result",
        lambda payload: captured.setdefault("payload", payload) and 0,
    )

    assert module.main() == 0
    assert captured["payload"]["status"] == "ready"
    assert [check["name"] for check in captured["payload"]["checks"]] == [
        "deploy_context",
        "inference_service",
        "observability_service",
        "metrics.exposed",
        "metrics.request-updated",
        "tracing.request-correlated",
    ]
    assert metric_queries == 3
    assert len(trace_id) == 32
    assert tempo_targets[0][1]["remote_host"] == "obs.example.com"
    assert (out_dir / "cases/metrics.exposed/metrics.prom").exists()
    assert (out_dir / "cases/metrics.request-updated/metrics-before.prom").exists()
    assert (out_dir / "cases/metrics.request-updated/metrics-after.prom").exists()
    assert (out_dir / "cases/tracing.request-correlated/trace.json").exists()
