#!/usr/bin/env python3
"""K8s / MindCluster environment preflight checks (3+3 part-2 step 2)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from mws_kubectl import build_kubectl_runner, kubectl_available
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


def run_environment_preflight_checks(
    *,
    machine: dict[str, Any],
    machine_ready: dict[str, Any],
    contract: dict[str, Any],
    deploy_mode: str | None = None,
) -> dict[str, Any]:
    """Read-only cluster environment checks; no namespace or deploy inputs.

    `deploy_mode` (from the Motor native user_config.json, which now exists
    before preflight in the 3+3 flow) selects the workload-specific check set
    from the contract: infer_service_set requires a Motor workload API
    (InferServiceSet or AscendJob) and a Motor operator; multi_deployment and
    single_container only need the base components. When None, only the base
    check set runs and the result records that no deploy_mode was supplied.

    kubectl always runs on the machine host through the remote transport; the
    machine host's kubeconfig and selected context are authoritative.
    """
    runner = CheckRunner()
    machine_context = str(machine.get("kube_context") or "").strip()
    inventory_alias = str(machine.get("alias") or machine_ready.get("alias") or "")

    if not machine_context:
        runner.append(
            {
                "name": "kube_context",
                "status": "error",
                "message": "machine inventory missing kube_context",
            }
        )
        return _finalize(runner, machine_context, contract, inventory_alias, deploy_mode)

    runner.append(
        {
            "name": "kube_context",
            "status": "ok",
            "message": "kube context resolved from machine inventory",
            "evidence": machine_context,
        }
    )

    runner.append(
        {
            "name": "deploy_mode",
            "status": "ok" if deploy_mode else "warning",
            "message": (
                f"deploy_mode {deploy_mode!r} selected workload check set"
                if deploy_mode
                else "no deploy_mode supplied; base environment check only"
            ),
            "evidence": deploy_mode or "",
        }
    )

    kubectl = build_kubectl_runner(machine, kube_context=machine_context)
    available, evidence = kubectl_available(machine, kube_context=machine_context)
    if not available:
        runner.append(
            {
                "name": "kubectl",
                "status": "error",
                "message": evidence,
            }
        )
        return _finalize(runner, machine_context, contract, inventory_alias, deploy_mode)
    runner.append(
        {
            "name": "kubectl",
            "status": "ok",
            "message": "kubectl available",
            "evidence": evidence,
        }
    )

    cluster_info = kubectl("cluster-info")
    if cluster_info.returncode != 0:
        runner.append(
            {
                "name": "kubernetes_api",
                "status": "unavailable",
                "message": cluster_info.stderr.strip() or "Kubernetes API unreachable",
            }
        )
        return _finalize(runner, machine_context, contract, inventory_alias, deploy_mode)

    runner.append(
        {
            "name": "kubernetes_api",
            "status": "ok",
            "message": "Kubernetes API reachable",
            "evidence": cluster_info.stdout.strip().splitlines()[0][:200],
        }
    )

    version = kubectl("version", "--output=json")
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

    auth = kubectl("auth", "can-i", "list", "customresourcedefinitions")
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
        return _finalize(runner, machine_context, contract, inventory_alias, deploy_mode)
    runner.append(
        {
            "name": "cluster_read_permissions",
            "status": "ok",
            "message": "can list cluster CRDs",
        }
    )

    def _api_resource_stdout(name: str, api_group: str) -> str:
        api = kubectl("api-resources", f"--api-group={api_group}", "-o", "name")
        return api.stdout or ""

    api_resources = list(contract.get("required_api_resources", []))
    api_resources += (contract.get("deploy_mode_api_resources", {}) or {}).get(deploy_mode) or []

    for resource in api_resources:
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
        stdout = _api_resource_stdout(name, api_group)
        found = name in stdout
        if not runner.append(
            {
                "name": f"api_resource:{name}",
                "status": "ok" if found else "error",
                "message": f"{name} present" if found else f"{name} missing in api-group {api_group}",
                "evidence": stdout.strip()[:200],
            }
        ):
            break

    if not runner.stopped_at:
        for group in (contract.get("deploy_mode_api_resource_groups", {}) or {}).get(deploy_mode) or []:
            alternatives = group.get("alternatives", []) if isinstance(group, dict) else []
            hit = ""
            for alt in alternatives:
                if not isinstance(alt, dict):
                    continue
                stdout = _api_resource_stdout(str(alt.get("name", "")), str(alt.get("api_group", "")))
                if str(alt.get("name", "")) in stdout:
                    hit = str(alt.get("name", ""))
                    break
            names = [str(a.get("name", "")) for a in alternatives if isinstance(a, dict)]
            if not runner.append(
                {
                    "name": f"api_resource_group:{group.get('name', 'unknown')}",
                    "status": "ok" if hit else "error",
                    "message": (
                        f"group {group.get('name')} satisfied by {hit}"
                        if hit
                        else f"group {group.get('name')} missing; none of {names} found"
                    ),
                    "evidence": hit,
                }
            ):
                break

    if not runner.stopped_at:
        component_patterns = list(contract.get("component_patterns", []))
        component_patterns += (contract.get("deploy_mode_components", {}) or {}).get(deploy_mode) or []
        for pattern in component_patterns:
            pattern = str(pattern).strip()
            if not pattern:
                continue
            pods = kubectl(
                "get",
                "pods",
                "-A",
                "-o",
                "jsonpath={range .items[*]}{.metadata.name}{'\\n'}{end}",
            )
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
        for group in (contract.get("deploy_mode_component_groups", {}) or {}).get(deploy_mode) or []:
            alternatives = group.get("alternatives", []) if isinstance(group, dict) else []
            if not alternatives:
                continue
            pods = kubectl(
                "get",
                "pods",
                "-A",
                "-o",
                "jsonpath={range .items[*]}{.metadata.name}{'\\n'}{end}",
            )
            if pods.returncode != 0:
                if not runner.append(
                    {
                        "name": f"controller_group:{group.get('name', 'unknown')}",
                        "status": "unavailable",
                        "message": "could not list cluster pods for controller probe",
                        "evidence": pods.stderr.strip(),
                    }
                ):
                    break
                continue
            hit = next(
                (str(a) for a in alternatives if any(str(a) in line for line in pods.stdout.splitlines())),
                "",
            )
            if not runner.append(
                {
                    "name": f"controller_group:{group.get('name', 'unknown')}",
                    "status": "ok" if hit else "error",
                    "message": (
                        f"group {group.get('name')} satisfied by {hit}"
                        if hit
                        else f"group {group.get('name')} missing; none of {[str(a) for a in alternatives]} found"
                    ),
                }
            ):
                break

    if not runner.stopped_at:
        resource_name = str(contract.get("npu_resource_name", "")).strip()
        if resource_name:
            nodes = kubectl(
                "get",
                "nodes",
                "-o",
                "jsonpath={range .items[*]}{.status.allocatable}{'\\n'}{end}",
            )
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

    return _finalize(runner, machine_context, contract, inventory_alias, deploy_mode)


def _finalize(
    runner: CheckRunner,
    kube_context: str,
    contract: dict[str, Any],
    alias: str,
    deploy_mode: str | None = None,
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
        "deploy_mode": deploy_mode,
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
            "deploy_mode": payload.get("deploy_mode"),
            "environment_contract": payload.get("environment_contract"),
            "stopped_at": payload.get("stopped_at"),
        },
    )
