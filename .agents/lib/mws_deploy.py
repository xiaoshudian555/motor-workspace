#!/usr/bin/env python3
"""Motor deployer thin-wrapper helpers."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

from mws_local_state import ROOT, WorkspaceStateError
from mws_lock import resolve_base_image_ref
from mws_machine_target import build_fixed_source_paths, machine_ref, pythonpath_for_machine
from mws_run_state import relative_repo

DEPLOYER_ROOT = ROOT / "motor" / "examples" / "deployer"
DEPLOY_PY = DEPLOYER_ROOT / "deploy.py"
OUTPUT_YAMLS = DEPLOYER_ROOT / "output_yamls"

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


def kubectl_base(profile: dict[str, Any]) -> list[str]:
    args = ["kubectl"]
    context = profile.get("kubernetes", {}).get("context")
    if context:
        args.extend(["--context", str(context)])
    return args


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


def _ensure_mnt_hostpath(pod_spec: dict[str, Any], mount_root: str = "/mnt") -> None:
    volumes = pod_spec.setdefault("volumes", [])
    if not isinstance(volumes, list):
        return
    for volume in volumes:
        if not isinstance(volume, dict):
            continue
        host_path = volume.get("hostPath")
        if isinstance(host_path, dict) and host_path.get("path") == mount_root:
            return
    volumes.append({"name": "mnt", "hostPath": {"path": mount_root}})


def _ensure_mnt_mount(pod_spec: dict[str, Any], mount_root: str = "/mnt") -> None:
    volume_name = None
    for volume in pod_spec.get("volumes", []) or []:
        if not isinstance(volume, dict):
            continue
        host_path = volume.get("hostPath")
        if isinstance(host_path, dict) and host_path.get("path") == mount_root:
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
        if any(isinstance(item, dict) and item.get("mountPath") == mount_root for item in mounts):
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
    namespace = str(deploy.get("namespace") or job_id).strip()
    return {
        "job_id": job_id,
        "namespace": namespace,
        "image_name": str(deploy.get("image_name") or deploy.get("image") or "").strip(),
    }


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


def run_deploy_dry_run(config_dir: Path) -> dict[str, Any]:
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


def process_manifest_documents(
    documents: list[dict[str, Any]],
    *,
    pythonpath: str,
    namespace: str,
    base_image_ref: str,
    mount_root: str = "/mnt",
) -> list[dict[str, Any]]:
    docs = inject_namespace(documents, namespace)
    docs = inject_hostpath_mount(docs, mount_root=mount_root)
    docs = inject_image_ref(docs, base_image_ref)
    docs = inject_pythonpath_env(docs, pythonpath)
    return docs


def process_manifest_file(
    path: Path,
    *,
    pythonpath: str,
    namespace: str,
    base_image_ref: str,
    mount_root: str,
    dest_dir: Path,
) -> Path:
    text = path.read_text(encoding="utf-8")
    docs = load_yaml_documents(text)
    docs = process_manifest_documents(
        docs,
        pythonpath=pythonpath,
        namespace=namespace,
        base_image_ref=base_image_ref,
        mount_root=mount_root,
    )
    out = dest_dir / path.name
    out.write_text(dump_yaml_documents(docs), encoding="utf-8")
    return out


def kubectl_dry_run_and_diff(
    profile: dict[str, Any],
    manifest_paths: list[Path],
    namespace: str,
) -> dict[str, Any]:
    kubectl = kubectl_base(profile)
    if not shutil.which("kubectl"):
        return {"status": "skipped", "reason": "kubectl not found in PATH"}
    results: dict[str, Any] = {"status": "ok", "manifests": []}
    for manifest in manifest_paths:
        item: dict[str, Any] = {"manifest": relative_repo(manifest)}
        apply_cmd = [*kubectl, "apply", "--dry-run=server", "-f", str(manifest)]
        if namespace:
            apply_cmd.extend(["-n", namespace])
        apply = subprocess.run(apply_cmd, check=False, text=True, capture_output=True)
        item["server_dry_run"] = {
            "returncode": apply.returncode,
            "stdout": apply.stdout[-2000:],
            "stderr": apply.stderr[-2000:],
        }
        diff_cmd = [*kubectl, "diff", "-f", str(manifest)]
        if namespace:
            diff_cmd.extend(["-n", namespace])
        diff = subprocess.run(diff_cmd, check=False, text=True, capture_output=True)
        item["diff"] = {
            "returncode": diff.returncode,
            "stdout": diff.stdout[-2000:],
            "stderr": diff.stderr[-2000:],
        }
        if apply.returncode not in (0,):
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

    deploy_result = run_deploy_dry_run(staged_config)
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
        profile,
        [ROOT / path for path in manifest_files],
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


def apply_from_plan(plan: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    kubectl = kubectl_base(profile)
    namespace = str(plan.get("namespace", ""))
    results: list[dict[str, Any]] = []
    for rel in plan.get("manifest_files", []):
        manifest = ROOT / rel
        cmd = [*kubectl, "apply", "-f", str(manifest)]
        if namespace:
            cmd.extend(["-n", namespace])
        result = subprocess.run(cmd, check=False, text=True, capture_output=True)
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


def stop_from_plan(plan: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    kubectl = kubectl_base(profile)
    namespace = str(plan.get("namespace", ""))
    results: list[dict[str, Any]] = []
    for rel in reversed(plan.get("manifest_files", [])):
        manifest = ROOT / rel
        cmd = [*kubectl, "delete", "--ignore-not-found", "-f", str(manifest)]
        if namespace:
            cmd.extend(["-n", namespace])
        result = subprocess.run(cmd, check=False, text=True, capture_output=True)
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


def restart_deploy_workloads(plan: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    if not shutil.which("kubectl"):
        return {"status": "error", "errors": ["kubectl not found in PATH"]}
    kubectl = kubectl_base(profile)
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
            restart_cmd = [*kubectl, "rollout", "restart", resource, "-n", namespace]
            result = subprocess.run(restart_cmd, check=False, text=True, capture_output=True)
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
            delete_cmd = [
                *kubectl,
                "delete",
                "pods",
                "-n",
                namespace,
                "-l",
                f"job-name={resource.split('/', 1)[1]}",
                "--wait=false",
            ]
            result = subprocess.run(delete_cmd, check=False, text=True, capture_output=True)
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


def pod_readiness_probe(profile: dict[str, Any], namespace: str) -> dict[str, Any]:
    kubectl = kubectl_base(profile)
    cmd = [*kubectl, "get", "pods", "-n", namespace, "-o", "json"]
    result = subprocess.run(cmd, check=False, text=True, capture_output=True)
    if result.returncode:
        return {"ready": False, "error": result.stderr.strip(), "skipped": not shutil.which("kubectl")}
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


def collect_component_status(profile: dict[str, Any], namespace: str) -> dict[str, Any]:
    kubectl = kubectl_base(profile)
    if not shutil.which("kubectl"):
        return {"status": "skipped", "reason": "kubectl not found"}
    components = {
        "pods": [*kubectl, "get", "pods", "-n", namespace, "-o", "wide"],
        "services": [*kubectl, "get", "svc", "-n", namespace, "-o", "wide"],
        "jobs": [*kubectl, "get", "jobs", "-n", namespace, "-o", "wide"],
    }
    out: dict[str, Any] = {"status": "ok", "components": {}}
    for name, cmd in components.items():
        result = subprocess.run(cmd, check=False, text=True, capture_output=True)
        out["components"][name] = {
            "returncode": result.returncode,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-1000:],
        }
        if result.returncode:
            out["status"] = "warning"
    return out


def openai_smoke(profile: dict[str, Any], namespace: str) -> dict[str, Any]:
    if os.environ.get("MWS_SKIP_OPENAI_SMOKE") == "1":
        return {"status": "skipped", "reason": "MWS_SKIP_OPENAI_SMOKE=1"}
    if not shutil.which("kubectl"):
        return {"status": "skipped", "reason": "kubectl not found"}
    return {
        "status": "skipped",
        "reason": "OpenAI smoke requires live Coordinator endpoint; run manually after pods ready",
    }


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
