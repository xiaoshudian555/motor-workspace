from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_functional import (  # noqa: E402
    compile_validation_spec,
    dispatch_validation_spec,
    load_case_catalog,
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
