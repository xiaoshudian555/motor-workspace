#!/usr/bin/env python3
"""K8s / MindCluster environment preflight checks (3+3 part-2 step 1).

Migrated from legacy ``machine_verify.py`` so machine-management stays limited
to SSH / shared-mount / parity substrate readiness.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from mws_deploy import kubectl_base, load_profile, pod_readiness_probe
from mws_machine_target import check_item

API_RESOURCE_GROUPS = {
    "ascendjobs": "mindx.huawei.com",
    "podgroups": "scheduling.volcano.sh",
}


def run_environment_preflight_checks(
    *,
    machine: dict[str, Any],
    profile: dict[str, Any],
    include_pod_readiness: bool = True,
) -> dict[str, Any]:
    """Read-only K8s/MindCluster environment checks for ``motor-deploy-preflight``."""
    checks: list[dict[str, Any]] = []
    errors: list[str] = []

    machine_context = str(machine.get("kube_context") or "").strip()
    profile_context = str(profile.get("kubernetes", {}).get("context", "") or "").strip()
    effective_context = machine_context or profile_context

    if machine_context and profile_context and machine_context != profile_context:
        checks.append(
            check_item(
                "kube_context_consistency",
                status="fail",
                error=(
                    f"machine kube_context={machine_context!r} != profile context={profile_context!r}"
                ),
            )
        )
        errors.append("kube context mismatch between machine inventory and profile")
    else:
        checks.append(
            check_item(
                "kube_context_consistency",
                status="pass" if effective_context else "not_applicable",
                evidence=effective_context or "default context",
            )
        )

    namespace = str(profile.get("kubernetes", {}).get("namespace", "") or "").strip()
    if not namespace:
        checks.append(
            check_item(
                "namespace_configured",
                status="fail",
                error="deploy profile missing kubernetes.namespace",
            )
        )
        errors.append("deploy profile missing kubernetes.namespace")
        return {"ready": False, "checks": checks, "errors": errors}

    if not shutil.which("kubectl"):
        checks.append(
            check_item("kubectl", status="fail", error="kubectl not found in PATH")
        )
        errors.append("kubectl not found in PATH")
        return {"ready": False, "checks": checks, "errors": errors}

    checks.append(check_item("kubectl", status="pass", evidence=shutil.which("kubectl") or ""))

    kubectl = kubectl_base(profile)
    auth_cmd = [*kubectl, "auth", "can-i", "get", "pods", "-n", namespace]
    auth = subprocess.run(auth_cmd, check=False, text=True, capture_output=True)
    auth_ok = auth.stdout.strip().lower() == "yes"
    checks.append(
        check_item(
            "namespace_auth",
            status="pass" if auth_ok else "fail",
            evidence=auth.stdout.strip(),
            error="" if auth_ok else f"cannot get pods in namespace {namespace}",
        )
    )
    if not auth_ok:
        errors.append(f"cannot get pods in namespace {namespace}")

    for resource in profile.get("mindcluster", {}).get("required_api_resources", []):
        api_group = API_RESOURCE_GROUPS.get(resource)
        if not api_group:
            checks.append(
                check_item(
                    f"api_resource:{resource}",
                    status="not_applicable",
                    error=f"unknown api group mapping for {resource}",
                )
            )
            continue
        cmd = [*kubectl, "api-resources", f"--api-group={api_group}", "-o", "name"]
        api = subprocess.run(cmd, check=False, text=True, capture_output=True)
        found = resource in api.stdout
        checks.append(
            check_item(
                f"api_resource:{resource}",
                status="pass" if found else "fail",
                evidence=api.stdout.strip()[:200],
                error="" if found else f"{resource} not found in api-group {api_group}",
            )
        )
        if not found:
            errors.append(f"{resource} not found in api-group {api_group}")

    if include_pod_readiness:
        pods = pod_readiness_probe(profile, namespace)
        checks.append(
            check_item(
                "pod_readiness",
                status="pass" if pods.get("ready") else "fail",
                evidence=json.dumps(pods),
                error="" if pods.get("ready") else "not all pods ready in namespace",
            )
        )
        if not pods.get("ready"):
            errors.append("not all pods ready in namespace")

    ready = not errors
    return {
        "ready": ready,
        "checks": checks,
        "errors": errors,
        "namespace": namespace,
        "kube_context": effective_context,
    }


def load_profile_from_path(profile_path: str | None, default: str = "profiles/a2-dev.yaml") -> dict[str, Any]:
    from pathlib import Path

    from repo_paths import SCAFFOLD_ROOT

    path = SCAFFOLD_ROOT / (profile_path or default)
    if not path.exists():
        return {}
    return load_profile(path)
