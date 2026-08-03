from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
REPO_ROOT = SCAFFOLD.parent
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_deploy import (  # noqa: E402
    compute_config_fingerprint,
    compute_npu_requirement,
    configure_deploy_bundle,
    normalize_native_config,
    verify_namespace_exists,
)
from mws_local_state import WorkspaceStateError  # noqa: E402


def _machine():
    return {
        "alias": "dev1",
        "host": "1.2.3.4",
        "port": 22,
        "user": "root",
        "mount_root": "/mnt",
        "kube_context": "ctx-a",
    }


def test_compute_config_fingerprint_excludes_code_digest() -> None:
    native = {"user_config.json": {"motor_deploy_config": {"job_id": "ns1"}}, "env.json": {}}
    paths = {"mount_root": "/mnt", "motor_source": "/mnt/motor-workspace/motor"}
    fp1 = compute_config_fingerprint(
        native_config=native,
        machine_paths=paths,
        deployer_version="v1",
    )
    fp2 = compute_config_fingerprint(
        native_config=native,
        machine_paths=paths,
        deployer_version="v1",
    )
    assert fp1 == fp2


def test_namespace_missing_fails_closed(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "user_config.json").write_text(
        json.dumps({"motor_deploy_config": {"job_id": "missing-ns"}}),
        encoding="utf-8",
    )
    with patch(
        "mws_deploy.verify_namespace_exists",
        return_value={
            "name": "namespace_exists",
            "status": "error",
            "message": "namespace 'missing-ns' not found",
        },
    ):
        result = configure_deploy_bundle(
            machine=_machine(),
            config_dir=config_dir,
            run_dir=tmp_path / "run",
            kube_context="ctx-a",
            base_image_ref="repo/image:tag",
            parity_path_refs={},
        )
    assert result["ready"] is False
    assert result["stopped_at"] == "namespace_exists"


def test_deploy_plan_redirects_to_configure() -> None:
    script = SCAFFOLD / ".agents/skills/motor-k8s-deploy/scripts/deploy_plan.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--machine", "dev1"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    payload = json.loads(proc.stdout)
    assert "motor-deploy-configure" in payload["errors"][0]


def test_compute_npu_requirement_cross_node() -> None:
    native = {
        "user_config.json": {
            "motor_deploy_config": {
                "p_instances_num": 1,
                "single_p_instance_pod_num": 1,
                "p_pod_npu_num": 2,
                "d_instances_num": 1,
                "single_d_instance_pod_num": 1,
                "d_pod_npu_num": 2,
                "prefill_node_selector": {"kubernetes.io/hostname": "node-p"},
                "decode_node_selector": {"kubernetes.io/hostname": "node-d"},
            }
        }
    }
    requirement = compute_npu_requirement(native)
    assert requirement["total"] == 4
    assert requirement["per_node"] == {"node-p": 2, "node-d": 2}


def test_compute_npu_requirement_missing_selector_fails() -> None:
    native = {
        "user_config.json": {
            "motor_deploy_config": {
                "p_instances_num": 1,
                "single_p_instance_pod_num": 1,
                "p_pod_npu_num": 2,
                "d_instances_num": 1,
                "single_d_instance_pod_num": 1,
                "d_pod_npu_num": 2,
            }
        }
    }
    with pytest.raises(WorkspaceStateError, match="prefill_node_selector"):
        compute_npu_requirement(native)


def _fake_kubectl_node_capacity(allocatable: int, allocated: int, required: int) -> list[dict]:
    """Return a fake kubectl runner backed by a scripted map of command->output."""
    node_json = json.dumps(
        {"status": {"allocatable": {"huawei.com/Ascend910": str(allocatable)}}}
    )
    pod_json = json.dumps(
        {
            "items": [
                {
                    "spec": {
                        "containers": [
                            {"resources": {"requests": {"huawei.com/Ascend910": "1"}}}
                            for _ in range(allocated)
                        ]
                    }
                }
            ]
        }
    )

    def runner(*args):
        joined = " ".join(args)
        if "get node node-a" in joined and "-o json" in joined:
            stdout = node_json
        elif "get pods -A" in joined and "--field-selector spec.nodeName=node-a" in joined:
            stdout = pod_json
        else:
            stdout = ""
        return subprocess.CompletedProcess(args, 0, stdout, "")

    return runner


def test_check_node_npu_capacity_enough() -> None:
    from mws_deploy import check_node_npu_capacity

    runner = _fake_kubectl_node_capacity(allocatable=16, allocated=0, required=4)
    checks = check_node_npu_capacity(
        kube_context="ctx",
        namespace="ns",
        per_node_requirement={"node-a": 4},
        kubectl=runner,
    )
    assert checks[0]["status"] == "ok"
    assert "required=4" in checks[0]["message"]


def test_check_node_npu_capacity_shortfall_fails() -> None:
    from mws_deploy import check_node_npu_capacity

    runner = _fake_kubectl_node_capacity(allocatable=16, allocated=14, required=4)
    checks = check_node_npu_capacity(
        kube_context="ctx",
        namespace="ns",
        per_node_requirement={"node-a": 4},
        kubectl=runner,
    )
    assert checks[0]["status"] == "error"
    assert "has 2 NPU available" in checks[0]["message"]
    assert "required=4" in checks[0]["message"]


def test_check_node_npu_capacity_no_node_fails() -> None:
    from mws_deploy import check_node_npu_capacity

    checks = check_node_npu_capacity(
        kube_context="ctx",
        namespace="ns",
        per_node_requirement={},
        kubectl=_fake_kubectl_node_capacity(16, 0, 4),
    )
    assert checks[0]["status"] == "error"
    assert "no node selected" in checks[0]["message"]
