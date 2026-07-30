from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
LIB = SCAFFOLD / ".agents" / "lib"
SKILL_SCRIPTS = SCAFFOLD / ".agents" / "skills" / "machine-management" / "scripts"

sys.path.insert(0, str(LIB))

from mws_local_state import (  # noqa: E402
    WorkspaceStateError,
    inventory_lock,
    load_inventory,
    remove_machine,
    save_inventory,
    upsert_machine,
    validate_machine_record,
)
from mws_machine_target import run_machine_ready_checks  # noqa: E402
from mws_transport import RemoteTransport  # noqa: E402
from mws_validate import ValidationError, validate_remote_workspace_in_mount  # noqa: E402


def _machine(**overrides) -> dict:
    base = {
        "alias": "dev1",
        "host": "dev1.example",
        "port": 22,
        "user": "root",
        "mount_root": "/mnt",
        "remote_workspace_root": "/mnt/motor-workspace",
        "kube_context": "ctx-a",
        "parity_backend": "shared-hostpath",
        "candidate_nodes": [],
    }
    base.update(overrides)
    return base


class MockReadyTransport(RemoteTransport):
    def __init__(self, *, writable: bool = True, tools: set[str] | None = None) -> None:
        self.writable = writable
        self.tools = tools or {"tar", "mkdir"}
        self.files: dict[str, bytes] = {}

    def run(self, remote_command: str) -> subprocess.CompletedProcess[str]:
        cmd = remote_command.strip()
        if cmd == "echo ok":
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n", stderr="")
        if cmd.startswith("command -v "):
            tool = cmd.split("command -v ", 1)[1].strip().strip("'\"")
            if tool in self.tools:
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=f"/usr/bin/{tool}\n", stderr=""
                )
            return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
        if cmd.startswith("test -d "):
            path = cmd.split("test -d ", 1)[1].strip().strip("'\"")
            ok = path == "/mnt"
            return subprocess.CompletedProcess(args=[], returncode=0 if ok else 1, stdout="", stderr="")
        if cmd.startswith("rm -rf "):
            prefix = cmd.split("rm -rf ", 1)[1].strip().strip("'\"")
            for key in list(self.files):
                if key.startswith(prefix):
                    del self.files[key]
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        if cmd.startswith("mkdir -p "):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    def upload_file(self, local_path: str, remote_path: str) -> None:
        if not self.writable:
            raise WorkspaceStateError("upload blocked")
        self.files[remote_path] = Path(local_path).read_bytes()

    def read_bytes(self, remote_path: str) -> bytes:
        if not self.writable:
            raise WorkspaceStateError("read blocked")
        if remote_path not in self.files:
            raise WorkspaceStateError(f"missing remote file: {remote_path}")
        return self.files[remote_path]

    def upload_bytes(self, remote_path: str, data: bytes) -> None:
        if not self.writable:
            raise WorkspaceStateError("upload blocked")
        self.files[remote_path] = data


@pytest.fixture
def inventory_paths(tmp_path, monkeypatch):
    inventory_path = tmp_path / "machine-inventory.json"
    lock_path = inventory_path.with_name(inventory_path.name + ".lock")
    monkeypatch.setattr("mws_local_state.INVENTORY_PATH", inventory_path, raising=False)
    monkeypatch.setattr("mws_local_state.INVENTORY_LOCK_PATH", lock_path, raising=False)
    return inventory_path


def _load_script_module(name: str):
    path = SKILL_SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _capture_script_main(module, argv: list[str]):
    stdout: list[str] = []

    def fake_print(*args, **kwargs):
        if kwargs.get("file") is sys.stderr:
            return None
        stdout.append(" ".join(str(arg) for arg in args))
        return None

    with patch.object(sys, "argv", [module.__file__, *argv]):
        with patch("builtins.print", side_effect=fake_print):
            exit_code = module.main()
    payload = json.loads(stdout[-1])
    return exit_code, payload


def test_validate_remote_workspace_in_mount_rejects_escape() -> None:
    with pytest.raises(ValidationError):
        validate_remote_workspace_in_mount("/mnt", "/data/other")


def test_validate_machine_record_normalizes_defaults() -> None:
    record = validate_machine_record(_machine(remote_workspace_root=""))
    assert record["remote_workspace_root"] == "/mnt/motor-workspace"


def test_upsert_and_remove_roundtrip(inventory_paths) -> None:
    action, saved = upsert_machine(_machine())
    assert action == "inserted"
    assert saved["alias"] == "dev1"

    action, saved = upsert_machine(_machine(kube_context="ctx-b"))
    assert action == "updated"
    assert saved["kube_context"] == "ctx-b"

    removed = remove_machine("dev1")
    assert removed["alias"] == "dev1"
    assert load_inventory()["machines"] == {}


def test_upsert_rejects_alias_host_conflict(inventory_paths) -> None:
    upsert_machine(_machine(alias="dev1", host="1.1.1.1"))
    with pytest.raises(WorkspaceStateError):
        upsert_machine(_machine(alias="dev2", host="1.1.1.1"))


def test_upsert_allows_host_update_for_existing_alias(inventory_paths) -> None:
    upsert_machine(_machine(alias="dev1", host="1.1.1.1"))
    action, saved = upsert_machine(_machine(alias="dev1", host="2.2.2.2"))
    assert action == "updated"
    assert saved["host"] == "2.2.2.2"


def test_inventory_lock_blocks_concurrent_writers(inventory_paths) -> None:
    upsert_machine(_machine())
    started = threading.Event()
    release = threading.Event()
    errors: list[Exception] = []

    def slow_writer() -> None:
        try:
            with inventory_lock():
                started.set()
                release.wait(timeout=2)
                inventory = load_inventory()
                inventory["machines"]["dev1"]["kube_context"] = "writer-a"
                save_inventory(inventory)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def waiting_writer() -> None:
        try:
            started.wait(timeout=2)
            with inventory_lock():
                inventory = load_inventory()
                inventory["machines"]["dev1"]["kube_context"] = "writer-b"
                save_inventory(inventory)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    first = threading.Thread(target=slow_writer)
    second = threading.Thread(target=waiting_writer)
    first.start()
    second.start()
    started.wait(timeout=2)
    release.set()
    first.join(timeout=3)
    second.join(timeout=3)
    assert not errors
    assert load_inventory()["machines"]["dev1"]["kube_context"] in {"writer-a", "writer-b"}


def test_run_machine_ready_checks_success() -> None:
    result = run_machine_ready_checks(_machine(), MockReadyTransport())
    assert result["ready"] is True
    assert result["endpoint"]["host"] == "dev1.example"
    assert any(item["name"] == "ssh" and item["status"] == "pass" for item in result["checks"])


def test_run_machine_ready_checks_ssh_failure() -> None:
    class BrokenTransport(MockReadyTransport):
        def run(self, remote_command: str) -> subprocess.CompletedProcess[str]:
            if remote_command.strip() == "echo ok":
                return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="denied")
            return super().run(remote_command)

    result = run_machine_ready_checks(_machine(), BrokenTransport())
    assert result["ready"] is False
    assert "denied" in result["errors"][0]


def test_run_machine_ready_checks_mount_not_writable() -> None:
    result = run_machine_ready_checks(_machine(), MockReadyTransport(writable=False))
    assert result["ready"] is False
    assert any(item["name"] == "mount_root" and item["status"] == "fail" for item in result["checks"])


def test_run_machine_ready_checks_remote_workspace_escape() -> None:
    result = run_machine_ready_checks(
        _machine(remote_workspace_root="/data/outside"),
        MockReadyTransport(),
    )
    assert result["ready"] is False
    assert any(item["name"] == "remote_workspace_path" for item in result["checks"])


def test_run_machine_ready_checks_missing_parity_tool() -> None:
    result = run_machine_ready_checks(_machine(), MockReadyTransport(tools={"mkdir"}))
    assert result["ready"] is False
    assert any(item["name"] == "parity_tool:tar" and item["status"] == "fail" for item in result["checks"])


def test_run_machine_ready_checks_includes_fixed_endpoint_evidence() -> None:
    result = run_machine_ready_checks(_machine(), MockReadyTransport())
    ref = result["machine_ref"]
    assert ref["mount_root"] == "/mnt"
    assert ref["remote_workspace_root"] == "/mnt/motor-workspace"
    assert ref["source_dirs"]["motor"] == "/mnt/motor-workspace/motor"
    assert ref["source_dirs"]["vllm_ascend"] == "/mnt/motor-workspace/vllm-ascend"
    assert result["endpoint"]["root"] == "/mnt/motor-workspace"
    assert result["endpoint"]["cwd"] == "/mnt/motor-workspace"
    assert result["endpoint"]["host"] == "dev1.example"
    assert any(item["name"] == "mount_root" and item["status"] == "pass" for item in result["checks"])


def test_run_machine_ready_checks_kube_context_metadata_mismatch() -> None:
    result = run_machine_ready_checks(
        _machine(kube_context="ctx-a"),
        MockReadyTransport(),
        profile_kube_context="ctx-b",
    )
    assert result["ready"] is False
    assert any(
        item["name"] == "kube_context_metadata" and item["status"] == "fail"
        for item in result["checks"]
    )


def test_run_machine_ready_checks_cleanup_failure() -> None:
    class CleanupFailTransport(MockReadyTransport):
        def run(self, remote_command: str) -> subprocess.CompletedProcess[str]:
            cmd = remote_command.strip()
            if cmd.startswith("test -d ") and ".mws-verify-" in cmd:
                return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            return super().run(remote_command)

    result = run_machine_ready_checks(_machine(), CleanupFailTransport())
    assert result["ready"] is False
    assert any(
        item["name"] in {"mount_root", "remote_workspace_root"} and item["status"] == "fail"
        for item in result["checks"]
    )


def test_run_machine_ready_checks_does_not_invoke_kubectl() -> None:
    with patch("subprocess.run", side_effect=AssertionError("kubectl must not run")):
        with patch("shutil.which", side_effect=AssertionError("kubectl lookup must not run")):
            result = run_machine_ready_checks(_machine(), MockReadyTransport())
    assert result["ready"] is True


def test_concurrent_upserts_keep_distinct_records(inventory_paths) -> None:
    errors: list[Exception] = []

    def insert(alias: str, host: str) -> None:
        try:
            upsert_machine(_machine(alias=alias, host=host))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=insert, args=("dev1", "1.1.1.1")),
        threading.Thread(target=insert, args=("dev2", "2.2.2.2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert not errors
    machines = load_inventory()["machines"]
    assert set(machines) == {"dev1", "dev2"}


def test_save_inventory_leaves_valid_json_without_temp_files(inventory_paths) -> None:
    upsert_machine(_machine())
    payload = json.loads(inventory_paths.read_text(encoding="utf-8"))
    assert payload["machines"]["dev1"]["alias"] == "dev1"
    assert list(inventory_paths.parent.glob(f".{inventory_paths.name}.*.tmp")) == []


def test_machine_add_script_registers_record(inventory_paths) -> None:
    module = _load_script_module("machine_add")
    exit_code, payload = _capture_script_main(
        module,
        ["--alias", "dev1", "--host", "1.2.3.4", "--mount-root", "/mnt"],
    )
    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["action"] == "inserted"
    saved = load_inventory()["machines"]["dev1"]
    assert saved["host"] == "1.2.3.4"
    assert saved["remote_workspace_root"] == "/mnt/motor-workspace"


def test_machine_verify_script_updates_diagnostic_metadata_only(inventory_paths, monkeypatch) -> None:
    upsert_machine(_machine(host="1.2.3.4", kube_context="ctx-a"))
    monkeypatch.setattr("mws_transport.transport_for_machine", lambda _: MockReadyTransport())
    module = _load_script_module("machine_verify")
    exit_code, payload = _capture_script_main(module, ["--alias", "dev1"])
    assert exit_code == 0
    assert payload["ready"] is True
    saved = load_inventory()["machines"]["dev1"]
    assert saved["host"] == "1.2.3.4"
    assert saved["kube_context"] == "ctx-a"
    assert saved["last_verified_at"]
    assert saved["last_verify_errors"] == []


def test_machine_verify_script_does_not_touch_kubectl(inventory_paths, monkeypatch) -> None:
    upsert_machine(_machine())
    monkeypatch.setattr("mws_transport.transport_for_machine", lambda _: MockReadyTransport())
    module = _load_script_module("machine_verify")
    with patch("subprocess.run", side_effect=AssertionError("kubectl must not run")):
        exit_code, payload = _capture_script_main(module, ["--alias", "dev1"])
    assert exit_code == 0
    assert payload["ready"] is True


def test_machine_repair_script_only_updates_explicit_fields(inventory_paths, monkeypatch) -> None:
    upsert_machine(_machine(host="1.2.3.4", kube_context="ctx-a"))
    monkeypatch.setattr("mws_transport.transport_for_machine", lambda _: MockReadyTransport())
    module = _load_script_module("machine_repair")
    exit_code, payload = _capture_script_main(module, ["--alias", "dev1"])
    assert exit_code == 0
    assert payload["updated_fields"] == []
    saved = load_inventory()["machines"]["dev1"]
    assert saved["host"] == "1.2.3.4"
    assert "last_repaired_at" not in saved

    exit_code, payload = _capture_script_main(
        module,
        ["--alias", "dev1", "--host", "5.6.7.8", "--kube-context", "ctx-b"],
    )
    assert exit_code == 0
    assert set(payload["updated_fields"]) == {"host", "kube_context"}
    saved = load_inventory()["machines"]["dev1"]
    assert saved["host"] == "5.6.7.8"
    assert saved["kube_context"] == "ctx-b"
    assert saved["last_repaired_at"]


def test_machine_remove_script_only_drops_local_inventory(inventory_paths, monkeypatch) -> None:
    upsert_machine(_machine())
    transport_calls: list[str] = []

    def track_transport(_machine):
        transport = MockReadyTransport()

        def run(remote_command: str) -> subprocess.CompletedProcess[str]:
            transport_calls.append(remote_command)
            return transport.run(remote_command)

        transport.run = run  # type: ignore[method-assign]
        return transport

    monkeypatch.setattr("mws_transport.transport_for_machine", track_transport)
    module = _load_script_module("machine_remove")
    exit_code, payload = _capture_script_main(module, ["--alias", "dev1"])
    assert exit_code == 0
    assert payload["action"] == "removed"
    assert load_inventory()["machines"] == {}
    assert transport_calls == []


def test_inventory_script_list_and_remove(inventory_paths) -> None:
    upsert_machine(_machine())
    module = _load_script_module("inventory")
    exit_code, payload = _capture_script_main(module, ["list"])
    assert exit_code == 0
    assert payload["count"] == 1
    assert "dev1" in payload["machines"]

    exit_code, payload = _capture_script_main(module, ["remove", "dev1"])
    assert exit_code == 0
    assert load_inventory()["machines"] == {}
