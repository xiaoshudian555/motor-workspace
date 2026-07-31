#!/usr/bin/env python3
"""JSON result contract for motor-workspace workflow scripts."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

RESULT_SCHEMA_VERSION = "mws.result.v1"
CheckStatus = Literal["ok", "warning", "error", "unavailable"]
ResultStatus = Literal["ready", "failed"]
TerminalCheckStatus = Literal["error", "unavailable"]

CHECK_TERMINAL: frozenset[str] = frozenset({"error", "unavailable"})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def progress(message: str, *, sentinel: str = "__MWS_PROGRESS__") -> None:
    print(
        f"{sentinel}={json.dumps({'message': message}, ensure_ascii=False)}",
        file=sys.stderr,
    )


def normalize_check(record: dict[str, Any]) -> dict[str, Any]:
    name = str(record.get("name", "")).strip()
    if not name:
        raise ValueError("check name is required")
    status = str(record.get("status", "")).strip()
    if status not in {"ok", "warning", "error", "unavailable"}:
        raise ValueError(f"invalid check status: {status!r}")
    normalized: dict[str, Any] = {
        "name": name,
        "status": status,
        "message": str(record.get("message", record.get("error", "")) or ""),
    }
    evidence = record.get("evidence")
    if evidence not in (None, ""):
        normalized["evidence"] = evidence
    artifact_refs = record.get("artifact_refs")
    if artifact_refs:
        normalized["artifact_refs"] = list(artifact_refs)
    return normalized


def aggregate_result_status(checks: list[dict[str, Any]]) -> ResultStatus:
    if any(check["status"] in CHECK_TERMINAL for check in checks):
        return "failed"
    return "ready"


def exit_code_for_result(status: str | ResultStatus, *, legacy: bool = False) -> int:
    if legacy:
        if status in {"ok", "warning", "ready"}:
            return 0
        return 1
    if status == "ready":
        return 0
    return 1


@dataclass
class CheckRunner:
    """Execute checks sequentially; stop on first error/unavailable."""

    checks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stopped_at: str | None = None

    @property
    def continue_ok(self) -> bool:
        return self.stopped_at is None

    def append(self, record: dict[str, Any]) -> bool:
        """Append a check. Return False when execution must stop."""
        check = normalize_check(record)
        self.checks.append(check)
        status = check["status"]
        message = check.get("message") or check.get("name")
        if status == "warning":
            self.warnings.append(str(message))
            return True
        if status in CHECK_TERMINAL:
            self.errors.append(str(message))
            self.stopped_at = check["name"]
            return False
        return True

    def run(self, name: str, fn: Callable[[], dict[str, Any]]) -> bool:
        try:
            record = fn()
        except Exception as exc:  # noqa: BLE001
            return self.append({"name": name, "status": "error", "message": str(exc)})
        if "name" not in record:
            record = dict(record)
            record["name"] = name
        return self.append(record)


def build_result_envelope(
    *,
    kind: str,
    run_id: str,
    workflow_run_id: str,
    checks: list[dict[str, Any]],
    started_at: str,
    finished_at: str | None = None,
    upstream_refs: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    status: ResultStatus | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_checks = [normalize_check(item) for item in checks]
    resolved_status = status or aggregate_result_status(normalized_checks)
    payload: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "kind": kind,
        "run_id": run_id,
        "workflow_run_id": workflow_run_id,
        "status": resolved_status,
        "started_at": started_at,
        "finished_at": finished_at or utc_now_iso(),
        "upstream_refs": list(upstream_refs or []),
        "checks": normalized_checks,
        "warnings": list(warnings or []),
        "errors": list(errors or []),
        "artifacts": list(artifacts or []),
    }
    if extra:
        payload.update(extra)
    return payload


def emit(payload: dict[str, Any]) -> int:
    """Emit JSON to stdout and derive exit code from payload status."""
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    if payload.get("schema_version") == RESULT_SCHEMA_VERSION:
        return exit_code_for_result(str(payload.get("status", "failed")))
    status = payload.get("status")
    return exit_code_for_result(str(status or "error"), legacy=True)


def emit_error(message: str, **extra: Any) -> int:
    payload = {"status": "error", "errors": [message], **extra}
    return emit(payload)


def emit_result(envelope: dict[str, Any]) -> int:
    if envelope.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise ValueError("emit_result expects mws.result.v1 envelope")
    return emit(envelope)
