#!/usr/bin/env python3
"""Compile functional validation intent and dispatch catalog-backed cases."""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any, Callable

from mws_local_state import WorkspaceStateError
from mws_result import normalize_check
from mws_state import atomic_write_json
from repo_paths import AGENTS_ROOT

SPEC_SCHEMA_VERSION = "mws.functional.spec.v1"
CATALOG_SCHEMA_VERSION = "mws.functional.catalog.v1"
DEFAULT_CATALOG_PATH = (
    AGENTS_ROOT / "skills" / "motor-functional" / "references" / "case-catalog.json"
)

CaseHandler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]

REQUEST_SUCCESS_METRIC = "vllm:request_success_total"
_PROMETHEUS_SAMPLE_RE = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?:\{.*\})?\s+"
    r"(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|[+-]?Inf|NaN)"
    r"(?:\s+\d+)?$"
)


def load_case_catalog(path: Path = DEFAULT_CATALOG_PATH) -> dict[str, Any]:
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceStateError(f"cannot load functional case catalog {path}: {exc}") from exc
    if not isinstance(catalog, dict):
        raise WorkspaceStateError("functional case catalog must be an object")
    if catalog.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise WorkspaceStateError("unsupported functional case catalog schema")
    features = catalog.get("features")
    cases = catalog.get("cases")
    if not isinstance(features, dict) or not features:
        raise WorkspaceStateError("functional case catalog has no features")
    if not isinstance(cases, dict) or not cases:
        raise WorkspaceStateError("functional case catalog has no cases")
    return catalog


def resolve_feature_ids(
    user_request: str,
    *,
    catalog: dict[str, Any],
    selected_features: list[str] | None = None,
) -> list[str]:
    features = catalog["features"]
    if selected_features:
        unknown = [feature_id for feature_id in selected_features if feature_id not in features]
        if unknown:
            raise WorkspaceStateError(
                f"unknown functional feature(s): {', '.join(dict.fromkeys(unknown))}"
            )
        return list(dict.fromkeys(selected_features))

    normalized_request = user_request.casefold()
    resolved: list[str] = []
    for feature_id, feature in features.items():
        aliases = [feature_id, *feature.get("aliases", [])]
        if any(str(alias).casefold() in normalized_request for alias in aliases):
            resolved.append(feature_id)
    if not resolved:
        raise WorkspaceStateError(
            "could not map the request to a functional feature; select a feature explicitly"
        )
    return resolved


def _resolve_case_ids(
    feature_ids: list[str],
    *,
    catalog: dict[str, Any],
    selected_cases: list[str] | None,
) -> list[str]:
    features = catalog["features"]
    cases = catalog["cases"]
    if selected_cases:
        unknown = [case_id for case_id in selected_cases if case_id not in cases]
        if unknown:
            raise WorkspaceStateError(
                f"unknown functional case(s): {', '.join(dict.fromkeys(unknown))}"
            )
        outside = [
            case_id
            for case_id in selected_cases
            if str(cases[case_id].get("feature")) not in feature_ids
        ]
        if outside:
            raise WorkspaceStateError(
                "selected cases do not belong to selected features: " + ", ".join(outside)
            )
        return list(dict.fromkeys(selected_cases))

    resolved: list[str] = []
    for feature_id in feature_ids:
        defaults = features[feature_id].get("default_cases", [])
        if not isinstance(defaults, list) or not defaults:
            raise WorkspaceStateError(f"functional feature {feature_id!r} has no default cases")
        for case_id in defaults:
            if case_id not in cases:
                raise WorkspaceStateError(
                    f"functional feature {feature_id!r} references unknown case {case_id!r}"
                )
            if cases[case_id].get("feature") != feature_id:
                raise WorkspaceStateError(
                    f"functional case {case_id!r} has inconsistent feature ownership"
                )
            if case_id not in resolved:
                resolved.append(case_id)
    return resolved


def compile_validation_spec(
    *,
    user_request: str,
    machine: str,
    deploy_run_id: str,
    selected_features: list[str] | None = None,
    selected_cases: list[str] | None = None,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile user intent into a deterministic, catalog-backed run specification."""
    request = user_request.strip()
    if not request:
        raise WorkspaceStateError("functional validation request is required")
    case_catalog = catalog or load_case_catalog()
    feature_ids = resolve_feature_ids(
        request,
        catalog=case_catalog,
        selected_features=selected_features,
    )
    case_ids = _resolve_case_ids(
        feature_ids,
        catalog=case_catalog,
        selected_cases=selected_cases,
    )
    compiled_cases: list[dict[str, Any]] = []
    for case_id in case_ids:
        case = dict(case_catalog["cases"][case_id])
        compiled_cases.append({"id": case_id, **case})
    return {
        "schema_version": SPEC_SCHEMA_VERSION,
        "validation_type": "functional",
        "user_request": request,
        "target": {
            "machine": machine,
            "deploy_run_id": deploy_run_id,
        },
        "features": feature_ids,
        "cases": compiled_cases,
        "pass_policy": {"all_selected_cases_must_pass": True},
    }


def validate_validation_spec(spec: dict[str, Any]) -> None:
    if spec.get("schema_version") != SPEC_SCHEMA_VERSION:
        raise WorkspaceStateError("unsupported functional validation spec schema")
    if spec.get("validation_type") != "functional":
        raise WorkspaceStateError("validation_type must be functional")
    target = spec.get("target")
    if not isinstance(target, dict) or not target.get("machine") or not target.get("deploy_run_id"):
        raise WorkspaceStateError("functional validation spec target is incomplete")
    cases = spec.get("cases")
    if not isinstance(cases, list) or not cases:
        raise WorkspaceStateError("functional validation spec has no cases")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise WorkspaceStateError("functional validation case must be an object")
        case_id = str(case.get("id") or "")
        if not case_id or case_id in seen:
            raise WorkspaceStateError("functional validation case ids must be unique and non-empty")
        seen.add(case_id)
        if not str(case.get("adapter") or ""):
            raise WorkspaceStateError(f"functional validation case {case_id!r} has no adapter")


def write_validation_spec(path: Path, spec: dict[str, Any]) -> Path:
    """Persist one immutable resolved spec without creating another run state model."""
    validate_validation_spec(spec)
    if path.exists():
        raise WorkspaceStateError(f"functional validation spec already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, spec)
    return path


def dispatch_validation_spec(
    spec: dict[str, Any],
    *,
    handlers: dict[str, CaseHandler],
) -> list[dict[str, Any]]:
    """Dispatch cases through an explicit adapter-to-handler map."""
    validate_validation_spec(spec)
    checks: list[dict[str, Any]] = []
    for case in spec["cases"]:
        case_id = str(case["id"])
        adapter = str(case["adapter"])
        handler = handlers.get(adapter)
        if handler is None:
            checks.append(
                normalize_check(
                    {
                        "name": case_id,
                        "status": "unavailable",
                        "message": f"functional adapter {adapter!r} is not implemented",
                    }
                )
            )
            continue
        try:
            record = dict(handler(case, spec))
            record["name"] = case_id
        except Exception as exc:  # noqa: BLE001
            record = {"name": case_id, "status": "error", "message": str(exc)}
        checks.append(normalize_check(record))
    return checks


def validate_non_stream_response(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("status") != 200:
        raise WorkspaceStateError(
            f"non-stream inference returned HTTP {response.get('status')}: "
            f"{str(response.get('body'))[:500]}"
        )
    try:
        body = json.loads(str(response.get("body") or ""))
    except json.JSONDecodeError as exc:
        raise WorkspaceStateError("non-stream inference response is not JSON") from exc
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices:
        raise WorkspaceStateError("non-stream inference response has no choices")
    fragments: list[str] = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        if isinstance(choice.get("text"), str):
            fragments.append(choice["text"])
        message = choice.get("message")
        if isinstance(message, dict):
            for key in ("content", "reasoning_content"):
                if isinstance(message.get(key), str):
                    fragments.append(message[key])
    usage = body.get("usage") if isinstance(body, dict) and isinstance(body.get("usage"), dict) else {}
    completion_tokens = int(usage.get("completion_tokens") or 0)
    if not any(fragment for fragment in fragments) and completion_tokens < 1:
        raise WorkspaceStateError("non-stream inference returned no generated output")
    return {
        "choices": len(choices),
        "completion_tokens": completion_tokens,
        "output_chars": sum(len(fragment) for fragment in fragments),
    }


def validate_stream_response(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("status") != 200:
        raise WorkspaceStateError(
            f"stream inference returned HTTP {response.get('status')}: "
            f"{str(response.get('body'))[:500]}"
        )
    events = 0
    choices_seen = 0
    output_chars = 0
    done = False
    for line in str(response.get("body") or "").splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            done = True
            continue
        if not data:
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError as exc:
            raise WorkspaceStateError("stream inference contains invalid SSE JSON") from exc
        events += 1
        choices = event.get("choices") if isinstance(event, dict) else None
        if not isinstance(choices, list):
            continue
        choices_seen += len(choices)
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            if isinstance(choice.get("text"), str):
                output_chars += len(choice["text"])
            delta = choice.get("delta")
            if isinstance(delta, dict):
                for key in ("content", "reasoning_content"):
                    if isinstance(delta.get(key), str):
                        output_chars += len(delta[key])
    if not done:
        raise WorkspaceStateError("stream inference response is missing data: [DONE]")
    if events < 1 or choices_seen < 1:
        raise WorkspaceStateError("stream inference response has no choice events")
    if output_chars < 1:
        raise WorkspaceStateError("stream inference returned no generated output")
    return {"events": events, "choices_seen": choices_seen, "output_chars": output_chars, "done": done}


def parse_prometheus_samples(text: str) -> dict[str, list[float]]:
    """Parse the numeric samples needed by Functional without normalizing Motor metric names."""
    samples: dict[str, list[float]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _PROMETHEUS_SAMPLE_RE.match(line)
        if match is None:
            continue
        value = float(match.group("value"))
        samples.setdefault(match.group("name"), []).append(value)
    return samples


def validate_metrics_response(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("status") != 200:
        raise WorkspaceStateError(
            f"metrics endpoint returned HTTP {response.get('status')}: "
            f"{str(response.get('body'))[:500]}"
        )
    body = str(response.get("body") or "")
    families = {
        line.split()[2]
        for line in body.splitlines()
        if line.startswith("# TYPE ") and len(line.split()) >= 4
    }
    samples = parse_prometheus_samples(body)
    if not families or not samples:
        raise WorkspaceStateError("metrics endpoint returned no Prometheus metric families/samples")
    motor_families = {
        name for name in families if name.startswith(("motor:", "vllm:"))
    }
    if not motor_families:
        raise WorkspaceStateError("metrics endpoint returned no Motor/vLLM metric families")
    return {
        "http_status": 200,
        "content_type": str(response.get("content_type") or ""),
        "family_count": len(families),
        "sample_count": sum(len(values) for values in samples.values()),
        "motor_family_count": len(motor_families),
    }


def prometheus_metric_total(response: dict[str, Any], metric_name: str) -> float:
    validate_metrics_response(response)
    values = parse_prometheus_samples(str(response.get("body") or "")).get(metric_name)
    if not values:
        raise WorkspaceStateError(f"metrics endpoint has no {metric_name!r} samples")
    finite_values = [value for value in values if math.isfinite(value)]
    if not finite_values:
        raise WorkspaceStateError(f"metric {metric_name!r} has no finite samples")
    return sum(finite_values)


def wait_for_metric_increase(
    request: Callable[[], dict[str, Any]],
    *,
    metric_name: str,
    before: float,
    timeout: float,
    interval: float = 1.0,
) -> tuple[dict[str, Any], float]:
    deadline = time.monotonic() + timeout
    last_total = before
    last_response: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_response = request()
        last_total = prometheus_metric_total(last_response, metric_name)
        if last_total > before:
            return last_response, last_total
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
    raise WorkspaceStateError(
        f"metric {metric_name!r} did not increase within {timeout:g}s; "
        f"before={before:g} last={last_total:g}"
    )


def validate_tempo_trace_response(
    response: dict[str, Any], *, expected_trace_id: str
) -> dict[str, Any]:
    if response.get("status") != 200:
        raise WorkspaceStateError(
            f"Tempo trace query returned HTTP {response.get('status')}: "
            f"{str(response.get('body'))[:500]}"
        )
    try:
        payload = json.loads(str(response.get("body") or ""))
    except json.JSONDecodeError as exc:
        raise WorkspaceStateError("Tempo trace query response is not JSON") from exc

    spans: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if str(value.get("traceId") or "").lower() == expected_trace_id.lower():
                spans.append(value)
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(payload)
    if not spans:
        raise WorkspaceStateError(
            f"Tempo response contains no spans for trace id {expected_trace_id}"
        )

    request_ids: list[str] = []
    for span in spans:
        attributes = span.get("attributes")
        if not isinstance(attributes, list):
            continue
        for attribute in attributes:
            if not isinstance(attribute, dict) or attribute.get("key") != "requestId":
                continue
            value = attribute.get("value")
            if isinstance(value, dict) and isinstance(value.get("stringValue"), str):
                request_ids.append(value["stringValue"])
    correlated_ids = [
        request_id
        for request_id in request_ids
        if request_id.lower().startswith(f"{expected_trace_id.lower()}-")
    ]
    if not correlated_ids:
        raise WorkspaceStateError(
            "Tempo trace has the injected trace id but no correlated Motor requestId attribute"
        )
    return {
        "trace_id": expected_trace_id,
        "span_count": len(spans),
        "span_names": sorted(
            {str(span.get("name")) for span in spans if span.get("name")}
        ),
        "correlated_request_ids": sorted(set(correlated_ids)),
    }


def wait_for_tempo_trace(
    request: Callable[[], dict[str, Any]],
    *,
    expected_trace_id: str,
    timeout: float,
    interval: float = 1.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last_status = 0
    last_body = "no response"
    while time.monotonic() < deadline:
        try:
            response = request()
            last_status = int(response.get("status") or 0)
            last_body = str(response.get("body") or "")
            if last_status == 200:
                try:
                    summary = validate_tempo_trace_response(
                        response, expected_trace_id=expected_trace_id
                    )
                except WorkspaceStateError as exc:
                    # Tempo can expose a trace before every related span is searchable.
                    last_body = str(exc)
                else:
                    return response, summary
        except (OSError, TimeoutError) as exc:
            last_body = str(exc)
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
    raise WorkspaceStateError(
        f"trace {expected_trace_id} did not become queryable in Tempo within {timeout:g}s; "
        f"last_status={last_status} last_body={last_body[:500]}"
    )
