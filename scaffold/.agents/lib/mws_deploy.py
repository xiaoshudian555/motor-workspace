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

from mws_local_state import WorkspaceStateError
from repo_paths import MOTOR_ROOT, REPO_ROOT
from mws_lock import resolve_base_image_ref
from mws_machine_target import build_fixed_source_paths, machine_ref, pythonpath_for_machine
from mws_run_state import (
    bundle_digest_for_files,
    create_config_bundle,
    digest_json,
    relative_repo,
)

DEPLOYER_ROOT = MOTOR_ROOT / "examples" / "deployer"
DEPLOY_PY = DEPLOYER_ROOT / "deploy.py"
OUTPUT_YAMLS = DEPLOYER_ROOT / "output_yamls"
MANIFEST_INJECTOR_VERSION = "mws-injector-v1"

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


def apply_from_plan(plan: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    kubectl = kubectl_base(profile)
    namespace = str(plan.get("namespace", ""))
    results: list[dict[str, Any]] = []
    for rel in plan.get("manifest_files", []):
        manifest = REPO_ROOT / rel
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
        manifest = REPO_ROOT / rel
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


def kubectl_base_from_context(kube_context: str = "") -> list[str]:
    args = ["kubectl"]
    context = str(kube_context or "").strip()
    if context:
        args.extend(["--context", context])
    return args


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


def verify_namespace_exists(*, kube_context: str, namespace: str) -> dict[str, Any]:
    kubectl = kubectl_base_from_context(kube_context)
    cmd = [*kubectl, "get", "namespace", namespace, "-o", "name"]
    result = subprocess.run(cmd, check=False, text=True, capture_output=True)
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
) -> list[dict[str, Any]]:
    kubectl = kubectl_base_from_context(kube_context)
    checks: list[dict[str, Any]] = []
    verbs = [
        ("create", "deployments"),
        ("create", "services"),
        ("create", "configmaps"),
    ]
    for verb, resource in verbs:
        cmd = [*kubectl, "auth", "can-i", verb, resource, "-n", namespace]
        result = subprocess.run(cmd, check=False, text=True, capture_output=True)
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
) -> list[dict[str, Any]]:
    if not shutil.which("kubectl"):
        return [
            {
                "name": "server_side_dry_run",
                "status": "unavailable",
                "message": "kubectl not found in PATH",
            }
        ]
    kubectl = kubectl_base_from_context(kube_context)
    checks: list[dict[str, Any]] = []
    for manifest in manifest_paths:
        cmd = [*kubectl, "apply", "--dry-run=server", "-f", str(manifest), "-n", namespace]
        result = subprocess.run(cmd, check=False, text=True, capture_output=True)
        checks.append(
            {
                "name": f"server_side_dry_run:{manifest.name}",
                "status": "ok" if result.returncode == 0 else "error",
                "message": result.stderr.strip() or result.stdout.strip() or manifest.name,
            }
        )
    return checks


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
) -> dict[str, Any]:
    """Render or reuse an immutable deploy config bundle."""
    from mws_result import CheckRunner

    runner = CheckRunner()
    run_dir.mkdir(parents=True, exist_ok=True)
    native_config = normalize_native_config(config_dir)
    deploy_config = load_motor_deploy_config(config_dir)
    namespace = deploy_config["namespace"]
    machine_paths = build_fixed_source_paths(machine)
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

    ns_check = verify_namespace_exists(kube_context=kube_context, namespace=namespace)
    if not runner.append(ns_check):
        return _configure_failed(runner, namespace=namespace)

    staged_config = run_dir / "config"
    patch_user_config_copy(
        source_config_dir=config_dir,
        dest_config_dir=staged_config,
        base_image_ref=base_image_ref,
    )
    runner.append({"name": "stage_native_config", "status": "ok", "message": str(staged_config)})

    deploy_result = run_deploy_dry_run(staged_config)
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
        )
        manifest_paths.append(out)
        manifest_files.append(relative_repo(out))

    for check in verify_manifest_rbac(
        kube_context=kube_context,
        namespace=namespace,
        manifest_paths=manifest_paths,
    ):
        if not runner.append(check):
            return _configure_failed(runner, namespace=namespace)

    for check in kubectl_server_side_dry_run(
        kube_context=kube_context,
        manifest_paths=manifest_paths,
        namespace=namespace,
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


def apply_config_bundle(
    *,
    bundle_dir: Path,
    kube_context: str,
    namespace: str,
) -> dict[str, Any]:
    kubectl = kubectl_base_from_context(kube_context)
    manifest_dir = bundle_dir / "manifests"
    if not manifest_dir.exists():
        raise WorkspaceStateError(f"bundle manifests missing: {manifest_dir}")
    results: list[dict[str, Any]] = []
    for manifest in sorted(manifest_dir.glob("*.yaml")):
        cmd = [*kubectl, "apply", "-f", str(manifest), "-n", namespace]
        result = subprocess.run(cmd, check=False, text=True, capture_output=True)
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


def _pick_runtime_pod(kubectl: list[str], namespace: str) -> str | None:
    cmd = [*kubectl, "get", "pods", "-n", namespace, "-o", "json"]
    result = subprocess.run(cmd, check=False, text=True, capture_output=True)
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


def verify_min_service_access(*, kube_context: str, namespace: str) -> dict[str, Any]:
    kubectl = kubectl_base_from_context(kube_context)
    if not shutil.which("kubectl"):
        return {"name": "min_service_access", "status": "unavailable", "message": "kubectl not found"}
    cmd = [*kubectl, "get", "endpoints", "-n", namespace, "-o", "json"]
    result = subprocess.run(cmd, check=False, text=True, capture_output=True)
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
    kube_context: str,
    namespace: str,
    pod_name: str | None = None,
) -> dict[str, Any]:
    kubectl = kubectl_base_from_context(kube_context)
    if not shutil.which("kubectl"):
        return {"status": "unavailable", "reason": "kubectl not found", "paths": {}}
    target_pod = pod_name or _pick_runtime_pod(kubectl, namespace)
    if not target_pod:
        return {"status": "error", "reason": "no ready pod found", "paths": {}}
    paths: dict[str, str] = {}
    errors: list[str] = []
    for module, _ in RUNTIME_MODULES:
        cmd = [
            *kubectl,
            "exec",
            "-n",
            namespace,
            target_pod,
            "--",
            "python3",
            "-c",
            f"import {module}; print(getattr({module}, '__file__', ''))",
        ]
        result = subprocess.run(cmd, check=False, text=True, capture_output=True)
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
    kube_context: str,
) -> dict[str, Any]:
    profile = {"kubernetes": {"context": kube_context}}
    return restart_deploy_workloads(plan, profile)


def stop_from_bundle(
    bundle_dir: Path,
    *,
    kube_context: str,
    namespace: str,
) -> dict[str, Any]:
    plan = bundle_to_plan(bundle_dir)
    plan["namespace"] = namespace or plan.get("namespace", "")
    profile = {"kubernetes": {"context": kube_context}}
    return stop_from_plan(plan, profile)


def pod_readiness_from_context(kube_context: str, namespace: str) -> dict[str, Any]:
    profile = {"kubernetes": {"context": kube_context}}
    return pod_readiness_probe(profile, namespace)
