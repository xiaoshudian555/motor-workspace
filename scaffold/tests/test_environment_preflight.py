from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

SCAFFOLD = Path(__file__).resolve().parents[1]
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_environment import run_environment_preflight_checks  # noqa: E402


def _patch_kubectl(side_effect):
    """Patch the kubectl strategy so tests never open real SSH or local kubectl."""

    def fake_runner(*args):
        cmd = ["kubectl"] + list(args)
        return side_effect(cmd)

    return (
        patch("mws_environment.build_kubectl_runner", return_value=fake_runner),
        patch("mws_environment.kubectl_available", return_value=(True, "/usr/bin/kubectl")),
    )


def _machine(**overrides):
    base = {"alias": "dev1", "host": "1.2.3.4", "kube_context": "ctx-a"}
    base.update(overrides)
    return base


def _machine_ready(**overrides):
    base = {"machine_run_id": "machine-1", "alias": "dev1"}
    base.update(overrides)
    return base


def _contract(**overrides):
    base = {
        "schema_version": 2,
        "name": "test-contract",
        "required_api_resources": [
            {"name": "podgroups", "api_group": "scheduling.volcano.sh"},
        ],
        "deploy_mode_api_resources": {
            "infer_service_set": [],
            "multi_deployment": [],
            "single_container": [],
        },
        "deploy_mode_api_resource_groups": {
            "infer_service_set": [
                {
                    "name": "motor_workload_api",
                    "alternatives": [
                        {"name": "inferservicesets", "api_group": "mindcluster.huawei.com"},
                        {"name": "ascendjobs", "api_group": "mindxdl.gitee.com"},
                    ],
                }
            ],
            "multi_deployment": [],
            "single_container": [],
        },
        "component_patterns": ["volcano", "ascend-device-plugin"],
        "deploy_mode_components": {
            "infer_service_set": [],
            "multi_deployment": [],
            "single_container": [],
        },
        "deploy_mode_component_groups": {
            "infer_service_set": [
                {"name": "motor_operator", "alternatives": ["infer-operator", "ascend-operator"]}
            ],
            "multi_deployment": [],
            "single_container": [],
        },
        "npu_resource_name": "huawei.com/Ascend910",
        "node_port_range": [30000, 32767],
    }
    base.update(overrides)
    return base


def _deploy_config(**overrides):
    base = {
        "deploy_mode": "infer_service_set",
        "image_name": "registry.example/motor:latest",
    }
    base.update(overrides)
    return base


def _nodes_json(*schedulable, **extra):
    items = [{"metadata": {"name": n}, "spec": {}} for n in schedulable]
    for node in extra.get("unschedulable", []):
        items.append({"metadata": {"name": node}, "spec": {"unschedulable": True}})
    return json.dumps({"items": items})


def _pods_json(node_images):
    """node_images: dict node -> iterable of image refs seen in running pods."""
    items = []
    for node, images in node_images.items():
        containers = [{"name": "c", "image": img} for img in images]
        items.append({"spec": {"nodeName": node, "containers": containers}})
    return json.dumps({"items": items})


def _services_json(node_ports):
    """node_ports: dict port -> list of service labels."""
    items = []
    for port, labels in node_ports.items():
        items.append(
            {
                "metadata": {"namespace": "ns", "name": labels[0] if labels else "svc"},
                "spec": {"ports": [{"name": "p", "nodePort": port}]},
            }
        )
    return json.dumps({"items": items})


def _kubectl_side_effect(
    *,
    has_inferservicesets: bool = True,
    has_ascendjobs: bool = True,
    has_operator: bool = True,
    schedulable_nodes=("node-a", "node-b"),
    node_images=None,
    service_node_ports=None,
):
    """Parameterized kubectl side effect for the preflight check sequence."""
    node_images = node_images or {"node-a": ["registry.example/motor:latest"]}
    service_node_ports = service_node_ports or {}

    def _output_format(cmd):
        try:
            i = list(cmd).index("-o")
        except ValueError:
            return None
        return list(cmd)[i + 1] if i + 1 < len(cmd) else None

    def run(cmd, **kwargs):
        joined = " ".join(cmd)
        if "cluster-info" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="Kubernetes control plane\n", stderr="")
        if "version" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout='{"serverVersion":{"gitVersion":"v1.29"}}', stderr="")
        if "auth can-i list customresourcedefinitions" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="yes\n", stderr="")
        if "api-resources" in joined and "scheduling.volcano.sh" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="podgroups\n", stderr="")
        if "api-resources" in joined and "mindcluster.huawei.com" in joined:
            out = "inferservicesets\ninferservices\n" if has_inferservicesets else ""
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=out, stderr="")
        if "api-resources" in joined and "mindxdl.gitee.com" in joined:
            out = "ascendjobs\n" if has_ascendjobs else ""
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=out, stderr="")
        if "get services" in joined and _output_format(cmd) == "json":
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=_services_json(service_node_ports), stderr="")
        if "get nodes" in joined and _output_format(cmd) == "json":
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=_nodes_json(*schedulable_nodes), stderr="")
        if "get pods" in joined and _output_format(cmd) == "json":
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=_pods_json(node_images), stderr="")
        if "get pods -A" in joined:
            pods = "volcano-scheduler-abc\nascend-device-plugin-xyz\n"
            if has_operator:
                pods += "infer-operator-manager-abc\n"
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=pods, stderr="")
        if "get nodes" in joined:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout='{"huawei.com/Ascend910":"8"}\n',
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    return run


def _run(deploy_config, *, side_effect=None):
    runner_patch, avail_patch = _patch_kubectl(side_effect or _kubectl_side_effect())
    with runner_patch, avail_patch:
        return run_environment_preflight_checks(
            machine=_machine(),
            machine_ready=_machine_ready(),
            contract=_contract(),
            deploy_config=deploy_config,
        )


def test_missing_kubectl_errors() -> None:
    with patch("mws_environment.kubectl_available", return_value=(False, "kubectl not found in PATH")):
        with patch(
            "mws_environment.build_kubectl_runner",
            return_value=lambda *a: subprocess.CompletedProcess(args=[], returncode=127, stdout="", stderr=""),
        ):
            result = run_environment_preflight_checks(
                machine=_machine(),
                machine_ready=_machine_ready(),
                contract=_contract(),
            )
    assert result["ready"] is False
    assert any(item["name"] == "kubectl" and item["status"] == "error" for item in result["checks"])


def test_missing_kube_context_errors() -> None:
    result = run_environment_preflight_checks(
        machine=_machine(kube_context=""),
        machine_ready=_machine_ready(),
        contract=_contract(),
    )
    assert result["ready"] is False
    assert any(item["name"] == "kube_context" for item in result["checks"])


def test_api_unreachable_short_circuits() -> None:
    def fake_run(cmd, **kwargs):
        if "cluster-info" in " ".join(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="connection refused")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="yes\n", stderr="")

    result = _run(None, side_effect=fake_run)
    assert result["ready"] is False
    assert result["stopped_at"] == "kubernetes_api"


def test_podgroups_missing_fails() -> None:
    def fake_run(cmd, **kwargs):
        joined = " ".join(cmd)
        if "cluster-info" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok\n", stderr="")
        if "version" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="{}", stderr="")
        if "auth can-i" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="yes\n", stderr="")
        if "api-resources" in joined and "scheduling.volcano.sh" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "api-resources" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "get pods -A" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="volcano\nascend-device-plugin\n", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    result = _run(None, side_effect=fake_run)
    assert result["ready"] is False
    assert any(item["name"] == "api_resource:podgroups" and item["status"] == "error" for item in result["checks"])


def test_warning_on_version_probe_failure() -> None:
    def fake_run(cmd, **kwargs):
        if "version" in " ".join(cmd):
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="forbidden")
        return _kubectl_side_effect()(cmd, **kwargs)

    result = _run(None, side_effect=fake_run)
    assert result["ready"] is True
    assert any(item["name"] == "cluster_version" and item["status"] == "warning" for item in result["checks"])


def test_success_path_base_environment() -> None:
    result = _run(None)
    assert result["ready"] is True
    assert "namespace" not in result


def test_no_deploy_config_records_warning() -> None:
    result = _run(None)
    deploy_check = next(item for item in result["checks"] if item["name"] == "deploy_mode")
    assert deploy_check["status"] == "warning"
    assert not any(item["name"].startswith("image_") for item in result["checks"])
    assert not any(item["name"].startswith("node_port_") for item in result["checks"])


def test_deploy_mode_recorded_in_result() -> None:
    result = _run(_deploy_config(deploy_mode="multi_deployment"))
    assert result["deploy_mode"] == "multi_deployment"


def test_infer_service_set_accepts_inferservicesets() -> None:
    result = _run(_deploy_config())
    assert result["ready"] is True
    group = next(item for item in result["checks"] if item["name"] == "api_resource_group:motor_workload_api")
    assert group["status"] == "ok"
    assert group["evidence"] == "inferservicesets"
    op = next(item for item in result["checks"] if item["name"] == "controller_group:motor_operator")
    assert op["status"] == "ok"


def test_infer_service_set_accepts_ascendjobs_alternative() -> None:
    side_effect = _kubectl_side_effect(has_inferservicesets=False, has_ascendjobs=True)
    result = _run(_deploy_config(), side_effect=side_effect)
    assert result["ready"] is True
    group = next(item for item in result["checks"] if item["name"] == "api_resource_group:motor_workload_api")
    assert group["evidence"] == "ascendjobs"


def test_infer_service_set_missing_workload_api_fails() -> None:
    side_effect = _kubectl_side_effect(has_inferservicesets=False, has_ascendjobs=False)
    result = _run(_deploy_config(), side_effect=side_effect)
    assert result["ready"] is False
    group = next(item for item in result["checks"] if item["name"] == "api_resource_group:motor_workload_api")
    assert group["status"] == "error"


def test_multi_deployment_does_not_require_workload_api() -> None:
    side_effect = _kubectl_side_effect(has_inferservicesets=False, has_ascendjobs=False)
    result = _run(_deploy_config(deploy_mode="multi_deployment"), side_effect=side_effect)
    assert result["ready"] is True
    assert not any(item["name"].startswith("api_resource_group:") for item in result["checks"])


# --- image checks ---


def test_image_reference_missing_fails() -> None:
    result = _run(_deploy_config(image_name=""))
    assert result["ready"] is False
    assert any(item["name"] == "image_reference" and item["status"] == "error" for item in result["checks"])


def test_image_reference_without_registry_fails() -> None:
    result = _run(_deploy_config(image_name="motor:latest"))
    assert result["ready"] is False
    assert any(item["name"] == "image_reference" and item["status"] == "error" for item in result["checks"])


def test_image_node_coverage_full_ok() -> None:
    side_effect = _kubectl_side_effect(
        schedulable_nodes=("node-a", "node-b"),
        node_images={
            "node-a": ["registry.example/motor:latest", "volcano:latest"],
            "node-b": ["registry.example/motor:latest"],
        },
    )
    result = _run(_deploy_config(), side_effect=side_effect)
    assert result["ready"] is True
    check = next(item for item in result["checks"] if item["name"] == "image_node_coverage")
    assert check["status"] == "ok"


def test_image_node_coverage_partial_warning() -> None:
    side_effect = _kubectl_side_effect(
        schedulable_nodes=("node-a", "node-b"),
        node_images={"node-a": ["registry.example/motor:latest"]},
    )
    result = _run(_deploy_config(), side_effect=side_effect)
    assert result["ready"] is True
    check = next(item for item in result["checks"] if item["name"] == "image_node_coverage")
    assert check["status"] == "warning"
    assert "node-b" in check.get("evidence", "")


def test_image_node_coverage_zero_warning() -> None:
    side_effect = _kubectl_side_effect(
        schedulable_nodes=("node-a", "node-b"),
        node_images={"node-a": [], "node-b": []},
    )
    result = _run(_deploy_config(), side_effect=side_effect)
    assert result["ready"] is True
    check = next(item for item in result["checks"] if item["name"] == "image_node_coverage")
    assert check["status"] == "warning"
    assert "missing" in check.get("evidence", "")


def test_image_node_coverage_skips_unschedulable_nodes() -> None:
    side_effect = _kubectl_side_effect(
        schedulable_nodes=("node-a",),
        node_images={"node-a": ["registry.example/motor:latest"]},
    )
    result = _run(_deploy_config(), side_effect=side_effect)
    check = next(item for item in result["checks"] if item["name"] == "image_node_coverage")
    assert check["status"] == "ok"


# --- NodePort checks ---


def test_node_port_no_overrides_warning() -> None:
    result = _run(_deploy_config())  # no node_port_overrides key
    check = next(item for item in result["checks"] if item["name"] == "node_port_conflict")
    assert check["status"] == "warning"


def test_node_port_out_of_range_fails() -> None:
    result = _run(_deploy_config(node_port_overrides={"31015": 20000}))
    assert result["ready"] is False
    check = next(item for item in result["checks"] if item["name"] == "node_port_range")
    assert check["status"] == "error"


def test_node_port_duplicate_fails() -> None:
    result = _run(
        _deploy_config(
            node_port_overrides={"31015": 32015, "31017": 32015}
        )
    )
    assert result["ready"] is False
    check = next(item for item in result["checks"] if item["name"] == "node_port_unique")
    assert check["status"] == "error"


def test_node_port_conflict_auto_avoids() -> None:
    side_effect = _kubectl_side_effect(service_node_ports={32015: ["ns/svc-a"]})
    result = _run(
        _deploy_config(node_port_overrides={"31015": 32015, "31017": 32017}),
        side_effect=side_effect,
    )
    assert result["ready"] is True
    check = next(item for item in result["checks"] if item["name"] == "node_port_conflict")
    assert check["status"] == "ok"
    assert "auto-avoided" in check["message"]
    resolved = result.get("node_port_overrides")
    assert resolved is not None
    assert resolved[31015] != 32015  # 被占用端口被避让
    assert resolved[31015] not in (32015, 32017)  # 不与集群占用/本批重复
    assert resolved[31017] == 32017  # 空闲端口保持不变


def test_node_port_no_free_port_fails() -> None:
    # 范围极窄（30000-30002），全部被集群占用，避让失败必须 fail closed
    contract = _contract(node_port_range=[30000, 30002])
    side_effect = _kubectl_side_effect(
        service_node_ports={30000: ["ns/a"], 30001: ["ns/b"], 30002: ["ns/c"]}
    )
    runner_patch, avail_patch = _patch_kubectl(side_effect)
    with runner_patch, avail_patch:
        result = run_environment_preflight_checks(
            machine=_machine(),
            machine_ready=_machine_ready(),
            contract=contract,
            deploy_config=_deploy_config(node_port_overrides={"31015": 30000}),
        )
    assert result["ready"] is False
    check = next(item for item in result["checks"] if item["name"] == "node_port_conflict")
    assert check["status"] == "error"
    assert "no free NodePort" in check["message"]


def test_node_port_all_free_ok() -> None:
    side_effect = _kubectl_side_effect(service_node_ports={31027: ["other/svc"]})
    result = _run(
        _deploy_config(node_port_overrides={"31015": 32015, "31017": 32017}),
        side_effect=side_effect,
    )
    assert result["ready"] is True
    check = next(item for item in result["checks"] if item["name"] == "node_port_conflict")
    assert check["status"] == "ok"
    resolved = result.get("node_port_overrides")
    assert resolved == {31015: 32015, 31017: 32017}


# --- write-back helper ---


def _import_preflight_script():
    import importlib.util

    script = (
        SCAFFOLD
        / ".agents/skills/motor-deploy-preflight/scripts/environment_preflight.py"
    )
    spec = importlib.util.spec_from_file_location("environment_preflight_test", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_apply_node_port_auto_avoid_writes_config(tmp_path) -> None:
    module = _import_preflight_script()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "user_config.json").write_text(
        json.dumps(
            {
                "motor_deploy_config": {
                    "deploy_mode": "infer_service_set",
                    "job_id": "test-job",
                }
            }
        ),
        encoding="utf-8",
    )
    messages: list[str] = []

    result = module._apply_node_port_auto_avoid(
        config_dir=str(config_dir),
        deploy_config={"node_port_overrides": {31015: 32015}},
        resolved_overrides={31015: 32115},
        progress=lambda msg: messages.append(msg),
    )

    assert result == {31015: 32115}
    data = json.loads((config_dir / "user_config.json").read_text(encoding="utf-8"))
    assert data["motor_deploy_config"]["node_port_overrides"] == {"31015": 32115}
    assert data["motor_deploy_config"]["job_id"] == "test-job"  # 其余字段保留
    assert messages and "wrote auto-avoided node_port_overrides" in messages[0]


def test_apply_node_port_auto_avoid_skips_without_config() -> None:
    module = _import_preflight_script()
    result = module._apply_node_port_auto_avoid(
        config_dir="",
        deploy_config={"node_port_overrides": {31015: 32015}},
        resolved_overrides={31015: 32115},
        progress=lambda msg: None,
    )
    assert result is None
