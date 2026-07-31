from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
REPO_ROOT = SCAFFOLD.parent
LIB = SCAFFOLD / ".agents" / "lib"
sys.path.insert(0, str(LIB))

from mws_local_state import WorkspaceStateError  # noqa: E402
from mws_run_state import (  # noqa: E402
    RUN_KINDS,
    create_config_bundle,
    digest_json,
    load_run,
    new_run_id,
    run_record_path,
    validate_upstream_refs,
    write_run,
)
from mws_state import atomic_write_json  # noqa: E402


@pytest.fixture()
def local_state_root(tmp_path, monkeypatch):
    monkeypatch.setattr("mws_local_state.LOCAL_ROOT", tmp_path, raising=False)
    monkeypatch.setattr("mws_local_state.ROOT", REPO_ROOT, raising=False)
    monkeypatch.setattr("mws_run_state.LOCAL_ROOT", tmp_path, raising=False)
    monkeypatch.setattr("mws_run_state.ROOT", REPO_ROOT, raising=False)
    monkeypatch.setattr("mws_run_state.CONFIG_BUNDLES_DIR", tmp_path / "config-bundles", raising=False)
    return tmp_path


def test_run_kinds_cover_six_types() -> None:
    assert len(RUN_KINDS) == 6


def test_write_run_is_immutable(local_state_root) -> None:
    run_id = new_run_id("machine")
    write_run(
        "machine-ready",
        run_id,
        {"status": "ready", "workflow_run_id": "wf-1"},
    )
    with pytest.raises(WorkspaceStateError, match="immutable"):
        write_run(
            "machine-ready",
            run_id,
            {"status": "ready", "workflow_run_id": "wf-1"},
        )


def test_load_run_rejects_wrong_kind(local_state_root) -> None:
    run_id = new_run_id("machine")
    path = run_record_path("machine-ready", run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        path,
        {
            "kind": "parity-complete",
            "run_id": run_id,
            "status": "ready",
            "workflow_run_id": "wf-1",
        },
    )
    with pytest.raises(WorkspaceStateError, match="kind mismatch"):
        load_run("machine-ready", run_id)


def test_validate_upstream_refs_fail_closed(local_state_root) -> None:
    upstream_id = new_run_id("machine")
    write_run(
        "machine-ready",
        upstream_id,
        {"status": "ready", "workflow_run_id": "wf-a"},
    )
    with pytest.raises(WorkspaceStateError, match="another workflow"):
        validate_upstream_refs(
            [{"kind": "machine-ready", "run_id": upstream_id}],
            workflow_run_id="wf-b",
        )


def test_config_bundle_reuse_and_tamper_detection(local_state_root, tmp_path) -> None:
    src = tmp_path / "manifest.yaml"
    src.write_text("apiVersion: v1\nkind: ConfigMap\n", encoding="utf-8")
    fingerprint = digest_json({"user_config": {"job_id": "ns1"}})
    first = create_config_bundle(
        config_fingerprint=fingerprint,
        bundle_files={"manifest.yaml": src},
        metadata={"injector_version": "v1"},
    )
    second = create_config_bundle(
        config_fingerprint=fingerprint,
        bundle_files={"manifest.yaml": src},
        metadata={"injector_version": "v1"},
    )
    assert first["bundle_digest"] == second["bundle_digest"]

    bundle_root = local_state_root / "config-bundles" / fingerprint
    tampered = bundle_root / "manifest.yaml"
    tampered.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(WorkspaceStateError, match="modified"):
        create_config_bundle(
            config_fingerprint=fingerprint,
            bundle_files={"manifest.yaml": src},
            metadata={"injector_version": "v1"},
        )


def test_concurrent_bundle_create_single_digest(local_state_root, tmp_path) -> None:
    src = tmp_path / "env.json"
    src.write_text(json.dumps({"x": 1}), encoding="utf-8")
    fingerprint = digest_json({"env": 1})

    def _create() -> str:
        result = create_config_bundle(
            config_fingerprint=fingerprint,
            bundle_files={"env.json": src},
            metadata={},
        )
        return result["bundle_digest"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        digests = list(pool.map(lambda _: _create(), range(4)))
    assert len(set(digests)) == 1
    assert run_record_path("deploy-config-ready", "unused")  # import sanity
