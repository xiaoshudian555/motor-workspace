from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tarfile
from io import BytesIO
from pathlib import Path

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
REPO_ROOT = SCAFFOLD.parent
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mws_deploy import (  # noqa: E402
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
    create_repo_tarball,
    fanout_nodes,
    sync_workspace_to_remote,
)
from mws_transport import FakeRemoteTransport, SshScpTransport  # noqa: E402


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


def test_build_source_manifest_schema_v2(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "a.py").write_text("a\n", encoding="utf-8")
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "motor", repo)
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "vllm", repo)
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "vllm_ascend", repo)

    def fake_git(args: list[str], path: Path):
        if args[:2] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, "deadbeef\n", "")
        if args[:1] == ["status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:1] == ["diff"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:1] == ["ls-files"]:
            return subprocess.CompletedProcess(args, 0, "a.py\0", "")
        if args[:2] == ["ls-files", "--error-unmatch"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("mws_parity._git", fake_git)
    manifest = build_source_manifest(_machine())
    assert manifest["schema_version"] == 2
    assert manifest["local_content_digest"]
    assert "snapshot_sha256" not in manifest
    assert manifest["machine"]["alias"] == "dev1"


def test_create_repo_tarball_includes_tracked_and_untracked(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "tracked.py").write_text("t\n", encoding="utf-8")
    (repo / "untracked.py").write_text("u\n", encoding="utf-8")
    (repo / "ignored.pyc").write_text("i\n", encoding="utf-8")

    def fake_git(args: list[str], path: Path):
        if args[:1] == ["ls-files"]:
            return subprocess.CompletedProcess(args, 0, "tracked.py\0untracked.py\0", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("mws_parity._git", fake_git)
    data = create_repo_tarball(repo)
    with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as archive:
        names = set(archive.getnames())
    assert names == {"tracked.py", "untracked.py"}


def test_sync_overwrites_and_removes_deleted_files(tmp_path: Path, monkeypatch) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    monkeypatch.setattr("mws_parity.LOCAL_ROOT", state_root)
    monkeypatch.setattr("mws_parity.PARITY_STATE_DIR", state_root / "parity-state")
    _setup_machine_ready_state(monkeypatch, state_root)
    FakeRemoteTransport._shared_parity_locks.clear()

    motor = tmp_path / "motor"
    motor.mkdir()
    (motor / ".git").mkdir()
    (motor / "keep.py").write_text("keep\n", encoding="utf-8")
    (motor / "drop.py").write_text("drop\n", encoding="utf-8")

    def fake_git(args: list[str], path: Path):
        if args[:2] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, "deadbeef\n", "")
        if args[:1] == ["status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:1] == ["diff"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:1] == ["ls-files"]:
            names = sorted(p.name for p in path.iterdir() if p.is_file())
            return subprocess.CompletedProcess(args, 0, "\0".join(names) + ("\0" if names else ""), "")
        if args[:2] == ["ls-files", "--error-unmatch"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("mws_parity._git", fake_git)
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "motor", motor)
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "vllm", motor)
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "vllm_ascend", motor)

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


def test_ssh_upload_streams_bytes_over_stdin(monkeypatch) -> None:
    calls: list[tuple[list[str], dict]] = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("mws_transport.subprocess.run", fake_run)
    transport = SshScpTransport({"host": "dev1", "user": "root", "port": 22})
    transport.upload_bytes("/tmp/demo.bin", b"payload")
    assert all(cmd[0] != "scp" for cmd, _ in calls)
    upload_cmd, upload_kwargs = calls[0]
    assert upload_cmd[0] == "ssh"
    assert "head -c 7" in upload_cmd[-1]
    assert upload_kwargs["input"] == b"payload"


def test_ssh_large_upload_chunks_bytes_over_stdin(monkeypatch) -> None:
    calls: list[tuple[list[str], dict]] = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("mws_transport.subprocess.run", fake_run)
    monkeypatch.setattr(SshScpTransport, "SSH_STDIN_CHUNK_BYTES", 2)
    transport = SshScpTransport({"host": "dev1", "user": "root", "port": 22})
    transport.upload_bytes("/tmp/demo.bin", b"payload")
    upload_calls = [call for call in calls if "head -c" in call[0][-1]]
    assert len(upload_calls) == 4
    assert all(len(call[1]["input"]) <= 2 for call in upload_calls)
    assert any("cat" in cmd[-1] for cmd, _ in calls)
    assert all(cmd[0] != "scp" for cmd, _ in calls)


def test_ssh_command_is_quoted() -> None:
    transport = SshScpTransport({"host": "dev1", "user": "root", "port": 22})
    script = "echo 'hello world'"
    cmd = transport._ssh(script)
    assert cmd[-3:] == ["bash", "-c", shlex.quote(script)]


def test_sync_failure_does_not_report_ok(tmp_path: Path, monkeypatch) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    monkeypatch.setattr("mws_parity.LOCAL_ROOT", state_root)
    monkeypatch.setattr("mws_parity.PARITY_STATE_DIR", state_root / "parity-state")
    _setup_machine_ready_state(monkeypatch, state_root)
    FakeRemoteTransport._shared_parity_locks.clear()

    motor = tmp_path / "motor"
    motor.mkdir()
    (motor / ".git").mkdir()
    (motor / "file.py").write_text("x\n", encoding="utf-8")

    def fake_git(args: list[str], path: Path):
        if args[:1] == ["ls-files"]:
            return subprocess.CompletedProcess(args, 0, "file.py\0", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("mws_parity._git", fake_git)
    monkeypatch.setattr(
        "mws_parity.build_source_manifest",
        lambda machine: {"schema_version": 2, "machine": machine.get("alias")},
    )
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "motor", motor)
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "vllm", motor)
    monkeypatch.setitem(sys.modules["mws_parity"].REPO_DIRS, "vllm_ascend", motor)

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
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        stdout = "deployment.apps/demo\n" if "get deployment" in " ".join(cmd) else ""
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    monkeypatch.setattr("mws_deploy.shutil.which", lambda _: "/usr/bin/kubectl")
    monkeypatch.setattr("mws_deploy.subprocess.run", fake_run)
    plan = {"namespace": "motor-dev", "workload_names": ["deployment/demo"]}
    profile = {"kubernetes": {}}
    result = restart_deploy_workloads(plan, profile)
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
