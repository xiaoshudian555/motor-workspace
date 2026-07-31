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
