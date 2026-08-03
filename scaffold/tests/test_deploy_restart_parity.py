from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
REPO_ROOT = SCAFFOLD.parent
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))


def _load_script():
    script = SCAFFOLD / ".agents/skills/motor-k8s-deploy/scripts/deploy_restart.py"
    spec = importlib.util.spec_from_file_location("deploy_restart_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _register_machine(inventory_path: Path, *, executor: str) -> None:
    payload = {
        "schema_version": 1,
        "machines": {
            "dev1": {
                "alias": "dev1",
                "host": "npu-host",
                "user": "root",
                "port": 22,
                "mount_root": "/mnt",
                "remote_workspace_root": "/mnt/motor-workspace",
                "kube_context": "ctx-a",
                "parity_backend": "shared-hostpath",
                "executor": executor,
                "candidate_nodes": [],
            }
        },
    }
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_text(json.dumps(payload), encoding="utf-8")


def _run_parity(executor: str):
    """Run deploy_restart.run_parity against a machine record with monkeypatched state."""
    module = _load_script()
    with tempfile.TemporaryDirectory() as tmp:
        state_root = Path(tmp)
        inventory = state_root / "machine-inventory.json"
        _register_machine(inventory, executor=executor)

        fake_out = json.dumps(
            {
                "status": "ready" if executor == "native" else "ok",
                "source_mode": "identity" if executor == "native" else "sync",
                "machine": "dev1",
            }
        )
        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=fake_out, stderr=""
            )

        with patch("mws_local_state.INVENTORY_PATH", inventory):
            with patch("mws_local_state.INVENTORY_LOCK_PATH", Path(str(inventory) + ".lock")):
                with patch("subprocess.run", side_effect=fake_run):
                    result = module.run_parity("dev1")
        return result, captured


def test_run_parity_native_uses_identity_script() -> None:
    result, captured = _run_parity("native")
    assert result["status"] == "ready"
    assert result["source_mode"] == "identity"
    assert "parity_identity.py" in captured["cmd"][1]
    assert "--approved-overwrite" not in captured["cmd"]


def test_run_parity_ssh_uses_sync_script() -> None:
    result, captured = _run_parity("ssh")
    assert result["status"] == "ok"
    assert "parity_sync.py" in captured["cmd"][1]
    assert "--approved-overwrite" in captured["cmd"]
