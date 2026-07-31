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
        "schema_version": 1,
        "name": "test-contract",
        "required_api_resources": [
            {"name": "ascendjobs", "api_group": "mindx.huawei.com"},
            {"name": "podgroups", "api_group": "scheduling.volcano.sh"},
        ],
        "component_patterns": ["volcano", "ascend-device-plugin"],
        "npu_resource_name": "huawei.com/Ascend910",
    }
    base.update(overrides)
    return base


def _kubectl_side_effect(cmd, **kwargs):
    joined = " ".join(cmd)
    if "cluster-info" in joined:
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="Kubernetes control plane\n", stderr="")
    if "version" in joined:
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout='{"serverVersion":{"gitVersion":"v1.29"}}', stderr="")
    if "auth can-i list customresourcedefinitions" in joined:
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="yes\n", stderr="")
    if "api-resources" in joined and "mindx.huawei.com" in joined:
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ascendjobs\n", stderr="")
    if "api-resources" in joined and "scheduling.volcano.sh" in joined:
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="podgroups\n", stderr="")
    if "get pods -A" in joined:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="volcano-scheduler-abc\nascend-device-plugin-xyz\n",
            stderr="",
        )
    if "get nodes" in joined:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout='{"huawei.com/Ascend910":"8"}\n',
            stderr="",
        )
    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


def test_missing_kubectl_errors() -> None:
    with patch("mws_environment.shutil.which", return_value=None):
        result = run_environment_preflight_checks(
            machine=_machine(),
            machine_ready=_machine_ready(),
            contract=_contract(),
        )
    assert result["ready"] is False
    assert result["checks"][0]["status"] == "error"


def test_missing_kube_context_errors() -> None:
    with patch("mws_environment.shutil.which", return_value="/usr/bin/kubectl"):
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

    with patch("mws_environment.shutil.which", return_value="/usr/bin/kubectl"):
        with patch("mws_environment._run_kubectl", side_effect=fake_run):
            result = run_environment_preflight_checks(
                machine=_machine(),
                machine_ready=_machine_ready(),
                contract=_contract(),
            )
    assert result["ready"] is False
    assert result["stopped_at"] == "kubernetes_api"


def test_api_resource_missing_fails() -> None:
    def fake_run(cmd, **kwargs):
        joined = " ".join(cmd)
        if "cluster-info" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok\n", stderr="")
        if "version" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="{}", stderr="")
        if "auth can-i" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="yes\n", stderr="")
        if "api-resources" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        if "get pods" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="volcano\nascend-device-plugin\n", stderr="")
        if "get nodes" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout='{"huawei.com/Ascend910":"8"}\n', stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch("mws_environment.shutil.which", return_value="/usr/bin/kubectl"):
        with patch("mws_environment._run_kubectl", side_effect=fake_run):
            result = run_environment_preflight_checks(
                machine=_machine(),
                machine_ready=_machine_ready(),
                contract=_contract(),
            )
    assert result["ready"] is False
    assert any(item["name"] == "api_resource:ascendjobs" and item["status"] == "error" for item in result["checks"])


def test_warning_on_version_probe_failure() -> None:
    def fake_run(cmd, **kwargs):
        joined = " ".join(cmd)
        if "version" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="forbidden")
        return _kubectl_side_effect(cmd, **kwargs)

    with patch("mws_environment.shutil.which", return_value="/usr/bin/kubectl"):
        with patch("mws_environment._run_kubectl", side_effect=fake_run):
            result = run_environment_preflight_checks(
                machine=_machine(),
                machine_ready=_machine_ready(),
                contract=_contract(),
            )
    assert result["ready"] is True
    assert any(item["name"] == "cluster_version" and item["status"] == "warning" for item in result["checks"])


def test_success_path() -> None:
    with patch("mws_environment.shutil.which", return_value="/usr/bin/kubectl"):
        with patch("mws_environment._run_kubectl", side_effect=_kubectl_side_effect):
            result = run_environment_preflight_checks(
                machine=_machine(),
                machine_ready=_machine_ready(),
                contract=_contract(),
            )
    assert result["ready"] is True
    assert "namespace" not in result
