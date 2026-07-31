from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_result import (  # noqa: E402
    CHECK_TERMINAL,
    CheckRunner,
    aggregate_result_status,
    build_result_envelope,
    emit,
    exit_code_for_result,
    normalize_check,
)


def test_warning_allows_ready_status() -> None:
    checks = [
        normalize_check({"name": "a", "status": "ok", "message": "fine"}),
        normalize_check({"name": "b", "status": "warning", "message": "soft"}),
    ]
    assert aggregate_result_status(checks) == "ready"


def test_error_marks_failed() -> None:
    checks = [
        normalize_check({"name": "a", "status": "ok", "message": ""}),
        normalize_check({"name": "b", "status": "error", "message": "boom"}),
    ]
    assert aggregate_result_status(checks) == "failed"


def test_check_runner_stops_on_unavailable() -> None:
    runner = CheckRunner()
    assert runner.append({"name": "first", "status": "ok", "message": ""})
    assert runner.append({"name": "second", "status": "warning", "message": "warn"})
    assert runner.append({"name": "third", "status": "unavailable", "message": "down"}) is False
    assert runner.stopped_at == "third"
    assert len(runner.checks) == 3


def test_build_result_envelope_fields() -> None:
    envelope = build_result_envelope(
        kind="machine-ready",
        run_id="machine-1",
        workflow_run_id="workflow-1",
        checks=[{"name": "ssh", "status": "ok", "message": ""}],
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:01+00:00",
        upstream_refs=[{"kind": "workspace-ready", "run_id": "ws-1"}],
    )
    assert envelope["schema_version"] == "mws.result.v1"
    assert envelope["status"] == "ready"
    assert envelope["checks"][0]["name"] == "ssh"


def test_emit_legacy_and_envelope_exit_codes() -> None:
    assert exit_code_for_result("ready") == 0
    assert exit_code_for_result("failed") == 1
    assert exit_code_for_result("ok", legacy=True) == 0
    assert exit_code_for_result("error", legacy=True) == 1


def test_invalid_check_status_raises() -> None:
    with pytest.raises(ValueError):
        normalize_check({"name": "x", "status": "skipped"})


def test_terminal_statuses_frozen() -> None:
    assert CHECK_TERMINAL == frozenset({"error", "unavailable"})
