from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
LIB = SCAFFOLD / ".agents" / "lib"
SKILL_SCRIPTS = SCAFFOLD / ".agents" / "skills" / "machine-management" / "scripts"

sys.path.insert(0, str(LIB))

from mws_local_state import load_inventory, upsert_machine  # noqa: E402
from mws_self_identify import (  # noqa: E402
    build_native_record,
    detect_hostname,
    find_existing_native_alias,
    sanitize_alias,
)


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
    payload = json_load(stdout[-1])
    return exit_code, payload


def json_load(line: str):
    import json

    return json.loads(line)


@pytest.fixture(autouse=True)
def _probe_defaults(monkeypatch):
    """Stub local host probes so tests are deterministic."""
    monkeypatch.setattr(
        "mws_self_identify.detect_hostname", lambda: "npu-host-01"
    )
    monkeypatch.setattr(
        "mws_self_identify.detect_current_user", lambda: "root"
    )
    monkeypatch.setattr(
        "mws_self_identify.detect_kube_context", lambda: "ctx-native"
    )
    monkeypatch.setattr(
        "mws_self_identify.detect_mount_root", lambda: "/mnt"
    )
    monkeypatch.setattr(
        "mws_self_identify.detect_remote_workspace_root",
        lambda mount_root: f"{mount_root}/motor-workspace",
    )


def test_sanitize_alias_normalizes_hostname() -> None:
    assert sanitize_alias("npu-host-01") == "npu-host-01"
    assert sanitize_alias("192.168.1.10") == "192.168.1.10"
    assert sanitize_alias("") == "native"
    assert sanitize_alias("a") == "axx"
    assert len(sanitize_alias("x" * 100)) <= 63


def test_build_native_record_derives_fixed_paths() -> None:
    record = build_native_record(alias="self-01")
    assert record["executor"] == "native"
    assert record["host"] == "npu-host-01"
    assert record["user"] == "root"
    assert record["mount_root"] == "/mnt"
    assert record["remote_workspace_root"] == "/mnt/motor-workspace"
    assert record["source_dirs"]["motor"] == "/mnt/motor-workspace/motor"
    assert record["source_dirs"]["vllm"] == "/mnt/motor-workspace/vllm"
    assert record["source_dirs"]["vllm_ascend"] == "/mnt/motor-workspace/vllm-ascend"
    assert record["source_dirs"]["python_overlay"] == "/mnt/motor-workspace/python-overlay"
    assert record["kube_context"] == "ctx-native"


def test_validate_native_record_accepts_source_dirs(inventory_paths) -> None:
    from mws_local_state import validate_machine_record

    record = build_native_record(alias="self-01")
    normalized = validate_machine_record(record)
    assert normalized["executor"] == "native"
    assert normalized["source_dirs"]["motor"].startswith("/mnt/motor-workspace/")


def test_script_dry_run_does_not_write_inventory(inventory_paths, tmp_path) -> None:
    module = _load_script_module("machine_self_identify")
    exit_code, payload = _capture_script_main(module, ["--dry-run"])
    assert exit_code == 0
    assert payload["dry_run"] is True
    assert payload["probed"]["alias"] == "npu-host-01"
    assert load_inventory()["machines"] == {}


def test_script_registers_native_machine(inventory_paths) -> None:
    module = _load_script_module("machine_self_identify")
    exit_code, payload = _capture_script_main(module, [])
    assert exit_code == 0
    assert payload["executor"] == "native"
    assert payload["alias"] == "npu-host-01"
    machines = load_inventory()["machines"]
    assert machines["npu-host-01"]["executor"] == "native"


def test_script_reuses_existing_native_alias(inventory_paths) -> None:
    upsert_machine(build_native_record(alias="stable-alias", hostname="npu-host-01"))
    assert find_existing_native_alias("npu-host-01") == "stable-alias"

    module = _load_script_module("machine_self_identify")
    exit_code, payload = _capture_script_main(module, [])
    assert exit_code == 0
    # same host already registered as native -> reuse its alias, do not fork
    assert payload["alias"] == "stable-alias"


def test_script_explicit_alias_overrides(inventory_paths) -> None:
    module = _load_script_module("machine_self_identify")
    exit_code, payload = _capture_script_main(module, ["--alias", "custom"])
    assert exit_code == 0
    assert payload["alias"] == "custom"
    assert load_inventory()["machines"]["custom"]["executor"] == "native"


def test_detect_hostname_real() -> None:
    assert detect_hostname()
