from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
REPO_ROOT = SCAFFOLD.parent
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mws_deploy import (  # noqa: E402
    inject_motor_wheel_dir_env,
    inject_namespace,
    inject_pythonpath_env,
    is_cluster_scoped,
    load_yaml_documents,
    process_manifest_documents,
    render_plan,
    restart_deploy_workloads,
)
from mws_lock import verify_lock  # noqa: E402
from mws_machine_target import build_fixed_source_paths, pythonpath_for_machine  # noqa: E402
from mws_parity import (  # noqa: E402
    build_source_manifest,
    fanout_nodes,
    sync_workspace_to_remote,
)
from mws_transport import FakeRemoteTransport, SshScpTransport  # noqa: E402


from git_fixtures import init_repo  # noqa: E402
from machine_ready_fixtures import write_valid_machine_ready_run  # noqa: E402
from mws_local_state import upsert_machine  # noqa: E402
from mws_parity import load_machine_ready_evidence  # noqa: E402


def _machine() -> dict:
    return {
        "alias": "dev1",
        "host": "dev1",
        "user": "root",
        "port": 22,
        "mount_root": "/mnt",
        "remote_workspace_root": "/mnt/motor-workspace",
        "kube_context": "ctx-a",
        "parity_backend": "shared-hostpath",
    }


def _machine_ready() -> dict:
    write_valid_machine_ready_run(_machine(), run_id="machine-test-1")
    return load_machine_ready_evidence("dev1", machine_run_id="machine-test-1")


def _setup_machine_ready_state(monkeypatch, state_root: Path) -> None:
    inventory_path = state_root / "machine-inventory.json"
    lock_path = inventory_path.with_name(inventory_path.name + ".lock")
    monkeypatch.setattr("mws_local_state.LOCAL_ROOT", state_root)
    monkeypatch.setattr("mws_local_state.INVENTORY_PATH", inventory_path)
    monkeypatch.setattr("mws_local_state.INVENTORY_LOCK_PATH", lock_path)
    monkeypatch.setattr("mws_parity.LOCAL_ROOT", state_root)
    monkeypatch.setattr("mws_parity.MACHINE_RUNS_DIR", state_root / "machine-runs")
    monkeypatch.setattr("mws_run_state.LOCAL_ROOT", state_root)
    upsert_machine(_machine())


def test_fixed_paths_use_machine_workspace_root() -> None:
    paths = build_fixed_source_paths(_machine())
    root = "/mnt/motor-workspace"
    assert paths["motor_source"] == f"{root}/motor"
    assert paths["vllm_source"] == f"{root}/vllm"
    assert paths["vllm_ascend_source"] == f"{root}/vllm-ascend"
    assert paths["python_overlay"] == f"{root}/python-overlay"
    assert "current" not in paths
    assert "snapshots" not in json.dumps(paths)


def test_pythonpath_uses_fixed_paths() -> None:
    machine = _machine()
    value = pythonpath_for_machine(machine)
    assert value.endswith("/python-overlay")
    assert "/current/" not in value


def _bind_repos(monkeypatch, repo: Path) -> None:
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "motor", repo)
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "vllm", repo)
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "vllm_ascend", repo)


def test_build_source_manifest_schema_v2(monkeypatch, tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo", files={"a.py": "a\n"})
    _bind_repos(monkeypatch, repo)
    manifest = build_source_manifest(_machine())
    assert manifest["schema_version"] == 2
    assert manifest["local_content_digest"]
    assert "snapshot_sha256" not in manifest
    assert manifest["machine"]["alias"] == "dev1"


def test_sync_overwrites_and_removes_deleted_files(tmp_path: Path, monkeypatch) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    monkeypatch.setattr("mws_parity.LOCAL_ROOT", state_root)
    monkeypatch.setattr("mws_parity.PARITY_STATE_DIR", state_root / "parity-state")
    _setup_machine_ready_state(monkeypatch, state_root)
    FakeRemoteTransport._shared_parity_locks.clear()

    motor = init_repo(
        tmp_path / "motor", files={"keep.py": "keep\n", "drop.py": "drop\n"}
    )
    _bind_repos(monkeypatch, motor)

    machine = _machine()
    ready = _machine_ready()
    fake_root = tmp_path / "remote"
    sync_workspace_to_remote(
        machine, fake_root=fake_root, machine_ready=ready, skip_fast_path=True
    )
    motor_dir = fake_root / "mnt/motor-workspace/motor"
    assert (motor_dir / "keep.py").exists()
    assert (motor_dir / "drop.py").exists()

    (motor / "drop.py").unlink()
    (motor / "new.py").write_text("new\n", encoding="utf-8")
    sync_workspace_to_remote(
        machine, fake_root=fake_root, machine_ready=ready, skip_fast_path=True
    )
    assert (motor_dir / "keep.py").exists()
    assert (motor_dir / "new.py").exists()
    assert not (motor_dir / "drop.py").exists()


def test_scp_upload_bytes_writes_remote_tmp_and_moves(monkeypatch) -> None:
    calls: list[tuple[list[str], dict]] = []
    observed_payloads: list[bytes] = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[0] == "scp":
            observed_payloads.append(Path(cmd[-2]).read_bytes())
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        # upload_bytes' `self.run(mv ...)` returns text-mode result.
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("mws_transport.subprocess.run", fake_run)
    transport = SshScpTransport({"host": "dev1", "user": "root", "port": 22})
    transport.upload_bytes("/tmp/demo.bin", b"payload")

    scp_calls = [call for call in calls if call[0][0] == "scp"]
    assert len(scp_calls) == 1
    scp_cmd = scp_calls[0][0]
    assert "-P" in scp_cmd
    assert "22" in scp_cmd
    assert scp_cmd[-1].startswith("root@dev1:/tmp/mws-upload-")
    assert scp_cmd[-1].endswith(".bin")
    assert scp_cmd[-2].startswith("/tmp/mws-upload-")
    assert observed_payloads == [b"payload"]
    assert not Path(scp_cmd[-2]).exists()

    move_calls = [call for call in calls if "mv" in call[0][-1]]
    assert len(move_calls) == 1
    assert "/tmp/mws-upload-" in move_calls[0][0][-1]


def test_scp_upload_retries_then_succeeds(monkeypatch) -> None:
    calls: list[tuple[list[str], dict]] = []
    attempts = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[0] == "scp":
            attempts["n"] += 1
            if attempts["n"] < 3:
                return subprocess.CompletedProcess(cmd, 1, b"", b"scp: connection reset")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("mws_transport.subprocess.run", fake_run)
    transport = SshScpTransport({"host": "dev1", "user": "root", "port": 22})
    transport.upload_bytes("/tmp/demo.bin", b"payload")
    scp_calls = [call for call in calls if call[0][0] == "scp"]
    assert len(scp_calls) == 3


def test_ssh_command_is_quoted() -> None:
    transport = SshScpTransport({"host": "dev1", "user": "root", "port": 22})
    script = "echo 'hello world'"
    cmd = transport._ssh(script)
    assert cmd[-3:] == ["bash", "-c", shlex.quote(script)]


def test_run_retries_timeout_then_succeeds(monkeypatch) -> None:
    attempts = {"n": 0}

    def flaky_run(cmd, **kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise subprocess.TimeoutExpired(cmd, 60)
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr("mws_transport.subprocess.run", flaky_run)
    monkeypatch.setattr(SshScpTransport, "SSH_RETRY_BACKOFF_SECONDS", 0)
    transport = SshScpTransport({"host": "dev1", "user": "root", "port": 22})
    result = transport.run("echo hi")
    assert result.returncode == 0
    assert result.stdout == "ok"
    assert attempts["n"] == 3


def test_run_does_not_retry_business_exit_code(monkeypatch) -> None:
    attempts = {"n": 0}

    def failing_run(cmd, **kwargs):
        attempts["n"] += 1
        return subprocess.CompletedProcess(cmd, 1, "", "no such resource")

    monkeypatch.setattr("mws_transport.subprocess.run", failing_run)
    transport = SshScpTransport({"host": "dev1", "user": "root", "port": 22})
    result = transport.run("kubectl get pod x")
    assert result.returncode == 1
    assert attempts["n"] == 1


def test_sync_failure_does_not_report_ok(tmp_path: Path, monkeypatch) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    monkeypatch.setattr("mws_parity.LOCAL_ROOT", state_root)
    monkeypatch.setattr("mws_parity.PARITY_STATE_DIR", state_root / "parity-state")
    _setup_machine_ready_state(monkeypatch, state_root)
    FakeRemoteTransport._shared_parity_locks.clear()

    motor = init_repo(tmp_path / "motor", files={"file.py": "x\n"})
    _bind_repos(monkeypatch, motor)

    class BrokenTransport(FakeRemoteTransport):
        def upload_file(self, local_path: str, remote_path: str) -> None:
            raise RuntimeError("upload failed")

    machine = _machine()
    with pytest.raises(Exception):
        sync_workspace_to_remote(
            machine,
            transport=BrokenTransport(tmp_path / "remote"),
            machine_ready=_machine_ready(),
        )


def test_node_local_fanout_is_unsupported() -> None:
    machine = {"host": "dev1", "parity_backend": "node-local-hostpath", "candidate_nodes": ["n1"]}
    with pytest.raises(Exception):
        fanout_nodes(machine, ["n1"])


def test_cluster_scoped_resource_does_not_get_namespace() -> None:
    docs = [
        {"kind": "Namespace", "metadata": {"name": "demo"}},
        {"kind": "Deployment", "metadata": {"name": "mindie-server"}, "spec": {"template": {"spec": {"containers": []}}}},
    ]
    patched = inject_namespace(docs, "motor-dev")
    assert "namespace" not in patched[0]["metadata"]
    assert patched[1]["metadata"]["namespace"] == "motor-dev"


def test_runtime_container_gets_pythonpath() -> None:
    yaml_text = """
kind: Deployment
metadata:
  name: mindie-server
spec:
  template:
    spec:
      containers:
        - name: mindie-server
          image: mindie:1.0.0
        - name: sidecar
          image: busybox:latest
"""
    docs = load_yaml_documents(yaml_text)
    patched = inject_pythonpath_env(docs, "/mnt/a:/mnt/b")
    containers = patched[0]["spec"]["template"]["spec"]["containers"]
    assert any(item.get("name") == "PYTHONPATH" for item in containers[0].get("env", []))
    sidecar_env = containers[1].get("env", [])
    assert not any(item.get("name") == "PYTHONPATH" for item in sidecar_env)


def test_inject_motor_wheel_dir_env_sets_runtime_container_only() -> None:
    yaml_text = """
kind: Deployment
metadata:
  name: mindie-server
spec:
  template:
    spec:
      containers:
        - name: mindie-server
          image: mindie:1.0.0
        - name: sidecar
          image: busybox:latest
"""
    docs = load_yaml_documents(yaml_text)
    patched = inject_motor_wheel_dir_env(docs, "/mnt/wheel-builds/sha/dist")
    containers = patched[0]["spec"]["template"]["spec"]["containers"]
    assert any(
        item.get("name") == "MOTOR_WHEEL_DIR" and item.get("value") == "/mnt/wheel-builds/sha/dist"
        for item in containers[0].get("env", [])
    )
    sidecar_env = containers[1].get("env", [])
    assert not any(item.get("name") == "MOTOR_WHEEL_DIR" for item in sidecar_env)


def test_process_manifest_documents_motor_wheel_skips_pythonpath() -> None:
    yaml_text = """
kind: Deployment
metadata:
  name: mindie-server
spec:
  template:
    spec:
      containers:
        - name: mindie-server
          image: mindie:1.0.0
"""
    docs = load_yaml_documents(yaml_text)
    patched = process_manifest_documents(
        docs,
        pythonpath="/mnt/motor-workspace/motor:/mnt/motor-workspace/vllm",
        namespace="motor-dev",
        base_image_ref="mindie:test",
        mount_root="/mnt",
        motor_wheel_dir="/mnt/wheel-builds/sha/dist",
    )
    env = patched[0]["spec"]["template"]["spec"]["containers"][0].get("env", [])
    assert any(item.get("name") == "MOTOR_WHEEL_DIR" for item in env)
    assert not any(item.get("name") == "PYTHONPATH" for item in env)


def test_process_manifest_documents_adds_mnt_hostpath_when_missing() -> None:
    yaml_text = """
kind: Deployment
metadata:
  name: mindie-server
spec:
  template:
    spec:
      containers:
        - name: mindie-server
          image: mindie:1.0.0
"""
    docs = load_yaml_documents(yaml_text)
    patched = process_manifest_documents(
        docs,
        pythonpath="/mnt/motor-workspace/motor:/mnt/motor-workspace/vllm",
        namespace="motor-dev",
        base_image_ref="mindie:test",
        mount_root="/mnt",
    )
    pod_spec = patched[0]["spec"]["template"]["spec"]
    assert any(volume.get("hostPath", {}).get("path") == "/mnt" for volume in pod_spec["volumes"])
    assert any(item.get("name") == "PYTHONPATH" for item in pod_spec["containers"][0]["env"])


def test_restart_command_never_uses_delete_all(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_kubectl(*args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("mws_deploy.build_kubectl_runner", lambda *args, **kwargs: fake_kubectl)
    plan = {"namespace": "motor-dev", "workload_names": ["deployment/demo"]}
    result = restart_deploy_workloads(plan, _machine(), kube_context="ctx-a")
    assert result["status"] == "ok"
    joined = json.dumps(calls)
    assert "--all" not in joined


def test_dirty_tree_not_blocked_by_lock() -> None:
    result = verify_lock(require_base_image=False, strict_commits=False)
    assert result["status"] in {"ok", "warning"}
    if result["warnings"]:
        assert all("dirty tree allowed" in warning or "unresolved" in warning for warning in result["warnings"])


def test_render_plan_fails_without_new_manifests(monkeypatch, tmp_path: Path) -> None:
    machine = _machine()
    profile = {"kubernetes": {"namespace": "motor-dev"}}
    run_dir = tmp_path / "plan"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "user_config.json").write_text(
        json.dumps(
            {
                "motor_deploy_config": {
                    "image_name": "mindie:test",
                    "job_id": "job-1",
                    "namespace": "motor-dev",
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("mws_deploy.run_deploy_dry_run", lambda *_args, **_kwargs: {"status": "ok", "generated_files": []})
    with pytest.raises(Exception):
        render_plan(
            machine=machine,
            profile=profile,
            profile_path="profiles/a2-dev.yaml",
            config_dir=config_dir,
            run_dir=run_dir,
            base_image_ref="mindie:test",
        )
