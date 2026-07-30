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


def _profile(**overrides):
    base = {
        "kubernetes": {"context": "ctx-a", "namespace": "motor-ns"},
        "mindcluster": {"required_api_resources": ["ascendjobs", "podgroups"]},
    }
    base.update(overrides)
    return base


def test_kube_context_mismatch_fails() -> None:
    with patch("mws_environment.shutil.which", return_value="/usr/bin/kubectl"):
        with patch(
            "mws_environment.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="yes\n", stderr=""),
        ):
            result = run_environment_preflight_checks(
                machine=_machine(kube_context="ctx-a"),
                profile=_profile(kubernetes={"context": "ctx-b", "namespace": "motor-ns"}),
                include_pod_readiness=False,
            )
    assert result["ready"] is False
    assert any(item["name"] == "kube_context_consistency" for item in result["checks"])


def test_missing_kubectl_fails() -> None:
    with patch("mws_environment.shutil.which", return_value=None):
        result = run_environment_preflight_checks(
            machine=_machine(),
            profile=_profile(),
            include_pod_readiness=False,
        )
    assert result["ready"] is False
    assert "kubectl" in result["errors"][0]


def test_api_resource_missing_fails() -> None:
    def fake_run(cmd, **kwargs):
        if "api-resources" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="yes\n", stderr="")

    with patch("mws_environment.shutil.which", return_value="/usr/bin/kubectl"):
        with patch("mws_environment.subprocess.run", side_effect=fake_run):
            result = run_environment_preflight_checks(
                machine=_machine(),
                profile=_profile(),
                include_pod_readiness=False,
            )
    assert result["ready"] is False
    assert any(item["name"] == "api_resource:ascendjobs" and item["status"] == "fail" for item in result["checks"])
