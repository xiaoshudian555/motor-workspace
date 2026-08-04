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


NODEPORT_DEFAULT_RANGE = (30000, 32767)


def run_environment_preflight_checks(
    *,
    machine: dict[str, Any],
    machine_ready: dict[str, Any],
    contract: dict[str, Any],
    deploy_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read-only cluster environment checks; no namespace or deploy inputs.

    `deploy_config` is the `motor_deploy_config` section of the Motor native
    user_config.json, which now exists before preflight in the 3+3 flow
    (motor-config-edit). preflight consumes only: `deploy_mode` (selects the
    workload-specific check set), `image_name` (image reference + per-node
    coverage probe), and `node_port_overrides` (target NodePort range/conflict
    validation). When None, only the base environment check set runs and the
    result records that no config was supplied.

    kubectl always runs on the machine host through the remote transport; the
    machine host's kubeconfig and selected context are authoritative.
    """
    runner = CheckRunner()
    machine_context = str(machine.get("kube_context") or "").strip()
    inventory_alias = str(machine.get("alias") or machine_ready.get("alias") or "")

    deploy_mode: str | None = None
    if deploy_config:
        raw_mode = deploy_config.get("deploy_mode")
        deploy_mode = str(raw_mode).strip() if raw_mode else None
    image_name = str((deploy_config or {}).get("image_name") or "").strip()
    node_port_overrides = _node_port_overrides_from_config(deploy_config)

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

    if not runner.stopped_at and deploy_config is not None:
        _run_image_checks(
            runner, kubectl, image_name=image_name, contract=contract
        )

    if not runner.stopped_at and deploy_config is not None:
        default_ports = (
            (contract.get("default_node_ports") or {}).get(deploy_mode or "") or []
        )
        node_port_overrides = _run_node_port_checks(
            runner,
            kubectl,
            overrides=node_port_overrides,
            port_range=contract.get("node_port_range", NODEPORT_DEFAULT_RANGE),
            default_ports=default_ports,
            auto_avoid=True,
        )

    return _finalize(
        runner,
        machine_context,
        contract,
        inventory_alias,
        deploy_mode,
        node_port_overrides=node_port_overrides,
    )


def _finalize(
    runner: CheckRunner,
    kube_context: str,
    contract: dict[str, Any],
    alias: str,
    deploy_mode: str | None = None,
    node_port_overrides: dict[int, int] | None = None,
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
        "node_port_overrides": node_port_overrides,
        "environment_contract": {
            "schema_version": contract.get("schema_version"),
            "name": contract.get("name"),
        },
    }


def _node_port_overrides_from_config(deploy_config: dict[str, Any] | None) -> dict[int, int]:
    """Extract the node_port_overrides map (template original -> replacement).

    The map is the config-driven source of truth for what the deploy requests.
    Returns {} when no overrides are declared. Entries must be positive ints.
    """
    if not deploy_config:
        return {}
    raw = deploy_config.get("node_port_overrides")
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise WorkspaceStateError(
            "motor_deploy_config.node_port_overrides must be an object"
        )
    overrides: dict[int, int] = {}
    for key, value in raw.items():
        try:
            old_port = int(key)
            new_port = int(value)
        except (TypeError, ValueError) as exc:
            raise WorkspaceStateError(
                f"motor_deploy_config.node_port_overrides keys/values must be integers: {key}={value}"
            ) from exc
        if old_port <= 0 or new_port <= 0:
            raise WorkspaceStateError(
                f"motor_deploy_config.node_port_overrides ports must be positive: {old_port}->{new_port}"
            )
        overrides[old_port] = new_port
    return overrides


def _run_image_checks(
    runner: CheckRunner,
    kubectl: Any,
    *,
    image_name: str,
    contract: dict[str, Any],
) -> None:
    """Validate the configured image reference and per-node coverage.

    kubectl-only preflight cannot prove pullability; it records the reference
    validity (error) and which schedulable nodes already run the image
    (warning + evidence when coverage is incomplete). Fail-closed pull
    verification stays with configure/deploy (TD-A3-04).
    """
    if not image_name:
        runner.append(
            {
                "name": "image_reference",
                "status": "error",
                "message": "motor_deploy_config.image_name is required",
            }
        )
        return

    if "/" not in image_name:
        runner.append(
            {
                "name": "image_reference",
                "status": "error",
                "message": (
                    f"motor_deploy_config.image_name {image_name!r} has no registry "
                    "or repository path; use a full image reference"
                ),
            }
        )
        return

    runner.append(
        {
            "name": "image_reference",
            "status": "ok",
            "message": "image reference parsed",
            "evidence": image_name,
        }
    )

    nodes = kubectl(
        "get",
        "nodes",
        "-o",
        "json",
    )
    if nodes.returncode != 0:
        runner.append(
            {
                "name": "image_node_coverage",
                "status": "unavailable",
                "message": "could not list nodes for image coverage probe",
                "evidence": nodes.stderr.strip(),
            }
        )
        return
    try:
        schedulable = _parse_schedulable_nodes(nodes.stdout)
    except (ValueError, TypeError) as exc:
        runner.append(
            {
                "name": "image_node_coverage",
                "status": "unavailable",
                "message": f"could not parse node list: {exc}",
            }
        )
        return

    pods = kubectl("get", "pods", "-A", "-o", "json")
    if pods.returncode != 0:
        runner.append(
            {
                "name": "image_node_coverage",
                "status": "unavailable",
                "message": "could not list pods for image coverage probe",
                "evidence": pods.stderr.strip(),
            }
        )
        return
    try:
        node_images = _parse_node_image_map(pods.stdout)
    except (ValueError, TypeError) as exc:
        runner.append(
            {
                "name": "image_node_coverage",
                "status": "unavailable",
                "message": f"could not parse pod list: {exc}",
            }
        )
        return

    wanted = _image_short_key(image_name)
    missing = [
        node
        for node in schedulable
        if not any(_image_short_key(img) == wanted for img in node_images.get(node, set()))
    ]
    if not missing:
        runner.append(
            {
                "name": "image_node_coverage",
                "status": "ok",
                "message": f"image present on all {len(schedulable)} schedulable nodes",
                "evidence": ",".join(schedulable),
            }
        )
    else:
        present = sorted(set(schedulable) - set(missing))
        runner.append(
            {
                "name": "image_node_coverage",
                "status": "warning",
                "message": (
                    f"image {image_name!r} not observed on {len(missing)} of "
                    f"{len(schedulable)} schedulable nodes; verify pullability before apply "
                    "(ErrImagePull risk)"
                ),
                "evidence": (
                    f"missing={','.join(sorted(missing))}"
                    + (f";present={','.join(present)}" if present else "")
                ),
            }
        )


def _parse_schedulable_nodes(stdout: str) -> list[str]:
    data = json.loads(stdout)
    nodes = []
    for item in data.get("items", []):
        if item.get("spec", {}).get("unschedulable"):
            continue
        nodes.append(item.get("metadata", {}).get("name") or "")
    return [name for name in nodes if name]


def _parse_node_image_map(stdout: str) -> dict[str, set[str]]:
    data = json.loads(stdout)
    mapping: dict[str, set[str]] = {}
    for item in data.get("items", []):
        node = item.get("spec", {}).get("nodeName") or ""
        if not node:
            continue
        images = mapping.setdefault(node, set())
        for container in item.get("spec", {}).get("containers", []) or []:
            image = container.get("image")
            if image:
                images.add(str(image))
        for container in item.get("spec", {}).get("initContainers", []) or []:
            image = container.get("image")
            if image:
                images.add(str(image))
    return mapping


def _image_short_key(ref: str) -> str:
    """Compare key: last '/'-separated segment (name[:tag]), ignoring registry."""
    return (str(ref).split("@")[0].rsplit("/", 1)[-1] or "").strip()


def _run_node_port_checks(
    runner: CheckRunner,
    kubectl: Any,
    *,
    overrides: dict[int, int],
    port_range: Any,
    default_ports: list[int] | None = None,
    auto_avoid: bool = True,
) -> dict[int, int]:
    """Validate configured NodePort targets and auto-avoid cluster conflicts.

    NodePorts are cluster-wide (not namespace-scoped), so usage is collected
    from all Services across namespaces. `overrides` is the declared
    template-original -> replacement map; when empty, the contract's template
    default ports for the active deploy_mode are probed instead (default
    override always on). When a target is occupied and `auto_avoid` is set,
    preflight assigns a free port, records the change, and returns the updated
    map so the caller can write it back into the config; without auto_avoid a
    conflict fails closed. Returns the effective map (empty when nothing needs
    to be written back).
    """
    if not overrides and not default_ports:
        runner.append(
            {
                "name": "node_port_conflict",
                "status": "warning",
                "message": (
                    "no node_port_overrides and no default NodePorts for this "
                    "deploy_mode in the contract; nothing to validate"
                ),
            }
        )
        return {}

    targets = list(overrides.values()) if overrides else list(default_ports or [])

    lo, hi = _normalize_port_range(port_range)
    out_of_range = [p for p in targets if p < lo or p > hi]
    if out_of_range:
        runner.append(
            {
                "name": "node_port_range",
                "status": "error",
                "message": f"NodePort {out_of_range} outside legal range [{lo}, {hi}]",
                "evidence": ",".join(str(p) for p in out_of_range),
            }
        )
        return overrides
    runner.append(
        {
            "name": "node_port_range",
            "status": "ok",
            "message": f"target NodePorts within [{lo}, {hi}]",
            "evidence": ",".join(str(p) for p in targets),
        }
    )

    duplicates = {p for p in targets if targets.count(p) > 1}
    if duplicates:
        runner.append(
            {
                "name": "node_port_unique",
                "status": "error",
                "message": f"duplicate target NodePorts in config: {sorted(duplicates)}",
            }
        )
        return overrides
    runner.append(
        {
            "name": "node_port_unique",
            "status": "ok",
            "message": "target NodePorts are unique within config",
        }
    )

    services = kubectl("get", "services", "-A", "-o", "json")
    if services.returncode != 0:
        runner.append(
            {
                "name": "node_port_conflict",
                "status": "unavailable",
                "message": "could not list cluster services for NodePort probe",
                "evidence": services.stderr.strip(),
            }
        )
        return overrides
    try:
        usage = _parse_service_node_ports(services.stdout)
    except (ValueError, TypeError) as exc:
        runner.append(
            {
                "name": "node_port_conflict",
                "status": "unavailable",
                "message": f"could not parse services list: {exc}",
            }
        )
        return overrides

    conflicts = [p for p in targets if p in usage]
    if conflicts:
        if auto_avoid:
            if overrides:
                adjusted = _assign_free_ports(
                    overrides, used=set(usage), lo=lo, hi=hi
                )
            else:
                identity = {p: p for p in targets}
                adjusted = _assign_free_ports(
                    identity, used=set(usage), lo=lo, hi=hi
                )
                if adjusted is not None:
                    adjusted = {o: n for o, n in adjusted.items() if n != o}
            if adjusted:
                changed = {
                    old: new
                    for old, new in sorted(adjusted.items())
                    if overrides.get(old, old) != new
                }
                evidence = ",".join(
                    f"{old}->{new}" for old, new in sorted(changed.items())
                ) or "none"
                runner.append(
                    {
                        "name": "node_port_conflict",
                        "status": "ok",
                        "message": (
                            f"auto-avoided occupied NodePorts {sorted(conflicts)}; "
                            "wrote updated node_port_overrides to config"
                        ),
                        "evidence": evidence,
                    }
                )
                return adjusted
            runner.append(
                {
                    "name": "node_port_conflict",
                    "status": "error",
                    "message": (
                        f"no free NodePort in [{lo}, {hi}] after {len(conflicts)} "
                        f"conflicts; enlarge the range or free ports"
                    ),
                }
            )
            return overrides
        occupied_by = [
            f"{p}->{','.join(sorted(usage[p]))}" for p in sorted(conflicts)
        ]
        suggestion = _suggest_free_port(lo, hi, set(targets) | set(usage))
        runner.append(
            {
                "name": "node_port_conflict",
                "status": "error",
                "message": (
                    f"NodePort conflict: {occupied_by} already used cluster-wide; "
                    f"pick a free port (e.g. {suggestion}) and update "
                    "motor_deploy_config.node_port_overrides"
                ),
                "evidence": ",".join(str(p) for p in sorted(conflicts)),
            }
        )
        return overrides

    if not overrides:
        runner.append(
            {
                "name": "node_port_conflict",
                "status": "ok",
                "message": (
                    f"template default NodePorts {targets} free cluster-wide; "
                    "no overrides needed"
                ),
            }
        )
        return {}
    runner.append(
        {
            "name": "node_port_conflict",
            "status": "ok",
            "message": "target NodePorts free cluster-wide",
        }
    )
    return overrides


def _assign_free_ports(
    overrides: dict[int, int],
    *,
    used: set[int],
    lo: int,
    hi: int,
) -> dict[int, int] | None:
    """Return an override map where every target is free; None if impossible.

    Preserves the template original -> replacement keys, only replacing values
    that collide with the cluster `used` set. Allocates greedily from `lo`
    upward, never picking a port already taken by the cluster or by another
    entry in the same map (a NodePort must stay unique cluster-wide).
    """
    result: dict[int, int] = {}
    busy = set(used)
    for old, new in overrides.items():
        if new in busy:
            replacement = _suggest_free_port(lo, hi, busy)
            if replacement is None:
                return None
            result[old] = replacement
            busy.add(replacement)
        else:
            result[old] = new
            busy.add(new)
    return result


def _normalize_port_range(port_range: Any) -> tuple[int, int]:
    try:
        lo, hi = int(port_range[0]), int(port_range[1])
    except (TypeError, ValueError, IndexError):
        return NODEPORT_DEFAULT_RANGE
    if lo <= 0 or hi <= 0 or lo >= hi:
        return NODEPORT_DEFAULT_RANGE
    return lo, hi


def _parse_service_node_ports(stdout: str) -> dict[int, set[str]]:
    data = json.loads(stdout)
    usage: dict[int, set[str]] = {}
    for item in data.get("items", []):
        ns = item.get("metadata", {}).get("namespace", "")
        name = item.get("metadata", {}).get("name", "")
        for port in item.get("spec", {}).get("ports", []) or []:
            node_port = port.get("nodePort")
            if isinstance(node_port, int) and node_port > 0:
                usage.setdefault(node_port, set()).add(f"{ns}/{name}")
    return usage


def _suggest_free_port(lo: int, hi: int, used: set[int]) -> int | None:
    for port in range(lo, hi + 1):
        if port not in used:
            return port
    return None


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
