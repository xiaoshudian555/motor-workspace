"""Helpers for writing or exercising machine-ready evidence in tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mws_machine_target import machine_ref, endpoint_payload_for_machine
from mws_result import RESULT_SCHEMA_VERSION, build_result_envelope, utc_now_iso
from mws_run_state import write_run


def default_machine_ready_checks() -> list[dict[str, Any]]:
    return [
        {"name": "ssh", "status": "ok", "message": "", "evidence": "root@dev1"},
        {"name": "mount_root", "status": "ok", "message": "", "evidence": "/mnt"},
        {"name": "remote_workspace_path", "status": "ok", "message": "", "evidence": "/mnt/motor-workspace"},
        {"name": "remote_workspace_root", "status": "ok", "message": "", "evidence": "/mnt/motor-workspace"},
        {"name": "parity_tool:tar", "status": "ok", "message": "", "evidence": "/usr/bin/tar"},
        {"name": "parity_tool:mkdir", "status": "ok", "message": "", "evidence": "/usr/bin/mkdir"},
        {"name": "shared_hostpath_root", "status": "ok", "message": "", "evidence": "/mnt"},
        {"name": "parity_backend", "status": "ok", "message": "", "evidence": "shared-hostpath"},
    ]


def build_machine_ready_run_payload(
    machine: dict[str, Any],
    *,
    run_id: str,
    workflow_run_id: str = "wf-test-1",
    status: str = "ready",
    checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    alias = str(machine.get("alias") or machine.get("host"))
    ref = machine_ref(machine)
    endpoint = endpoint_payload_for_machine(machine)
    started_at = utc_now_iso()
    envelope = build_result_envelope(
        kind="machine-ready",
        run_id=run_id,
        workflow_run_id=workflow_run_id,
        checks=checks if checks is not None else default_machine_ready_checks(),
        started_at=started_at,
        status="ready" if status == "ready" else "failed",
        extra={
            "alias": alias,
            "machine": alias,
            "machine_ref": ref,
            "endpoint": endpoint,
        },
    )
    if status != "ready":
        envelope["status"] = status
    return envelope


def write_valid_machine_ready_run(
    machine: dict[str, Any],
    *,
    run_id: str = "machine-test-1",
    workflow_run_id: str = "wf-test-1",
    status: str = "ready",
    checks: list[dict[str, Any]] | None = None,
) -> Path:
    payload = build_machine_ready_run_payload(
        machine,
        run_id=run_id,
        workflow_run_id=workflow_run_id,
        status=status,
        checks=checks,
    )
    return write_run("machine-ready", run_id, payload, immutable=True)
