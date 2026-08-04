from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

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
    }
    base.update(overrides)
    return base


def _kubectl_side_effect(
    *,
    has_inferservicesets: bool = True,
    has_ascendjobs: bool = True,
    has_operator: bool = True,
):
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


def _run(monkeypatch, *, deploy_mode: str | None = None, side_effect=None):
    runner_patch, avail_patch = _patch_kubectl(side_effect or _kubectl_side_effect())
    with runner_patch, avail_patch:
        return run_environment_preflight_checks(
            machine=_machine(),
            machine_ready=_machine_ready(),
            contract=_contract(),
            deploy_mode=deploy_mode,
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
        if "get pods" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="volcano\nascend-device-plugin\n", stderr="")
        if "get nodes" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout='{"huawei.com/Ascend910":"8"}\n', stderr="")
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


def test_no_deploy_mode_records_warning() -> None:
    result = _run(None)
    deploy_check = next(item for item in result["checks"] if item["name"] == "deploy_mode")
    assert deploy_check["status"] == "warning"


def test_deploy_mode_recorded_in_result() -> None:
    result = _run(None, deploy_mode="multi_deployment")
    assert result["deploy_mode"] == "multi_deployment"


def test_infer_service_set_accepts_inferservicesets() -> None:
    result = _run(None, deploy_mode="infer_service_set")
    assert result["ready"] is True
    group = next(item for item in result["checks"] if item["name"] == "api_resource_group:motor_workload_api")
    assert group["status"] == "ok"
    assert group["evidence"] == "inferservicesets"
    op = next(item for item in result["checks"] if item["name"] == "controller_group:motor_operator")
    assert op["status"] == "ok"


def test_infer_service_set_accepts_ascendjobs_alternative() -> None:
    side_effect = _kubectl_side_effect(has_inferservicesets=False, has_ascendjobs=True)
    result = _run(None, deploy_mode="infer_service_set", side_effect=side_effect)
    assert result["ready"] is True
    group = next(item for item in result["checks"] if item["name"] == "api_resource_group:motor_workload_api")
    assert group["evidence"] == "ascendjobs"


def test_infer_service_set_missing_workload_api_fails() -> None:
    side_effect = _kubectl_side_effect(has_inferservicesets=False, has_ascendjobs=False)
    result = _run(None, deploy_mode="infer_service_set", side_effect=side_effect)
    assert result["ready"] is False
    group = next(item for item in result["checks"] if item["name"] == "api_resource_group:motor_workload_api")
    assert group["status"] == "error"


def test_multi_deployment_does_not_require_workload_api() -> None:
    side_effect = _kubectl_side_effect(has_inferservicesets=False, has_ascendjobs=False)
    result = _run(None, deploy_mode="multi_deployment", side_effect=side_effect)
    assert result["ready"] is True
    assert not any(item["name"].startswith("api_resource_group:") for item in result["checks"])
