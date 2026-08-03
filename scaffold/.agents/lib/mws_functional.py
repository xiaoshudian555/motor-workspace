#!/usr/bin/env python3
"""Compile functional validation intent and dispatch catalog-backed cases."""

from __future__ import annotations

import json
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
