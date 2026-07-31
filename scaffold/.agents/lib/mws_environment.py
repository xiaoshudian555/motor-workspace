#!/usr/bin/env python3
"""K8s / MindCluster environment preflight checks (3+3 part-2 step 1)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from mws_local_state import WorkspaceStateError
from mws_result import CheckRunner, build_result_envelope, utc_now_iso
from repo_paths import SCAFFOLD_ROOT


def load_environment_contract(path: Path | None = None) -> dict[str, Any]:
    contract_path = path or (
        SCAFFOLD_ROOT
        / ".agents/skills/motor-deploy-preflight/references/environment-contract.yaml"
    )
    if not contract_path.exists():
        raise WorkspaceStateError(f"environment contract not found: {contract_path}")
    text = contract_path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise WorkspaceStateError(
                f"{contract_path} is YAML; install PyYAML"
            ) from exc
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise WorkspaceStateError(f"{contract_path} must contain an object")
    return data


def kubectl_base(*, kube_context: str = "") -> list[str]:
    args = ["kubectl"]
    context = str(kube_context or "").strip()
    if context:
        args.extend(["--context", context])
    return args


def _run_kubectl(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=False, text=True, capture_output=True)


def run_environment_preflight_checks(
    *,
    machine: dict[str, Any],
    machine_ready: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Read-only cluster environment checks; no namespace or deploy inputs."""
    runner = CheckRunner()
    machine_context = str(machine.get("kube_context") or "").strip()
    inventory_alias = str(machine.get("alias") or machine_ready.get("alias") or "")

    if not shutil.which("kubectl"):
        runner.append(
            {
                "name": "kubectl",
                "status": "error",
                "message": "kubectl not found in PATH",
            }
        )
        return _finalize(runner, machine_context, contract, inventory_alias)

    runner.append(
        {
            "name": "kubectl",
            "status": "ok",
            "message": "kubectl available",
            "evidence": shutil.which("kubectl"),
        }
    )

    if not machine_context:
        runner.append(
            {
                "name": "kube_context",
                "status": "error",
                "message": "machine inventory missing kube_context",
            }
        )
        return _finalize(runner, machine_context, contract, inventory_alias)

    runner.append(
        {
            "name": "kube_context",
            "status": "ok",
            "message": "kube context resolved from machine inventory",
            "evidence": machine_context,
        }
    )

    kubectl = kubectl_base(kube_context=machine_context)
    cluster_info = _run_kubectl([*kubectl, "cluster-info"])
    if cluster_info.returncode != 0:
        runner.append(
            {
                "name": "kubernetes_api",
                "status": "unavailable",
                "message": cluster_info.stderr.strip() or "Kubernetes API unreachable",
            }
        )
        return _finalize(runner, machine_context, contract, inventory_alias)

    runner.append(
        {
            "name": "kubernetes_api",
            "status": "ok",
            "message": "Kubernetes API reachable",
            "evidence": cluster_info.stdout.strip().splitlines()[0][:200],
        }
    )

    version = _run_kubectl([*kubectl, "version", "--output=json"])
    if version.returncode != 0:
        runner.append(
            {
                "name": "cluster_version",
                "status": "warning",
                "message": "could not read cluster version",
                "evidence": version.stderr.strip(),
            }
        )
    else:
        runner.append(
            {
                "name": "cluster_version",
                "status": "ok",
                "message": "cluster version available",
                "evidence": version.stdout.strip()[:400],
            }
        )

    auth = _run_kubectl([*kubectl, "auth", "can-i", "list", "customresourcedefinitions"])
    auth_ok = auth.stdout.strip().lower() == "yes"
    if not auth_ok:
        runner.append(
            {
                "name": "cluster_read_permissions",
                "status": "error",
                "message": "insufficient permissions to list cluster CRDs",
                "evidence": auth.stdout.strip() or auth.stderr.strip(),
            }
        )
        return _finalize(runner, machine_context, contract, inventory_alias)
    runner.append(
        {
            "name": "cluster_read_permissions",
            "status": "ok",
            "message": "can list cluster CRDs",
        }
    )

    for resource in contract.get("required_api_resources", []):
        if not isinstance(resource, dict):
            continue
        name = str(resource.get("name", "")).strip()
        api_group = str(resource.get("api_group", "")).strip()
        if not name or not api_group:
            if not runner.append(
                {
                    "name": f"api_resource:{name or 'unknown'}",
                    "status": "error",
                    "message": "invalid environment contract api resource entry",
                }
            ):
                break
            continue
        cmd = [*kubectl, "api-resources", f"--api-group={api_group}", "-o", "name"]
        api = _run_kubectl(cmd)
        found = name in api.stdout
        if not runner.append(
            {
                "name": f"api_resource:{name}",
                "status": "ok" if found else "error",
                "message": f"{name} present" if found else f"{name} missing in api-group {api_group}",
                "evidence": api.stdout.strip()[:200],
            }
        ):
            break

    if not runner.stopped_at:
        for pattern in contract.get("component_patterns", []):
            pattern = str(pattern).strip()
            if not pattern:
                continue
            cmd = [
                *kubectl,
                "get",
                "pods",
                "-A",
                "-o",
                "jsonpath={range .items[*]}{.metadata.name}{'\\n'}{end}",
            ]
            pods = _run_kubectl(cmd)
            if pods.returncode != 0:
                if not runner.append(
                    {
                        "name": f"controller:{pattern}",
                        "status": "unavailable",
                        "message": "could not list cluster pods for controller probe",
                        "evidence": pods.stderr.strip(),
                    }
                ):
                    break
                continue
            matched = any(pattern in line for line in pods.stdout.splitlines())
            if not runner.append(
                {
                    "name": f"controller:{pattern}",
                    "status": "ok" if matched else "error",
                    "message": f"controller pattern {pattern!r} {'found' if matched else 'missing'}",
                }
            ):
                break

    if not runner.stopped_at:
        resource_name = str(contract.get("npu_resource_name", "")).strip()
        if resource_name:
            cmd = [
                *kubectl,
                "get",
                "nodes",
                "-o",
                "jsonpath={range .items[*]}{.status.allocatable}{'\\n'}{end}",
            ]
            nodes = _run_kubectl(cmd)
            if nodes.returncode != 0:
                runner.append(
                    {
                        "name": "npu_resource_type",
                        "status": "unavailable",
                        "message": "could not read node allocatable resources",
                        "evidence": nodes.stderr.strip(),
                    }
                )
            elif resource_name in nodes.stdout:
                runner.append(
                    {
                        "name": "npu_resource_type",
                        "status": "ok",
                        "message": f"NPU resource {resource_name!r} advertised by cluster",
                    }
                )
            else:
                runner.append(
                    {
                        "name": "npu_resource_type",
                        "status": "error",
                        "message": f"NPU resource {resource_name!r} not found on any node",
                    }
                )

    return _finalize(runner, machine_context, contract, inventory_alias)


def _finalize(
    runner: CheckRunner,
    kube_context: str,
    contract: dict[str, Any],
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
        "kube_context": kube_context,
        "environment_contract": {
            "schema_version": contract.get("schema_version"),
            "name": contract.get("name"),
        },
    }


def build_environment_result_envelope(
    *,
    run_id: str,
    workflow_run_id: str,
    machine_run_id: str,
    payload: dict[str, Any],
    started_at: str,
) -> dict[str, Any]:
    return build_result_envelope(
        kind="deploy-environment-ready",
        run_id=run_id,
        workflow_run_id=workflow_run_id,
        checks=payload.get("checks", []),
        started_at=started_at,
        upstream_refs=[
            {"kind": "machine-ready", "run_id": machine_run_id},
        ],
        warnings=payload.get("warnings", []),
        errors=payload.get("errors", []),
        extra={
            "alias": payload.get("alias"),
            "kube_context": payload.get("kube_context"),
            "environment_contract": payload.get("environment_contract"),
            "stopped_at": payload.get("stopped_at"),
        },
    )
