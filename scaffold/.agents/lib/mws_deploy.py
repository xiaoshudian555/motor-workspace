#!/usr/bin/env python3
"""Motor deployer thin-wrapper helpers."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

from mws_kubectl import KubectlRunner, build_kubectl_runner, stage_remote_files
from mws_local_state import WorkspaceStateError
from repo_paths import MOTOR_ROOT, REPO_ROOT
from mws_lock import resolve_base_image_ref
from mws_machine_target import build_fixed_source_paths, machine_ref, pythonpath_for_machine
from mws_transport import shell_quote
from mws_run_state import (
    bundle_digest_for_files,
    create_config_bundle,
    digest_json,
    relative_repo,
)

DEPLOYER_ROOT = MOTOR_ROOT / "examples" / "deployer"
DEPLOY_PY = DEPLOYER_ROOT / "deploy.py"
OUTPUT_YAMLS = DEPLOYER_ROOT / "output_yamls"
MANIFEST_INJECTOR_VERSION = "mws-injector-v2"

CLUSTER_SCOPED_KINDS = frozenset(
    {
        "ClusterRole",
        "ClusterRoleBinding",
        "CustomResourceDefinition",
        "Namespace",
        "PersistentVolume",
        "StorageClass",
        "PriorityClass",
        "IngressClass",
        "RuntimeClass",
        "MutatingWebhookConfiguration",
        "ValidatingWebhookConfiguration",
    }
)

RUNTIME_CONTAINER_HINTS = (
    "mindie",
    "motor",
    "vllm",
    "engine",
    "coordinator",
    "controller",
    "worker",
    "server",
    "decode",
    "prefill",
)


def load_profile(profile_path: Path) -> dict[str, Any]:
    text = profile_path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise WorkspaceStateError(f"{profile_path} is YAML; install PyYAML") from exc
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise WorkspaceStateError(f"{profile_path} must contain an object")
    return data


def _yaml_loader():
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise WorkspaceStateError("PyYAML required for manifest processing") from exc
    return yaml


def load_yaml_documents(text: str) -> list[dict[str, Any]]:
    yaml = _yaml_loader()
    docs = list(yaml.safe_load_all(text))
    return [doc for doc in docs if isinstance(doc, dict)]


def dump_yaml_documents(documents: list[dict[str, Any]]) -> str:
    yaml = _yaml_loader()
    chunks = []
    for doc in documents:
        chunks.append(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
    return "---\n".join(chunks)


def is_cluster_scoped(doc: dict[str, Any]) -> bool:
    return str(doc.get("kind", "")) in CLUSTER_SCOPED_KINDS


def iter_pod_specs(doc: dict[str, Any]) -> Iterable[dict[str, Any]]:
    kind = doc.get("kind")
    spec = doc.get("spec")
    if not isinstance(spec, dict):
        return
    if kind in {"Deployment", "StatefulSet", "DaemonSet", "Job", "ReplicationController"}:
        template = spec.get("template")
        if isinstance(template, dict):
            pod_spec = template.get("spec")
            if isinstance(pod_spec, dict):
                yield pod_spec
    elif kind == "Pod":
        if isinstance(spec, dict):
            yield spec
    elif kind in {"AscendJob", "VolcanoJob"}:
        for key in ("template", "jobTemplate", "workerTemplate", "masterTemplate"):
            template = spec.get(key)
            if isinstance(template, dict):
                pod_spec = template.get("spec")
                if isinstance(pod_spec, dict):
                    yield pod_spec
        replica_specs = spec.get("replicaSpecs")
        if isinstance(replica_specs, dict):
            for replica in replica_specs.values():
                if not isinstance(replica, dict):
                    continue
                template = replica.get("template")
                if isinstance(template, dict):
                    pod_spec = template.get("spec")
                    if isinstance(pod_spec, dict):
                        yield pod_spec


def _is_runtime_container(container: dict[str, Any]) -> bool:
    name = str(container.get("name", "")).lower()
    image = str(container.get("image", "")).lower()
    command = " ".join(container.get("command") or []).lower()
    joined = " ".join((name, image, command))
    return any(hint in joined for hint in RUNTIME_CONTAINER_HINTS)


def _path_covers(mounted: str, requested: str) -> bool:
    """True when `mounted` is an ancestor-or-self of `requested`.

    Comparison is segment-wise so `/mnt/foo` does not cover `/mnt/foobar`.
    Used to avoid mounting overlapping hostPath volumes when the upstream
    template already mounts an ancestor of the target (e.g. `/mnt` covers
    `/mnt/share/...`).
    """
    mounted = mounted.rstrip("/") or "/"
    requested = requested.rstrip("/") or "/"
    if mounted == "/":
        return True
    return requested == mounted or requested.startswith(mounted + "/")


def _ensure_mnt_hostpath(pod_spec: dict[str, Any], mount_root: str = "/mnt") -> None:
    volumes = pod_spec.setdefault("volumes", [])
    if not isinstance(volumes, list):
        return
    for volume in volumes:
        if not isinstance(volume, dict):
            continue
        host_path = volume.get("hostPath")
        if isinstance(host_path, dict) and _path_covers(
            str(host_path.get("path", "")), mount_root
        ):
            return
    volumes.append({"name": "mnt", "hostPath": {"path": mount_root}})


def _ensure_mnt_mount(pod_spec: dict[str, Any], mount_root: str = "/mnt") -> None:
    volume_name = None
    for volume in pod_spec.get("volumes", []) or []:
        if not isinstance(volume, dict):
            continue
        host_path = volume.get("hostPath")
        if isinstance(host_path, dict) and _path_covers(
            str(host_path.get("path", "")), mount_root
        ):
            volume_name = volume.get("name") or "mnt"
            break
    if not volume_name:
        volume_name = "mnt"
    for container in pod_spec.get("containers", []) or []:
        if not isinstance(container, dict) or not _is_runtime_container(container):
            continue
        mounts = container.setdefault("volumeMounts", [])
        if not isinstance(mounts, list):
            continue
        if any(
            isinstance(item, dict)
            and _path_covers(str(item.get("mountPath", "")), mount_root)
            for item in mounts
        ):
            continue
        mounts.append({"name": volume_name, "mountPath": mount_root})


def inject_hostpath_mount(documents: list[dict[str, Any]], mount_root: str = "/mnt") -> list[dict[str, Any]]:
    patched: list[dict[str, Any]] = []
    for doc in documents:
        doc = copy.deepcopy(doc)
        for pod_spec in iter_pod_specs(doc):
            _ensure_mnt_hostpath(pod_spec, mount_root=mount_root)
            _ensure_mnt_mount(pod_spec, mount_root=mount_root)
        patched.append(doc)
    return patched


_PD_WORKLOAD_PATTERN = re.compile(r"^(.+)-([pd])(\d+)$")


def _ensure_pod_anti_affinity(pod_spec: dict[str, Any], other_label: str) -> None:
    """Add a hard host-level anti-affinity rule against `other_label`."""
    affinity = pod_spec.setdefault("affinity", {})
    if not isinstance(affinity, dict):
        return
    pod_anti = affinity.setdefault("podAntiAffinity", {})
    if not isinstance(pod_anti, dict):
        return
    required = pod_anti.setdefault("requiredDuringSchedulingIgnoredDuringExecution", [])
    if not isinstance(required, list):
        return
    for rule in required:
        if not isinstance(rule, dict):
            continue
        if rule.get("labelSelector") == {"app": other_label}:
            return
    required.append(
        {
            "labelSelector": {"matchLabels": {"app": other_label}},
            "topologyKey": "kubernetes.io/hostname",
        }
    )


def inject_pd_anti_affinity(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep prefill and decode engine pods off the same node in PD mode.

    Motor names engine workloads `{base}-p{index}` / `{base}-d{index}` and labels
    their pods with `app: <workload-name>`. For each matched pair we inject a
    required anti-affinity rule so a prefill pod refuses to land on a node that
    already runs its decode counterpart (and vice versa).
    """
    patched: list[dict[str, Any]] = []
    for doc in documents:
        doc = copy.deepcopy(doc)
        if doc.get("kind") in {"Deployment", "StatefulSet"}:
            name = str(doc.get("metadata", {}).get("name", ""))
            match = _PD_WORKLOAD_PATTERN.match(name)
            if match:
                base, role, index = match.group(1), match.group(2), match.group(3)
                other = "d" if role == "p" else "p"
                other_label = f"{base}-{other}{index}"
                for pod_spec in iter_pod_specs(doc):
                    _ensure_pod_anti_affinity(pod_spec, other_label)
        patched.append(doc)
    return patched


def inject_pythonpath_env(documents: list[dict[str, Any]], pythonpath: str) -> list[dict[str, Any]]:
    if not pythonpath:
        return documents
    patched: list[dict[str, Any]] = []
    for doc in documents:
        doc = copy.deepcopy(doc)
        for pod_spec in iter_pod_specs(doc):
            containers = pod_spec.get("containers", [])
            if not isinstance(containers, list):
                continue
            for container in containers:
                if not isinstance(container, dict) or not _is_runtime_container(container):
                    continue
                env = container.setdefault("env", [])
                if not isinstance(env, list):
                    continue
                replaced = False
                for item in env:
                    if isinstance(item, dict) and item.get("name") == "PYTHONPATH":
                        item["value"] = pythonpath
                        replaced = True
                        break
                if not replaced:
                    env.append({"name": "PYTHONPATH", "value": pythonpath})
        patched.append(doc)
    return patched


def inject_namespace(documents: list[dict[str, Any]], namespace: str) -> list[dict[str, Any]]:
    patched: list[dict[str, Any]] = []
    for doc in documents:
        doc = copy.deepcopy(doc)
        if namespace and not is_cluster_scoped(doc):
            metadata = dict(doc.get("metadata", {}))
            metadata["namespace"] = namespace
            doc["metadata"] = metadata
        patched.append(doc)
    return patched


def inject_image_ref(documents: list[dict[str, Any]], image_ref: str) -> list[dict[str, Any]]:
    if not image_ref:
        return documents
    patched: list[dict[str, Any]] = []
    for doc in documents:
        doc = copy.deepcopy(doc)
        for pod_spec in iter_pod_specs(doc):
            containers = pod_spec.get("containers", [])
            if not isinstance(containers, list):
                continue
            for container in containers:
                if not isinstance(container, dict) or not _is_runtime_container(container):
                    continue
                if "image" in container:
                    container["image"] = image_ref
        patched.append(doc)
    return patched


def inject_node_port_override(
    documents: list[dict[str, Any]],
    overrides: dict[int, int],
) -> list[dict[str, Any]]:
    """Rewrite Service nodePort values to avoid cluster-wide NodePort conflicts.

    overrides maps the template's original nodePort to the replacement value.
    Only applies to Services that carry the original nodePort; other docs are
    left untouched.
    """
    patched: list[dict[str, Any]] = []
    for doc in documents:
        doc = copy.deepcopy(doc)
        if doc.get("kind") != "Service":
            patched.append(doc)
            continue
        spec = doc.get("spec")
        if not isinstance(spec, dict):
            patched.append(doc)
            continue
        ports = spec.get("ports")
        if not isinstance(ports, list):
            patched.append(doc)
            continue
        changed = False
        for port in ports:
            if not isinstance(port, dict):
                continue
            node_port = port.get("nodePort")
            if isinstance(node_port, int) and node_port in overrides:
                port["nodePort"] = overrides[node_port]
                changed = True
        if changed:
            spec["ports"] = ports
        patched.append(doc)
    return patched


def _load_node_port_overrides(native_config: dict[str, Any]) -> dict[int, int]:
    """Read optional node_port_overrides from motor_deploy_config.

    Maps the template's original nodePort to a replacement. Entries must be
    positive integers.
    """
    deploy = native_config.get("user_config.json", {}).get("motor_deploy_config", {})
    raw = deploy.get("node_port_overrides")
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise WorkspaceStateError("motor_deploy_config.node_port_overrides must be an object")
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


def load_motor_deploy_config(config_dir: Path) -> dict[str, Any]:
    user_config_path = config_dir / "user_config.json"
    if not user_config_path.exists():
        raise WorkspaceStateError(f"user_config.json missing in {config_dir}")
    data = json.loads(user_config_path.read_text(encoding="utf-8"))
    deploy = data.get("motor_deploy_config")
    if not isinstance(deploy, dict):
        raise WorkspaceStateError("user_config.json must contain motor_deploy_config")
    job_id = str(deploy.get("job_id", "")).strip()
    if not job_id:
        raise WorkspaceStateError("motor_deploy_config.job_id is required in user_config.json")
    # Motor 原生配置没有独立 namespace 字段，upstream deployer 恒以 job_id 作为
    # namespace；workspace 不得发明第二套字段。显式给了不一致的 namespace 属于
    # 旧运行时副本残留，必须 fail closed 而不是静默忽略。
    explicit_namespace = str(deploy.get("namespace") or "").strip()
    if explicit_namespace and explicit_namespace != job_id:
        raise WorkspaceStateError(
            "motor_deploy_config.namespace is not a Motor native field and must not "
            f"differ from job_id ({explicit_namespace!r} != {job_id!r}); remove the "
            "namespace field and set job_id to the target namespace"
        )
    namespace = job_id
    return {
        "job_id": job_id,
        "namespace": namespace,
        "image_name": str(deploy.get("image_name") or deploy.get("image") or "").strip(),
    }


def _int_field(deploy: dict[str, Any], key: str) -> int:
    value = deploy.get(key)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise WorkspaceStateError(
            f"motor_deploy_config.{key} must be an integer, got {value!r}"
        ) from exc


def _node_selector_hostnames(deploy: dict[str, Any], key: str) -> list[str]:
    selector = deploy.get(key)
    if not selector:
        return []
    if not isinstance(selector, dict):
        raise WorkspaceStateError(f"motor_deploy_config.{key} must be an object")
    hostnames: list[str] = []
    for selector_key, value in selector.items():
        if selector_key == "kubernetes.io/hostname":
            hostnames.append(str(value))
    if not hostnames:
        raise WorkspaceStateError(
            f"motor_deploy_config.{key} has no kubernetes.io/hostname selector; "
            "cannot derive target node for NPU capacity check"
        )
    return hostnames


def compute_npu_requirement(native_config: dict[str, Any]) -> dict[str, Any]:
    """Compute total NPU demand from motor_deploy_config.

    Returns {"total": int, "per_node": {hostname: count}} derived from
    p/d instance counts and pod NPU numbers, mapped to the node selected by
    the prefill/decode node selectors.
    """
    deploy = native_config.get("user_config.json", {}).get("motor_deploy_config", {})
    if not isinstance(deploy, dict):
        raise WorkspaceStateError("user_config.json must contain motor_deploy_config")

    def demand(
        instances_key: str,
        pods_per_instance_key: str,
        npu_per_pod_key: str,
        selector_key: str,
    ) -> dict[str, int]:
        instances = _int_field(deploy, instances_key)
        pods_per_instance = _int_field(deploy, pods_per_instance_key)
        npu_per_pod = _int_field(deploy, npu_per_pod_key)
        per_node = instances * pods_per_instance * npu_per_pod
        if per_node > 0 and not deploy.get(selector_key):
            raise WorkspaceStateError(
                f"motor_deploy_config.{selector_key} is required when {instances_key} > 0 "
                "for the NPU capacity check"
            )
        per_node_map: dict[str, int] = {}
        for hostname in _node_selector_hostnames(deploy, selector_key):
            per_node_map[hostname] = per_node
        return per_node_map

    per_node: dict[str, int] = {}
    for demand_map in (
        demand(
            "p_instances_num",
            "single_p_instance_pod_num",
            "p_pod_npu_num",
            "prefill_node_selector",
        ),
        demand(
            "d_instances_num",
            "single_d_instance_pod_num",
            "d_pod_npu_num",
            "decode_node_selector",
        ),
    ):
        for hostname, count in demand_map.items():
            per_node[hostname] = per_node.get(hostname, 0) + count
    return {"total": sum(per_node.values()), "per_node": per_node}


def check_node_npu_capacity(
    *,
    kube_context: str,
    namespace: str,
    per_node_requirement: dict[str, int],
    machine: dict[str, Any] | None = None,
    kubectl: KubectlRunner | None = None,
    npu_resource_name: str = "huawei.com/Ascend910",
) -> list[dict[str, Any]]:
    """Verify each selected node has enough allocatable NPUs for the requested count.

    A Pod already scheduled on the node counts against the available pool, so the
    check compares requested count against (allocatable - allocated) per node.
    Returns check records; a shortfall or unresolvable node is an error check.
    """
    run_kubectl = _resolve_kubectl_runner(
        machine=machine,
        kube_context=kube_context,
        kubectl=kubectl,
    )

    def summarize(node_name: str) -> tuple[int, int, int]:
        """Return (allocatable, allocated, available) NPU counts for a node."""
        allocatable = 0
        node_result = run_kubectl("get", "node", node_name, "-o", "json")
        if node_result.returncode == 0 and node_result.stdout.strip():
            try:
                node_payload = json.loads(node_result.stdout)
                raw = node_payload.get("status", {}).get("allocatable", {}).get(npu_resource_name)
                allocatable = int(raw) if raw is not None else 0
            except (json.JSONDecodeError, ValueError, AttributeError):
                allocatable = 0
        pod_result = run_kubectl(
            "get", "pods", "-A",
            "--field-selector",
            f"spec.nodeName={node_name},status.phase=Running",
            "-o", "json",
        )
        allocated = 0
        if pod_result.returncode == 0 and pod_result.stdout.strip():
            try:
                pods_payload = json.loads(pod_result.stdout)
            except json.JSONDecodeError:
                pods_payload = {}
            for item in pods_payload.get("items", []) if isinstance(pods_payload, dict) else []:
                if not isinstance(item, dict):
                    continue
                for container in item.get("spec", {}).get("containers", []):
                    if not isinstance(container, dict):
                        continue
                    requests = container.get("resources", {}).get("requests", {}) if isinstance(
                        container.get("resources"), dict
                    ) else {}
                    if not isinstance(requests, dict):
                        continue
                    raw = requests.get(npu_resource_name)
                    try:
                        allocated += int(raw)
                    except (TypeError, ValueError):
                        continue
        return allocatable, allocated, max(allocatable - allocated, 0)

    checks: list[dict[str, Any]] = []
    for node_name, required in sorted(per_node_requirement.items()):
        allocatable, allocated, available = summarize(node_name)
        ok = available >= required
        checks.append(
            {
                "name": f"npu_capacity:{node_name}",
                "status": "ok" if ok else "error",
                "message": (
                    f"node {node_name} has {available} NPU available "
                    f"(allocatable={allocatable}, allocated={allocated}, required={required})"
                ),
            }
        )
    if not checks:
        checks.append(
            {
                "name": "npu_capacity",
                "status": "error",
                "message": "no node selected by node_selector for NPU capacity check",
            }
        )
    return checks


def patch_user_config_copy(
    *,
    source_config_dir: Path,
    dest_config_dir: Path,
    base_image_ref: str,
) -> Path:
    dest_config_dir.mkdir(parents=True, exist_ok=True)
    for item in source_config_dir.iterdir():
        if item.is_file():
            shutil.copy2(item, dest_config_dir / item.name)
    user_config_path = dest_config_dir / "user_config.json"
    if not user_config_path.exists():
        raise WorkspaceStateError(f"user_config.json missing in {source_config_dir}")
    load_motor_deploy_config(dest_config_dir)
    if base_image_ref:
        data = json.loads(user_config_path.read_text(encoding="utf-8"))
        deploy = data.setdefault("motor_deploy_config", {})
        deploy["image_name"] = base_image_ref
        user_config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return user_config_path


def extract_workload_names(documents: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for doc in documents:
        kind = doc.get("kind")
        metadata = doc.get("metadata", {})
        name = metadata.get("name")
        if not name:
            continue
        if kind in {"Deployment", "StatefulSet", "DaemonSet", "Job"}:
            names.append(f"{kind.lower()}/{name}")
        elif kind == "AscendJob":
            names.append(f"ascendjob/{name}")
    return names


def collect_generated_manifests(output_dir: Path) -> list[Path]:
    if not output_dir.exists():
        return []
    return sorted(output_dir.glob("*.yaml"))


def run_deploy_dry_run(
    config_dir: Path,
    *,
    machine: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run `deploy.py --dry-run` and return the generated manifest names.

    When `machine` is given the dry-run executes on the machine host over SSH
    (config uploaded first, generated manifests fetched back). Otherwise it runs
    locally.
    """
    if machine is None:
        return _run_deploy_dry_run_local(config_dir)
    return _run_deploy_dry_run_remote(config_dir, machine)


def _run_deploy_dry_run_local(config_dir: Path) -> dict[str, Any]:
    if not DEPLOY_PY.exists():
        return {
            "status": "error",
            "reason": f"deployer not found: {DEPLOY_PY}",
            "returncode": None,
        }
    before = {path.name for path in collect_generated_manifests(OUTPUT_YAMLS)}
    cmd = [
        "python3",
        str(DEPLOY_PY),
        "--config_dir",
        str(config_dir),
        "--dry-run",
    ]
    result = subprocess.run(
        cmd,
        cwd=str(DEPLOYER_ROOT),
        check=False,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    after = {path.name for path in collect_generated_manifests(OUTPUT_YAMLS)}
    generated = sorted(after - before)
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
        "generated_files": generated,
    }


def _run_deploy_dry_run_remote(config_dir: Path, machine: dict[str, Any]) -> dict[str, Any]:
    """Execute the deployer dry-run on the machine host over SSH or natively."""
    from mws_execution import execution_adapter_for_machine

    paths = build_fixed_source_paths(machine)
    remote_motor = str(paths["motor_source"]).rstrip("/")
    remote_deployer = f"{remote_motor}/examples/deployer"
    remote_deploy_py = f"{remote_deployer}/deploy.py"
    remote_config = f"/tmp/mws-dryrun-config-{os.getpid()}"

    adapter = execution_adapter_for_machine(machine)

    probe = adapter.run(f"test -f {shell_quote(remote_deploy_py)} && echo OK")
    if probe.returncode != 0 or "OK" not in probe.stdout:
        return {
            "status": "error",
            "reason": f"remote deployer not found: {remote_deploy_py}",
            "returncode": probe.returncode,
            "stdout_tail": probe.stdout[-4000:],
            "stderr_tail": probe.stderr[-4000:],
            "generated_files": [],
        }

    cleanup = adapter.run(f"rm -rf {shell_quote(remote_config)} && mkdir -p {shell_quote(remote_config)}")
    if cleanup.returncode:
        return {
            "status": "error",
            "reason": f"remote dry-run config staging failed: {cleanup.stderr.strip() or cleanup.stdout.strip()}",
            "returncode": cleanup.returncode,
            "generated_files": [],
        }
    for item in sorted(config_dir.iterdir()):
        if item.is_file():
            adapter.upload_file(str(item), f"{remote_config}/{item.name}")

    remote_output = f"{remote_deployer}/output_yamls"
    clear = adapter.run(
        f"rm -rf {shell_quote(remote_output)} && mkdir -p {shell_quote(remote_output)}"
    )
    if clear.returncode:
        return {
            "status": "error",
            "reason": f"remote output staging failed: {clear.stderr.strip() or clear.stdout.strip()}",
            "returncode": clear.returncode,
            "generated_files": [],
        }

    command = (
        f"cd {shell_quote(remote_deployer)} && "
        f"python3 deploy.py --config_dir {shell_quote(remote_config)} --dry-run"
    )
    result = adapter.run(command)

    remote_files = set(adapter.directory_file_hashes(remote_output).keys())

    local_output = OUTPUT_YAMLS
    local_output.mkdir(parents=True, exist_ok=True)
    fetched: list[str] = []
    for name in sorted(remote_files):
        remote_path = f"{remote_output}/{name}"
        data = adapter.read_bytes(remote_path)
        (local_output / name).write_bytes(data)
        fetched.append(name)

    adapter.run(f"rm -rf {shell_quote(remote_config)}")
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
        "generated_files": fetched,
    }


def run_deploy_full(
    config_dir: Path,
    *,
    machine: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute the full upstream deployment (not dry-run) on the machine host.

    The config directory is staged under `/tmp` on the machine and
    `deploy.py --nostep --auto_log_collect` is run from the remote deployer.
    ConfigMap/env generation, apply and log collection are all owned by the
    upstream deployer. Returns stdout/stderr tails plus the status.
    """
    if machine is None:
        return _run_deploy_full_local(config_dir)
    return _run_deploy_full_remote(config_dir, machine)


def _run_deploy_full_local(config_dir: Path) -> dict[str, Any]:
    if not DEPLOY_PY.exists():
        return {
            "status": "error",
            "reason": f"deployer not found: {DEPLOY_PY}",
            "returncode": None,
        }
    cmd = [
        "python3",
        str(DEPLOY_PY),
        "--config_dir",
        str(config_dir),
        "--nostep",
        "--auto_log_collect",
    ]
    result = subprocess.run(
        cmd,
        cwd=str(DEPLOYER_ROOT),
        check=False,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
    }


def _run_deploy_full_remote(config_dir: Path, machine: dict[str, Any]) -> dict[str, Any]:
    """Execute the full upstream deployment on the machine host."""
    from mws_execution import execution_adapter_for_machine

    paths = build_fixed_source_paths(machine)
    remote_motor = str(paths["motor_source"]).rstrip("/")
    remote_deployer = f"{remote_motor}/examples/deployer"
    remote_deploy_py = f"{remote_deployer}/deploy.py"
    remote_config = f"/tmp/mws-deploy-config-{os.getpid()}"

    adapter = execution_adapter_for_machine(machine)

    probe = adapter.run(f"test -f {shell_quote(remote_deploy_py)} && echo OK")
    if probe.returncode != 0 or "OK" not in probe.stdout:
        return {
            "status": "error",
            "reason": f"remote deployer not found: {remote_deploy_py}",
            "returncode": probe.returncode,
            "stdout_tail": probe.stdout[-4000:],
            "stderr_tail": probe.stderr[-4000:],
        }

    cleanup = adapter.run(
        f"rm -rf {shell_quote(remote_config)} && mkdir -p {shell_quote(remote_config)}"
    )
    if cleanup.returncode:
        return {
            "status": "error",
            "reason": f"remote config staging failed: {cleanup.stderr.strip() or cleanup.stdout.strip()}",
            "returncode": cleanup.returncode,
        }
    for item in sorted(config_dir.iterdir()):
        if item.is_file() and item.name != "bundle.json":
            adapter.upload_file(str(item), f"{remote_config}/{item.name}")

    command = (
        f"cd {shell_quote(remote_deployer)} && "
        "python3 deploy.py --config_dir "
        f"{shell_quote(remote_config)} --nostep --auto_log_collect"
    )
    result = adapter.run(command)
    return {
        "status": "ok" if result.returncode == 0 else "error",
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
    }


def process_manifest_documents(
    documents: list[dict[str, Any]],
    *,
    pythonpath: str,
    namespace: str,
    base_image_ref: str,
    mount_root: str = "/mnt",
    node_port_overrides: dict[int, int] | None = None,
) -> list[dict[str, Any]]:
    docs = inject_namespace(documents, namespace)
    docs = inject_hostpath_mount(docs, mount_root=mount_root)
    docs = inject_pd_anti_affinity(docs)
    docs = inject_image_ref(docs, base_image_ref)
    docs = inject_pythonpath_env(docs, pythonpath)
    if node_port_overrides:
        docs = inject_node_port_override(docs, node_port_overrides)
    return docs


def process_manifest_file(
    path: Path,
    *,
    pythonpath: str,
    namespace: str,
    base_image_ref: str,
    mount_root: str,
    dest_dir: Path,
    node_port_overrides: dict[int, int] | None = None,
) -> Path:
    text = path.read_text(encoding="utf-8")
    docs = load_yaml_documents(text)
    docs = process_manifest_documents(
        docs,
        pythonpath=pythonpath,
        namespace=namespace,
        base_image_ref=base_image_ref,
        mount_root=mount_root,
        node_port_overrides=node_port_overrides,
    )
    out = dest_dir / path.name
    out.write_text(dump_yaml_documents(docs), encoding="utf-8")
    return out


def kubectl_dry_run_and_diff(
    machine: dict[str, Any],
    kube_context: str,
    manifest_paths: list[Path],
    namespace: str,
) -> dict[str, Any]:
    kubectl = build_kubectl_runner(machine, kube_context=kube_context)
    results: dict[str, Any] = {"status": "ok", "manifests": []}
    with stage_remote_files(machine, manifest_paths, prefix="mws-plan-manifests") as staged:
        for manifest in manifest_paths:
            remote_manifest = staged[manifest]
            item: dict[str, Any] = {"manifest": relative_repo(manifest)}
            apply_args = ["apply", "--dry-run=server", "-f", remote_manifest]
            if namespace:
                apply_args.extend(["-n", namespace])
            apply = kubectl(*apply_args)
            item["server_dry_run"] = {
                "returncode": apply.returncode,
                "stdout": apply.stdout[-2000:],
                "stderr": apply.stderr[-2000:],
            }
            diff_args = ["diff", "-f", remote_manifest]
            if namespace:
                diff_args.extend(["-n", namespace])
            diff = kubectl(*diff_args)
            item["diff"] = {
                "returncode": diff.returncode,
                "stdout": diff.stdout[-2000:],
                "stderr": diff.stderr[-2000:],
            }
            if apply.returncode != 0:
                results["status"] = "warning"
            results["manifests"].append(item)
    return results


def render_plan(
    *,
    machine: dict[str, Any],
    profile: dict[str, Any],
    profile_path: str,
    config_dir: Path,
    run_dir: Path,
    base_image_ref: str,
    parity_run_id: str | None = None,
    lock_verify: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir = run_dir / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    deploy_config = load_motor_deploy_config(config_dir)
    namespace = deploy_config["namespace"]
    job_id = deploy_config["job_id"]
    pythonpath = pythonpath_for_machine(machine)
    mount_root = build_fixed_source_paths(machine)["mount_root"]

    staged_config = run_dir / "config"
    patch_user_config_copy(
        source_config_dir=config_dir,
        dest_config_dir=staged_config,
        base_image_ref=base_image_ref,
    )

    deploy_result = run_deploy_dry_run(staged_config, machine=machine)
    if deploy_result.get("status") != "ok":
        raise WorkspaceStateError(
            "deployer dry-run failed: "
            + (deploy_result.get("stderr_tail") or deploy_result.get("reason") or "unknown error")
        )
    generated_names = deploy_result.get("generated_files") or []
    if not generated_names:
        raise WorkspaceStateError("deployer dry-run produced no new YAML manifests")

    manifest_files: list[str] = []
    workload_names: list[str] = []
    for name in generated_names:
        src = OUTPUT_YAMLS / name
        if not src.exists():
            raise WorkspaceStateError(f"expected generated manifest missing: {src}")
        text = src.read_text(encoding="utf-8")
        docs = load_yaml_documents(text)
        workload_names.extend(extract_workload_names(docs))
        out = process_manifest_file(
            src,
            pythonpath=pythonpath,
            namespace=namespace,
            base_image_ref=base_image_ref,
            mount_root=str(mount_root),
            dest_dir=manifests_dir,
        )
        manifest_files.append(relative_repo(out))

    k8s_checks = kubectl_dry_run_and_diff(
        machine,
        str(machine.get("kube_context") or ""),
        [REPO_ROOT / path for path in manifest_files],
        namespace,
    )

    plan_body = {
        "machine": machine_ref(machine),
        "parity_run_id": parity_run_id,
        "namespace": namespace,
        "job_id": job_id,
        "profile": profile_path,
        "config_source": relative_repo(config_dir),
        "staged_config_dir": relative_repo(staged_config),
        "submodule_commits_diagnostic": {
            item["name"]: item.get("commit")
            for item in (lock_verify or {}).get("sources", [])
            if item.get("present")
        },
        "base_image_ref": base_image_ref,
        "pythonpath": pythonpath,
        "manifest_files": manifest_files,
        "workload_names": workload_names,
        "deploy_dry_run": deploy_result,
        "kubernetes": k8s_checks,
    }
    (run_dir / "plan-body.json").write_text(
        json.dumps(plan_body, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return plan_body


def load_plan_from_dir(plan_dir: Path) -> dict[str, Any]:
    path = plan_dir / "plan-body.json"
    if not path.exists():
        raise WorkspaceStateError(f"plan-body.json missing in {plan_dir}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise WorkspaceStateError(f"{path} must contain an object")
    return data


def apply_from_plan(
    plan: dict[str, Any],
    machine: dict[str, Any],
    *,
    kube_context: str = "",
) -> dict[str, Any]:
    kubectl = build_kubectl_runner(machine, kube_context=kube_context)
    namespace = str(plan.get("namespace", ""))
    results: list[dict[str, Any]] = []
    manifests = [REPO_ROOT / rel for rel in plan.get("manifest_files", [])]
    with stage_remote_files(machine, manifests, prefix="mws-apply-plan") as staged:
        for rel, manifest in zip(plan.get("manifest_files", []), manifests):
            args = ["apply", "-f", staged[manifest]]
            if namespace:
                args.extend(["-n", namespace])
            result = kubectl(*args)
            results.append(
                {
                    "manifest": rel,
                    "returncode": result.returncode,
                    "stdout": result.stdout[-2000:],
                    "stderr": result.stderr[-2000:],
                }
            )
    ok = all(item["returncode"] == 0 for item in results) if results else False
    return {"status": "ok" if ok else "error", "apply_results": results}


def stop_from_plan(
    plan: dict[str, Any],
    machine: dict[str, Any],
    *,
    kube_context: str = "",
) -> dict[str, Any]:
    kubectl = build_kubectl_runner(machine, kube_context=kube_context)
    namespace = str(plan.get("namespace", ""))
    results: list[dict[str, Any]] = []
    rel_paths = list(plan.get("manifest_files", []))
    manifests = [REPO_ROOT / rel for rel in rel_paths]
    with stage_remote_files(machine, manifests, prefix="mws-stop-plan") as staged:
        for rel, manifest in reversed(list(zip(rel_paths, manifests))):
            args = ["delete", "--ignore-not-found", "-f", staged[manifest]]
            if namespace:
                args.extend(["-n", namespace])
            result = kubectl(*args)
            results.append(
                {
                    "manifest": rel,
                    "returncode": result.returncode,
                    "stdout": result.stdout[-2000:],
                    "stderr": result.stderr[-2000:],
                }
            )
    ok = all(item["returncode"] == 0 for item in results) if results else False
    return {"status": "ok" if ok else "error", "delete_results": results}


def restart_deploy_workloads(
    plan: dict[str, Any],
    machine: dict[str, Any],
    *,
    kube_context: str = "",
) -> dict[str, Any]:
    kubectl = build_kubectl_runner(machine, kube_context=kube_context)
    namespace = str(plan.get("namespace", ""))
    if not namespace:
        return {"status": "error", "errors": ["plan missing namespace"]}

    actions: list[dict[str, Any]] = []
    matched = False
    for resource in plan.get("workload_names", []):
        if not isinstance(resource, str) or "/" not in resource:
            continue
        matched = True
        if resource.startswith("deployment/") or resource.startswith("statefulset/"):
            result = kubectl("rollout", "restart", resource, "-n", namespace)
            actions.append(
                {
                    "action": "rollout_restart",
                    "resource": resource,
                    "returncode": result.returncode,
                    "stderr": result.stderr[-1000:],
                }
            )
            continue
        if resource.startswith("job/") or resource.startswith("ascendjob/"):
            delete_args = [
                "delete",
                "pods",
                "-n",
                namespace,
                "-l",
                f"job-name={resource.split('/', 1)[1]}",
                "--wait=false",
            ]
            result = kubectl(*delete_args)
            actions.append(
                {
                    "action": "delete_pods",
                    "resource": resource,
                    "returncode": result.returncode,
                    "stderr": result.stderr[-1000:],
                }
            )

    if not matched:
        return {
            "status": "error",
            "errors": [
                "could not locate restart targets from deploy plan workload_names; "
                f"namespace={namespace}"
            ],
        }
    failed = [item for item in actions if item.get("returncode") not in (0, None)]
    return {"status": "error" if failed else "ok", "actions": actions}


def pod_readiness_probe(
    machine: dict[str, Any],
    namespace: str,
    *,
    kube_context: str = "",
) -> dict[str, Any]:
    kubectl = build_kubectl_runner(machine, kube_context=kube_context)
    result = kubectl("get", "pods", "-n", namespace, "-o", "json")
    if result.returncode:
        return {"ready": False, "error": result.stderr.strip() or result.stdout.strip()}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ready": False, "error": "invalid kubectl json"}
    items = data.get("items", [])
    total = len(items)
    ready = sum(
        1
        for pod in items
        if all(
            cond.get("status") == "True"
            for cond in pod.get("status", {}).get("conditions", [])
            if cond.get("type") == "Ready"
        )
    )
    return {"ready": total > 0 and ready == total, "pods_total": total, "pods_ready": ready}


def collect_component_status(
    machine: dict[str, Any],
    namespace: str,
    *,
    kube_context: str = "",
) -> dict[str, Any]:
    kubectl = build_kubectl_runner(machine, kube_context=kube_context)
    components = {
        "pods": ("get", "pods", "-n", namespace, "-o", "wide"),
        "services": ("get", "svc", "-n", namespace, "-o", "wide"),
        "jobs": ("get", "jobs", "-n", namespace, "-o", "wide"),
    }
    out: dict[str, Any] = {"status": "ok", "components": {}}
    for name, args in components.items():
        result = kubectl(*args)
        out["components"][name] = {
            "returncode": result.returncode,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-1000:],
        }
        if result.returncode:
            out["status"] = "warning"
    return out


def resolve_deploy_base_image(config_dir: Path, lock: dict[str, Any] | None = None) -> str:
    try:
        return resolve_base_image_ref(config_dir=config_dir, lock=lock)
    except WorkspaceStateError:
        if lock:
            runtime = lock.get("runtime", {})
            locked = runtime.get("base_image_ref") or runtime.get("base_image", "")
            if locked and locked != "UNRESOLVED":
                return str(locked)
        raise


def deployer_version_token() -> str:
    if DEPLOY_PY.exists():
        return digest_json(
            {"deploy_py": DEPLOY_PY.read_bytes().decode("utf-8", errors="replace")[:4096]}
        )
    return "missing-deployer"


def normalize_native_config(config_dir: Path) -> dict[str, Any]:
    user_config_path = config_dir / "user_config.json"
    env_path = config_dir / "env.json"
    user_config = json.loads(user_config_path.read_text(encoding="utf-8"))
    env_config = json.loads(env_path.read_text(encoding="utf-8")) if env_path.exists() else {}
    return {"user_config.json": user_config, "env.json": env_config}


def compute_config_fingerprint(
    *,
    native_config: dict[str, Any],
    machine_paths: dict[str, str],
    deployer_version: str,
    injector_version: str = MANIFEST_INJECTOR_VERSION,
) -> str:
    payload = {
        "native_config": native_config,
        "machine_paths": machine_paths,
        "deployer_version": deployer_version,
        "injector_version": injector_version,
    }
    return digest_json(payload)


def _resolve_kubectl_runner(
    *,
    machine: dict[str, Any] | None,
    kube_context: str,
    kubectl: KubectlRunner | None,
) -> KubectlRunner:
    if kubectl is not None:
        return kubectl
    if machine is None:
        raise WorkspaceStateError("machine record is required for remote kubectl")
    return build_kubectl_runner(machine, kube_context=kube_context)


def verify_namespace_exists(
    *,
    kube_context: str,
    namespace: str,
    machine: dict[str, Any] | None = None,
    kubectl: KubectlRunner | None = None,
) -> dict[str, Any]:
    run_kubectl = _resolve_kubectl_runner(
        machine=machine,
        kube_context=kube_context,
        kubectl=kubectl,
    )
    result = run_kubectl("get", "namespace", namespace, "-o", "name")
    ok = result.returncode == 0 and namespace in result.stdout
    return {
        "name": "namespace_exists",
        "status": "ok" if ok else "error",
        "message": f"namespace {namespace!r} exists" if ok else f"namespace {namespace!r} not found",
        "evidence": result.stdout.strip() or result.stderr.strip(),
    }


def verify_manifest_rbac(
    *,
    kube_context: str,
    namespace: str,
    manifest_paths: list[Path],
    machine: dict[str, Any] | None = None,
    kubectl: KubectlRunner | None = None,
) -> list[dict[str, Any]]:
    run_kubectl = _resolve_kubectl_runner(
        machine=machine,
        kube_context=kube_context,
        kubectl=kubectl,
    )
    checks: list[dict[str, Any]] = []
    verbs = [
        ("create", "deployments"),
        ("create", "services"),
        ("create", "configmaps"),
    ]
    for verb, resource in verbs:
        result = run_kubectl("auth", "can-i", verb, resource, "-n", namespace)
        ok = result.stdout.strip().lower() == "yes"
        checks.append(
            {
                "name": f"rbac:{verb}:{resource}",
                "status": "ok" if ok else "error",
                "message": result.stdout.strip() or result.stderr.strip(),
            }
        )
    if manifest_paths:
        checks.append(
            {
                "name": "manifest_files_present",
                "status": "ok",
                "message": f"{len(manifest_paths)} manifest(s) staged",
            }
        )
    return checks


def kubectl_server_side_dry_run(
    *,
    kube_context: str,
    manifest_paths: list[Path],
    namespace: str,
    kubectl: KubectlRunner | None = None,
    machine: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    run_kubectl = _resolve_kubectl_runner(
        machine=machine,
        kube_context=kube_context,
        kubectl=kubectl,
    )

    def run_checks(targets: dict[Path, str]) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        for manifest in manifest_paths:
            target = targets[manifest]
            result = run_kubectl("apply", "--dry-run=server", "-f", target, "-n", namespace)
            checks.append(
                {
                    "name": f"server_side_dry_run:{manifest.name}",
                    "status": "ok" if result.returncode == 0 else "error",
                    "message": result.stderr.strip() or result.stdout.strip() or manifest.name,
                }
            )
        return checks

    if machine is None:
        return run_checks({manifest: str(manifest) for manifest in manifest_paths})
    try:
        with stage_remote_files(machine, manifest_paths, prefix="mws-dryrun-manifests") as staged:
            return run_checks(staged)
    except WorkspaceStateError as exc:
        return [
            {
                "name": "server_side_dry_run",
                "status": "unavailable",
                "message": str(exc),
            }
        ]


def _configure_failed(
    runner: Any,
    *,
    namespace: str,
    deploy_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ready": False,
        "checks": runner.checks,
        "warnings": runner.warnings,
        "errors": runner.errors,
        "stopped_at": runner.stopped_at,
        "namespace": namespace,
    }
    if deploy_result is not None:
        payload["deploy_dry_run"] = deploy_result
    return payload


def configure_deploy_bundle(
    *,
    machine: dict[str, Any],
    config_dir: Path,
    run_dir: Path,
    kube_context: str,
    base_image_ref: str,
    parity_path_refs: dict[str, str],
    reuse_bundle_dir: Path | None = None,
    skip_npu_check: bool = False,
) -> dict[str, Any]:
    """Render or reuse an immutable deploy config bundle."""
    from mws_result import CheckRunner

    runner = CheckRunner()
    run_dir.mkdir(parents=True, exist_ok=True)
    native_config = normalize_native_config(config_dir)
    deploy_config = load_motor_deploy_config(config_dir)
    namespace = deploy_config["namespace"]
    machine_paths = build_fixed_source_paths(machine)
    node_port_overrides = _load_node_port_overrides(native_config)
    fingerprint = compute_config_fingerprint(
        native_config=native_config,
        machine_paths=machine_paths,
        deployer_version=deployer_version_token(),
    )

    if reuse_bundle_dir and reuse_bundle_dir.exists():
        bundle_json = reuse_bundle_dir / "bundle.json"
        if not bundle_json.exists():
            raise WorkspaceStateError(f"reuse bundle missing manifest: {bundle_json}")
        bundle_meta = json.loads(bundle_json.read_text(encoding="utf-8"))
        stored_paths = bundle_meta.get("machine_paths", {})
        if stored_paths != machine_paths:
            raise WorkspaceStateError("bundle machine path mapping does not match current parity/machine")
        staged_paths = {
            name: reuse_bundle_dir / name
            for name in sorted(p.name for p in reuse_bundle_dir.rglob("*") if p.is_file() and p.name != "bundle.json")
        }
        manifest_dir = reuse_bundle_dir / "manifests"
        if manifest_dir.exists():
            staged_paths = {
                f"manifests/{path.name}": path for path in sorted(manifest_dir.glob("*.yaml"))
            }
            for extra in ("user_config.json", "env.json"):
                extra_path = reuse_bundle_dir / extra
                if extra_path.exists():
                    staged_paths[extra] = extra_path
        expected_digest = str(bundle_meta.get("bundle_digest", ""))
        if staged_paths and expected_digest:
            current_digest = bundle_digest_for_files(staged_paths)
            if current_digest != expected_digest:
                raise WorkspaceStateError("config bundle content was modified or fingerprint collision detected")
        runner.append({"name": "reuse_bundle", "status": "ok", "message": str(reuse_bundle_dir)})
        return {
            "ready": True,
            "checks": runner.checks,
            "config_fingerprint": fingerprint,
            "bundle_digest": bundle_meta.get("bundle_digest"),
            "bundle_dir": str(reuse_bundle_dir),
            "namespace": namespace,
            "job_id": deploy_config["job_id"],
            "manifest_files": bundle_meta.get("manifest_files", []),
            "reused": True,
        }

    kubectl_runner = build_kubectl_runner(machine, kube_context=kube_context)
    ns_check = verify_namespace_exists(
        kube_context=kube_context,
        namespace=namespace,
        kubectl=kubectl_runner,
    )
    if not runner.append(ns_check):
        return _configure_failed(runner, namespace=namespace)

    staged_config = run_dir / "config"
    patch_user_config_copy(
        source_config_dir=config_dir,
        dest_config_dir=staged_config,
        base_image_ref=base_image_ref,
    )
    runner.append({"name": "stage_native_config", "status": "ok", "message": str(staged_config)})

    deploy_result = run_deploy_dry_run(staged_config, machine=machine)
    if deploy_result.get("status") != "ok":
        runner.append(
            {
                "name": "upstream_dry_run",
                "status": "error",
                "message": deploy_result.get("stderr_tail") or "upstream dry-run failed",
            }
        )
        return _configure_failed(runner, namespace=namespace, deploy_result=deploy_result)

    runner.append({"name": "upstream_dry_run", "status": "ok", "message": "manifests generated"})
    manifests_dir = run_dir / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    pythonpath = pythonpath_for_machine(machine)
    mount_root = machine_paths["mount_root"]
    manifest_paths: list[Path] = []
    manifest_files: list[str] = []
    workload_names: list[str] = []
    for name in deploy_result.get("generated_files") or []:
        src = OUTPUT_YAMLS / name
        if not src.exists():
            runner.append(
                {
                    "name": f"manifest:{name}",
                    "status": "error",
                    "message": f"expected generated manifest missing: {src}",
                }
            )
            return _configure_failed(runner, namespace=namespace)
        text = src.read_text(encoding="utf-8")
        docs = load_yaml_documents(text)
        workload_names.extend(extract_workload_names(docs))
        out = process_manifest_file(
            src,
            pythonpath=pythonpath,
            namespace=namespace,
            base_image_ref=base_image_ref,
            mount_root=str(mount_root),
            dest_dir=manifests_dir,
            node_port_overrides=node_port_overrides or None,
        )
        manifest_paths.append(out)
        manifest_files.append(relative_repo(out))

    for check in verify_manifest_rbac(
        kube_context=kube_context,
        namespace=namespace,
        manifest_paths=manifest_paths,
        kubectl=kubectl_runner,
    ):
        if not runner.append(check):
            return _configure_failed(runner, namespace=namespace)

    for check in kubectl_server_side_dry_run(
        kube_context=kube_context,
        manifest_paths=manifest_paths,
        namespace=namespace,
        kubectl=kubectl_runner,
        machine=machine,
    ):
        if not runner.append(check):
            return _configure_failed(runner, namespace=namespace)

    if skip_npu_check:
        runner.append(
            {
                "name": "npu_capacity",
                "status": "ok",
                "message": "skipped by --skip-npu-check",
            }
        )
    else:
        npu_requirement = compute_npu_requirement(native_config)
        for check in check_node_npu_capacity(
            kube_context=kube_context,
            namespace=namespace,
            per_node_requirement=npu_requirement["per_node"],
            machine=machine,
            kubectl=kubectl_runner,
        ):
            if not runner.append(check):
                return _configure_failed(runner, namespace=namespace)

    bundle_files = {f"manifests/{path.name}": path for path in manifest_paths}
    bundle_files["user_config.json"] = staged_config / "user_config.json"
    env_path = staged_config / "env.json"
    if env_path.exists():
        bundle_files["env.json"] = env_path
    bundle_meta = create_config_bundle(
        config_fingerprint=fingerprint,
        bundle_files=bundle_files,
        metadata={
            "namespace": namespace,
            "job_id": deploy_config["job_id"],
            "manifest_files": manifest_files,
            "workload_names": workload_names,
            "machine_paths": machine_paths,
            "parity_path_refs": parity_path_refs,
            "injector_version": MANIFEST_INJECTOR_VERSION,
            "deployer_version": deployer_version_token(),
        },
    )
    return {
        "ready": True,
        "checks": runner.checks,
        "warnings": runner.warnings,
        "errors": runner.errors,
        "config_fingerprint": fingerprint,
        "bundle_digest": bundle_meta["bundle_digest"],
        "bundle_dir": bundle_meta["bundle_dir"],
        "namespace": namespace,
        "job_id": deploy_config["job_id"],
        "manifest_files": manifest_files,
        "reused": False,
    }


def load_config_bundle(bundle_dir: Path) -> dict[str, Any]:
    bundle_json = bundle_dir / "bundle.json"
    if not bundle_json.exists():
        raise WorkspaceStateError(f"bundle.json missing in {bundle_dir}")
    data = json.loads(bundle_json.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise WorkspaceStateError(f"{bundle_json} must contain an object")
    return data


def _apply_injected_overlay(
    *,
    manifest_dir: Path,
    machine: dict[str, Any],
    kube_context: str,
    namespace: str,
    kubectl: KubectlRunner | None = None,
) -> dict[str, Any]:
    """Idempotently apply the injected (overlay) manifests from a config bundle.

    These are the injector copies of the upstream YAMLs (hostPath/PYTHONPATH,
    PD anti-affinity, nodePort overrides). Applying them on top of the upstream
    deployment triggers a rolling update so pods pick up the shared source tree.
    """
    run_kubectl = _resolve_kubectl_runner(
        machine=machine,
        kube_context=kube_context,
        kubectl=kubectl,
    )
    results: list[dict[str, Any]] = []
    manifests = sorted(manifest_dir.glob("*.yaml"))
    with stage_remote_files(machine, manifests, prefix="mws-apply-overlay") as staged:
        for manifest in manifests:
            result = run_kubectl("apply", "-f", staged[manifest], "-n", namespace)
            results.append(
                {
                    "manifest": manifest.name,
                    "bytes_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                    "returncode": result.returncode,
                    "stdout": result.stdout[-2000:],
                    "stderr": result.stderr[-2000:],
                }
            )
    ok = all(item["returncode"] == 0 for item in results) if results else False
    return {"status": "ok" if ok else "error", "apply_results": results}


def _apply_bundle_direct(
    *,
    manifest_dir: Path,
    machine: dict[str, Any],
    kube_context: str,
    namespace: str,
    kubectl: KubectlRunner | None = None,
) -> dict[str, Any]:
    """Fallback: apply bundle manifests directly when the upstream deployer is unavailable."""
    run_kubectl = _resolve_kubectl_runner(
        machine=machine,
        kube_context=kube_context,
        kubectl=kubectl,
    )
    results: list[dict[str, Any]] = []
    manifests = sorted(manifest_dir.glob("*.yaml"))
    with stage_remote_files(machine, manifests, prefix="mws-apply-bundle") as staged:
        for manifest in manifests:
            result = run_kubectl("apply", "-f", staged[manifest], "-n", namespace)
            results.append(
                {
                    "manifest": manifest.name,
                    "bytes_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                    "returncode": result.returncode,
                    "stdout": result.stdout[-2000:],
                    "stderr": result.stderr[-2000:],
                }
            )
    ok = all(item["returncode"] == 0 for item in results) if results else False
    return {"status": "ok" if ok else "error", "apply_results": results}


def apply_config_bundle(
    *,
    bundle_dir: Path,
    machine: dict[str, Any],
    kube_context: str,
    namespace: str,
    kubectl: KubectlRunner | None = None,
) -> dict[str, Any]:
    """Deploy a config bundle.

    Primary path: run the full upstream deployment on the machine host
    (ConfigMap/env generation, apply and log collection are owned by the upstream
    deployer), then overlay the injected manifests so the rolling update lands
    pods on the shared source tree. Falls back to a direct apply of the bundle
    only when the upstream deployer itself is unavailable; an upstream deploy
    failure is reported as an error rather than silently downgraded.
    """
    manifest_dir = bundle_dir / "manifests"
    if not manifest_dir.exists():
        raise WorkspaceStateError(f"bundle manifests missing: {manifest_dir}")

    deploy = run_deploy_full(bundle_dir, machine=machine)
    if deploy.get("status") == "ok":
        overlay = _apply_injected_overlay(
            manifest_dir=manifest_dir,
            machine=machine,
            kube_context=kube_context,
            namespace=namespace,
            kubectl=kubectl,
        )
        return {
            "status": overlay["status"],
            "upstream_deploy": deploy,
            "overlay": overlay,
            "apply_results": overlay.get("apply_results", []),
            "fallback": False,
        }

    if "deployer not found" in str(deploy.get("reason", "")):
        fallback = _apply_bundle_direct(
            manifest_dir=manifest_dir,
            machine=machine,
            kube_context=kube_context,
            namespace=namespace,
            kubectl=kubectl,
        )
        return {
            "status": fallback["status"],
            "upstream_deploy": deploy,
            "apply_results": fallback.get("apply_results", []),
            "fallback": True,
        }

    return {
        "status": "error",
        "upstream_deploy": deploy,
        "errors": [
            deploy.get("stderr_tail") or deploy.get("reason") or "upstream deploy failed"
        ],
        "fallback": False,
    }


RUNTIME_MODULES = (
    ("motor", "motor_source"),
    ("vllm", "vllm_source"),
    ("vllm_ascend", "vllm_ascend_source"),
)


def bundle_to_plan(bundle_dir: Path, bundle_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convert an immutable config bundle into a deploy plan dict for restart/stop."""
    meta = bundle_meta if bundle_meta is not None else load_config_bundle(bundle_dir)
    manifest_files = [
        relative_repo(path)
        for path in sorted((bundle_dir / "manifests").glob("*.yaml"))
    ]
    return {
        "namespace": str(meta.get("namespace", "")),
        "job_id": meta.get("job_id", ""),
        "manifest_files": manifest_files or list(meta.get("manifest_files", [])),
        "workload_names": list(meta.get("workload_names", [])),
        "machine_paths": dict(meta.get("machine_paths", {})),
        "bundle_dir": relative_repo(bundle_dir),
    }


def verify_bundle_digest(bundle_dir: Path, expected_digest: str) -> None:
    bundle_meta = load_config_bundle(bundle_dir)
    stored = str(bundle_meta.get("bundle_digest", ""))
    if expected_digest and stored != expected_digest:
        raise WorkspaceStateError("bundle_digest mismatch for config run")
    staged_paths: dict[str, Path] = {}
    manifest_dir = bundle_dir / "manifests"
    if manifest_dir.exists():
        staged_paths.update({f"manifests/{p.name}": p for p in sorted(manifest_dir.glob("*.yaml"))})
    for extra in ("user_config.json", "env.json"):
        extra_path = bundle_dir / extra
        if extra_path.exists():
            staged_paths[extra] = extra_path
    if staged_paths and stored:
        current = bundle_digest_for_files(staged_paths)
        if current != stored:
            raise WorkspaceStateError("config bundle content was modified or fingerprint collision detected")


def _pick_runtime_pod(kubectl: KubectlRunner, namespace: str) -> str | None:
    result = kubectl("get", "pods", "-n", namespace, "-o", "json")
    if result.returncode:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    for pod in data.get("items", []):
        phase = pod.get("status", {}).get("phase")
        conditions = pod.get("status", {}).get("conditions", [])
        ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)
        if phase == "Running" and ready:
            return str(pod.get("metadata", {}).get("name", "")) or None
    return None


def verify_min_service_access(
    *,
    machine: dict[str, Any],
    kube_context: str,
    namespace: str,
    kubectl: KubectlRunner | None = None,
) -> dict[str, Any]:
    run_kubectl = _resolve_kubectl_runner(
        machine=machine,
        kube_context=kube_context,
        kubectl=kubectl,
    )
    result = run_kubectl("get", "endpoints", "-n", namespace, "-o", "json")
    if result.returncode:
        return {
            "name": "min_service_access",
            "status": "error",
            "message": result.stderr.strip() or "endpoints lookup failed",
        }
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"name": "min_service_access", "status": "error", "message": "invalid endpoints json"}
    items = data.get("items", [])
    ready_endpoints = [
        item.get("metadata", {}).get("name", "")
        for item in items
        if item.get("subsets")
    ]
    if not ready_endpoints:
        return {
            "name": "min_service_access",
            "status": "error",
            "message": f"no ready endpoints in namespace {namespace!r}",
        }
    return {
        "name": "min_service_access",
        "status": "ok",
        "message": f"endpoints ready: {', '.join(ready_endpoints)}",
    }


def collect_runtime_code_paths(
    *,
    machine: dict[str, Any],
    kube_context: str,
    namespace: str,
    pod_name: str | None = None,
    kubectl: KubectlRunner | None = None,
) -> dict[str, Any]:
    run_kubectl = _resolve_kubectl_runner(
        machine=machine,
        kube_context=kube_context,
        kubectl=kubectl,
    )
    target_pod = pod_name or _pick_runtime_pod(run_kubectl, namespace)
    if not target_pod:
        return {"status": "error", "reason": "no ready pod found", "paths": {}}
    paths: dict[str, str] = {}
    errors: list[str] = []
    for module, _ in RUNTIME_MODULES:
        args = [
            "exec",
            "-n",
            namespace,
            target_pod,
            "--",
            "python3",
            "-c",
            f"import {module}; print(getattr({module}, '__file__', ''))",
        ]
        result = run_kubectl(*args)
        value = result.stdout.strip()
        if result.returncode or not value:
            errors.append(f"{module}: {result.stderr.strip() or 'missing __file__'}")
            continue
        paths[module] = value
    status = "ok" if paths and not errors else "error"
    return {"status": status, "pod": target_pod, "paths": paths, "errors": errors}


def verify_runtime_code_paths(
    collected: dict[str, Any],
    machine_paths: dict[str, str],
) -> dict[str, Any]:
    paths = collected.get("paths", {})
    mismatches: list[str] = []
    for module, path_key in RUNTIME_MODULES:
        actual = str(paths.get(module, ""))
        expected_root = str(machine_paths.get(path_key, ""))
        if not actual:
            mismatches.append(f"{module}: missing runtime path")
            continue
        if expected_root and not actual.startswith(expected_root):
            mismatches.append(f"{module}: {actual!r} does not start with {expected_root!r}")
    if mismatches:
        return {
            "name": "runtime_code_paths",
            "status": "error",
            "message": "; ".join(mismatches),
            "paths": paths,
        }
    return {
        "name": "runtime_code_paths",
        "status": "ok",
        "message": "runtime modules load from fixed shared paths",
        "paths": paths,
    }


def restart_deploy_workloads_from_context(
    plan: dict[str, Any],
    *,
    machine: dict[str, Any],
    kube_context: str,
) -> dict[str, Any]:
    return restart_deploy_workloads(plan, machine, kube_context=kube_context)


def stop_from_bundle(
    bundle_dir: Path,
    *,
    machine: dict[str, Any],
    kube_context: str,
    namespace: str,
) -> dict[str, Any]:
    plan = bundle_to_plan(bundle_dir)
    plan["namespace"] = namespace or plan.get("namespace", "")
    return stop_from_plan(plan, machine, kube_context=kube_context)


def pod_readiness_from_context(
    machine: dict[str, Any],
    kube_context: str,
    namespace: str,
) -> dict[str, Any]:
    return pod_readiness_probe(machine, namespace, kube_context=kube_context)
