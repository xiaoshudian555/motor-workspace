#!/usr/bin/env python3
"""Machine-bound fixed remote workspace mapping."""

from __future__ import annotations

import uuid
from typing import Any

from mws_local_state import WorkspaceStateError, get_machine
from mws_transport import RemoteTransport, shell_quote, validate_machine_transport_fields
from mws_validate import normalize_mount_root, require_safe_id, validate_remote_workspace_in_mount

DEFAULT_REMOTE_WORKSPACE_SUFFIX = "motor-workspace"
PARITY_TOOL_COMMANDS = ("tar", "mkdir")


def check_item(
    name: str,
    *,
    status: str,
    evidence: str = "",
    error: str = "",
) -> dict[str, Any]:
    return {"name": name, "status": status, "evidence": evidence, "error": error}


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
        return check_item(check_name, status="pass", evidence=directory)
    except Exception as exc:  # noqa: BLE001
        transport.run(f"rm -rf {shell_quote(verify_dir)}")
        return check_item(check_name, status="fail", error=str(exc))


def run_machine_ready_checks(
    machine: dict[str, Any],
    transport: RemoteTransport,
    *,
    profile_kube_context: str = "",
) -> dict[str, Any]:
    """Read-only machine-ready checks for remote development / parity substrate."""
    validate_machine_transport_fields(machine)
    checks: list[dict[str, Any]] = []
    errors: list[str] = []

    ssh = transport.run("echo ok")
    if ssh.returncode == 0 and ssh.stdout.strip() == "ok":
        checks.append(
            check_item(
                "ssh",
                status="pass",
                evidence=f"{machine.get('user', 'root')}@{machine['host']}",
            )
        )
    else:
        msg = ssh.stderr.strip() or ssh.stdout.strip() or "SSH probe failed"
        checks.append(check_item("ssh", status="fail", error=msg))
        errors.append(msg)
        return {
            "ready": False,
            "checks": checks,
            "errors": errors,
            "machine_ref": machine_ref(machine),
            "endpoint": endpoint_payload_for_machine(machine),
        }

    mount_root = normalize_mount_root(machine.get("mount_root"))
    mount_check = _verify_writable_directory(transport, mount_root, check_name="mount_root")
    checks.append(mount_check)
    if mount_check["status"] == "fail":
        errors.append(mount_check.get("error") or "mount_root not writable")

    workspace_root = remote_workspace_root(machine)
    try:
        validate_remote_workspace_in_mount(mount_root, workspace_root)
        checks.append(
            check_item(
                "remote_workspace_path",
                status="pass",
                evidence=workspace_root,
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(check_item("remote_workspace_path", status="fail", error=str(exc)))
        errors.append(str(exc))
        workspace_root = ""

    if workspace_root:
        workspace_check = _verify_writable_directory(
            transport,
            workspace_root,
            check_name="remote_workspace_root",
        )
        checks.append(workspace_check)
        if workspace_check["status"] == "fail":
            errors.append(workspace_check.get("error") or "remote_workspace_root not writable")

    for tool in PARITY_TOOL_COMMANDS:
        result = transport.run(f"command -v {shell_quote(tool)}")
        found = result.returncode == 0 and bool(result.stdout.strip())
        checks.append(
            check_item(
                f"parity_tool:{tool}",
                status="pass" if found else "fail",
                evidence=result.stdout.strip(),
                error="" if found else f"{tool} not found in remote PATH",
            )
        )
        if not found:
            errors.append(f"{tool} not found in remote PATH")

    visible = transport.run(f"test -d {shell_quote(mount_root)}")
    checks.append(
        check_item(
            "shared_hostpath_root",
            status="pass" if visible.returncode == 0 else "fail",
            evidence=mount_root,
            error="" if visible.returncode == 0 else "shared mount root not visible on login host",
        )
    )
    if visible.returncode:
        errors.append("shared mount root not visible on login host")

    machine_context = str(machine.get("kube_context") or "").strip()
    profile_context = str(profile_kube_context or "").strip()
    if machine_context and profile_context and machine_context != profile_context:
        checks.append(
            check_item(
                "kube_context_metadata",
                status="fail",
                error=(
                    f"machine kube_context={machine_context!r} != profile context={profile_context!r}"
                ),
            )
        )
        errors.append("kube context mismatch between machine inventory and profile")
    elif machine_context or profile_context:
        checks.append(
            check_item(
                "kube_context_metadata",
                status="pass",
                evidence=machine_context or profile_context,
            )
        )
    else:
        checks.append(
            check_item(
                "kube_context_metadata",
                status="not_applicable",
                evidence="no kube context recorded",
            )
        )

    backend = machine.get("parity_backend", "shared-hostpath")
    if backend == "node-local-hostpath":
        checks.append(
            check_item(
                "parity_backend",
                status="fail",
                error="node-local-hostpath is not supported yet; use shared-hostpath",
            )
        )
        errors.append("unsupported parity_backend")
    else:
        checks.append(check_item("parity_backend", status="pass", evidence=backend))

    ready = not errors
    return {
        "ready": ready,
        "checks": checks,
        "errors": errors,
        "machine_ref": machine_ref(machine),
        "endpoint": endpoint_payload_for_machine(machine),
    }


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
    return {
        "mount_root": mount,
        "remote_workspace_root": root,
        "motor_source": f"{root}/motor",
        "vllm_source": f"{root}/vllm",
        "vllm_ascend_source": f"{root}/vllm-ascend",
        "python_overlay": f"{root}/python-overlay",
    }


def pythonpath_for_machine(machine: dict[str, Any]) -> str:
    paths = build_fixed_source_paths(machine)
    return ":".join(
        [
            paths["motor_source"],
            paths["vllm_source"],
            paths["vllm_ascend_source"],
            paths["python_overlay"],
        ]
    )


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
        "pythonpath": pythonpath_for_machine(machine),
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
