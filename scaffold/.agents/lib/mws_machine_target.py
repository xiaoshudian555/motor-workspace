#!/usr/bin/env python3
"""Machine-bound fixed remote workspace mapping."""

from __future__ import annotations

import uuid
from typing import Any

from mws_local_state import WorkspaceStateError, get_machine
from mws_result import CheckRunner, build_result_envelope
from mws_transport import RemoteTransport, shell_quote, validate_machine_transport_fields
from mws_validate import normalize_mount_root, require_safe_id, validate_remote_workspace_in_mount

DEFAULT_REMOTE_WORKSPACE_SUFFIX = "motor-workspace"
PARITY_TOOL_COMMANDS = ("tar", "mkdir", "git")

MACHINE_READY_REQUIRED_CHECKS = frozenset(
    {
        "ssh",
        "mount_root",
        "remote_workspace_path",
        "remote_workspace_root",
        "parity_tool:tar",
        "parity_tool:mkdir",
        "parity_tool:git",
        "shared_hostpath_root",
        "parity_backend",
    }
)


def check_item(
    name: str,
    *,
    status: str,
    evidence: str = "",
    message: str = "",
) -> dict[str, Any]:
    record: dict[str, Any] = {"name": name, "status": status}
    if evidence:
        record["evidence"] = evidence
    if message:
        record["message"] = message
    return record


def _verify_writable_directory(
    transport: RemoteTransport,
    directory: str,
    *,
    check_name: str,
) -> dict[str, Any]:
    verify_token = uuid.uuid4().hex[:12]
    verify_dir = f"{directory.rstrip('/')}/.mws-verify-{verify_token}"
    probe_file = f"{verify_dir}/write-test"
    try:
        transport.mkdir(verify_dir)
        payload = f"mws-verify-{verify_token}".encode()
        transport.upload_bytes(probe_file, payload)
        read_back = transport.read_bytes(probe_file)
        if read_back != payload:
            raise WorkspaceStateError("remote write verification failed: content mismatch")
        transport.run(f"rm -rf {shell_quote(verify_dir)}")
        if transport.run(f"test -d {shell_quote(verify_dir)}").returncode == 0:
            raise WorkspaceStateError("remote verify directory was not cleaned up")
        return check_item(check_name, status="ok", evidence=directory)
    except Exception as exc:  # noqa: BLE001
        transport.run(f"rm -rf {shell_quote(verify_dir)}")
        return check_item(check_name, status="error", message=str(exc))


def run_machine_ready_checks(
    machine: dict[str, Any],
    transport: RemoteTransport,
    *,
    profile_kube_context: str = "",
) -> dict[str, Any]:
    """Read-only machine-ready checks for remote development / parity substrate."""
    validate_machine_transport_fields(machine)
    runner = CheckRunner()
    alias = str(machine.get("alias") or machine.get("host") or "")

    try:
        ssh = transport.run("echo ok")
    except Exception as exc:  # noqa: BLE001
        runner.append(
            {
                "name": "ssh",
                "status": "unavailable",
                "message": str(exc),
            }
        )
        return _finalize_machine_checks(runner, machine, alias)

    if ssh.returncode == 0 and ssh.stdout.strip() == "ok":
        runner.append(
            check_item(
                "ssh",
                status="ok",
                evidence=f"{machine.get('user', 'root')}@{machine['host']}",
            )
        )
    else:
        msg = ssh.stderr.strip() or ssh.stdout.strip() or "SSH probe failed"
        runner.append({"name": "ssh", "status": "error", "message": msg})
        return _finalize_machine_checks(runner, machine, alias)

    mount_root = normalize_mount_root(machine.get("mount_root"))
    mount_check = _verify_writable_directory(transport, mount_root, check_name="mount_root")
    if not runner.append(mount_check):
        return _finalize_machine_checks(runner, machine, alias)

    workspace_root = remote_workspace_root(machine)
    try:
        validate_remote_workspace_in_mount(mount_root, workspace_root)
        if not runner.append(
            check_item(
                "remote_workspace_path",
                status="ok",
                evidence=workspace_root,
            )
        ):
            return _finalize_machine_checks(runner, machine, alias)
    except Exception as exc:  # noqa: BLE001
        runner.append(
            check_item("remote_workspace_path", status="error", message=str(exc))
        )
        return _finalize_machine_checks(runner, machine, alias)

    workspace_check = _verify_writable_directory(
        transport,
        workspace_root,
        check_name="remote_workspace_root",
    )
    if not runner.append(workspace_check):
        return _finalize_machine_checks(runner, machine, alias)

    for tool in PARITY_TOOL_COMMANDS:
        result = transport.run(f"command -v {shell_quote(tool)}")
        found = result.returncode == 0 and bool(result.stdout.strip())
        if not runner.append(
            check_item(
                f"parity_tool:{tool}",
                status="ok" if found else "error",
                evidence=result.stdout.strip() if found else "",
                message="" if found else f"{tool} not found in remote PATH",
            )
        ):
            return _finalize_machine_checks(runner, machine, alias)

    visible = transport.run(f"test -d {shell_quote(mount_root)}")
    if not runner.append(
        check_item(
            "shared_hostpath_root",
            status="ok" if visible.returncode == 0 else "error",
            evidence=mount_root,
            message=""
            if visible.returncode == 0
            else "shared mount root not visible on login host",
        )
    ):
        return _finalize_machine_checks(runner, machine, alias)

    machine_context = str(machine.get("kube_context") or "").strip()
    profile_context = str(profile_kube_context or "").strip()
    if machine_context and profile_context and machine_context != profile_context:
        runner.append(
            check_item(
                "kube_context_metadata",
                status="error",
                message=(
                    f"machine kube_context={machine_context!r} != profile context={profile_context!r}"
                ),
            )
        )
        return _finalize_machine_checks(runner, machine, alias)
    if machine_context and not profile_context:
        runner.append(
            check_item(
                "kube_context_metadata",
                status="warning",
                message="profile missing kube context; using machine inventory value",
                evidence=machine_context,
            )
        )
    elif machine_context or profile_context:
        runner.append(
            check_item(
                "kube_context_metadata",
                status="ok",
                evidence=machine_context or profile_context,
            )
        )

    backend = machine.get("parity_backend", "shared-hostpath")
    if backend == "node-local-hostpath":
        runner.append(
            check_item(
                "parity_backend",
                status="error",
                message="node-local-hostpath is not supported yet; use shared-hostpath",
            )
        )
        return _finalize_machine_checks(runner, machine, alias)

    runner.append(check_item("parity_backend", status="ok", evidence=backend))
    return _finalize_machine_checks(runner, machine, alias)


def _finalize_machine_checks(
    runner: CheckRunner,
    machine: dict[str, Any],
    alias: str,
) -> dict[str, Any]:
    ready = runner.stopped_at is None and not runner.errors
    return {
        "ready": ready,
        "alias": alias,
        "checks": runner.checks,
        "warnings": runner.warnings,
        "errors": runner.errors,
        "stopped_at": runner.stopped_at,
        "machine_ref": machine_ref(machine),
        "endpoint": endpoint_payload_for_machine(machine),
    }


def build_machine_result_envelope(
    *,
    run_id: str,
    workflow_run_id: str,
    payload: dict[str, Any],
    started_at: str,
) -> dict[str, Any]:
    return build_result_envelope(
        kind="machine-ready",
        run_id=run_id,
        workflow_run_id=workflow_run_id,
        checks=payload.get("checks", []),
        started_at=started_at,
        warnings=payload.get("warnings", []),
        errors=payload.get("errors", []),
        extra={
            "alias": payload.get("alias"),
            "machine": payload.get("alias"),
            "machine_ref": payload.get("machine_ref"),
            "endpoint": payload.get("endpoint"),
            "stopped_at": payload.get("stopped_at"),
        },
    )


def remote_workspace_root(machine: dict[str, Any]) -> str:
    mount = normalize_mount_root(machine.get("mount_root"))
    configured = machine.get("remote_workspace_root")
    if configured:
        root = str(configured).rstrip("/")
        if not root.startswith("/"):
            raise WorkspaceStateError("remote_workspace_root must be an absolute POSIX path")
        return root
    return f"{mount}/{DEFAULT_REMOTE_WORKSPACE_SUFFIX}"


def build_fixed_source_paths(machine: dict[str, Any]) -> dict[str, str]:
    mount = normalize_mount_root(machine.get("mount_root"))
    root = remote_workspace_root(machine)
    custom = machine.get("source_dirs") or {}
    return {
        "mount_root": mount,
        "remote_workspace_root": root,
        "motor_source": custom.get("motor") or f"{root}/motor",
        "vllm_source": custom.get("vllm") or f"{root}/vllm",
        "vllm_ascend_source": custom.get("vllm_ascend") or f"{root}/vllm-ascend",
        "python_overlay": custom.get("python_overlay") or f"{root}/python-overlay",
    }


def machine_ref(machine: dict[str, Any]) -> dict[str, Any]:
    alias = machine.get("alias") or machine.get("host")
    paths = build_fixed_source_paths(machine)
    return {
        "alias": alias,
        "host": machine.get("host"),
        "port": machine.get("port", 22),
        "user": machine.get("user", "root"),
        "mount_root": paths["mount_root"],
        "remote_workspace_root": paths["remote_workspace_root"],
        "source_dirs": {
            "motor": paths["motor_source"],
            "vllm": paths["vllm_source"],
            "vllm_ascend": paths["vllm_ascend_source"],
            "python_overlay": paths["python_overlay"],
        },
    }


def resolve_machine(alias: str) -> dict[str, Any]:
    normalized = require_safe_id(alias, label="machine")
    machine = get_machine(normalized)
    machine = dict(machine)
    machine.setdefault("alias", normalized)
    return machine


def endpoint_payload_for_machine(
    machine: dict[str, Any],
    *,
    root: str | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    paths = build_fixed_source_paths(machine)
    payload: dict[str, Any] = {
        "host": str(machine["host"]),
        "port": int(machine.get("port", 22)),
        "user": str(machine.get("user", "root")),
        "root": root or paths["remote_workspace_root"],
        "cwd": cwd or paths["remote_workspace_root"],
        "alias": machine.get("alias"),
        "source": {"machine_ref": machine_ref(machine)},
    }
    return payload


def machine_identity_matches(machine: dict[str, Any], machine_ref_payload: dict[str, Any]) -> bool:
    expected = machine_ref(machine)
    for key in ("alias", "host", "port", "user", "mount_root", "remote_workspace_root"):
        if str(expected.get(key, "")) != str(machine_ref_payload.get(key, "")):
            return False
    return True


def endpoint_matches_machine(machine: dict[str, Any], endpoint: dict[str, Any]) -> bool:
    expected = endpoint_payload_for_machine(machine)
    for key in ("host", "port", "user", "root", "cwd"):
        if str(expected.get(key, "")) != str(endpoint.get(key, "")):
            return False
    return True
