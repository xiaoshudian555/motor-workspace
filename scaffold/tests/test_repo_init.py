from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCAFFOLD = Path(__file__).resolve().parents[1]
REPO_ROOT = SCAFFOLD.parent
LIB = SCAFFOLD / ".agents" / "lib"
SKILL_SCRIPTS = SCAFFOLD / ".agents" / "skills" / "repo-init" / "scripts"

sys.path.insert(0, str(LIB))
sys.path.insert(0, str(SKILL_SCRIPTS))

from _repo_init_common import (  # noqa: E402
    REPO_ROLES,
    classify_remote,
    compact_submodule_summary,
    gh_login,
    inspect_repo,
    parse_remote_url,
)
from repo_topology import RepoTopologyError, configure_remotes, remote_names, resolve_repo  # noqa: E402


PROBE = SKILL_SCRIPTS / "repo_init_probe.py"
APPLY = SKILL_SCRIPTS / "repo_init_apply.py"
PROGRESS_PREFIX = "__MWS_PROGRESS__="


def _run_json(script: Path, *args: str, cwd: Path | None = None) -> tuple[subprocess.CompletedProcess[str], dict]:
    result = subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(cwd or REPO_ROOT),
        check=False,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)
    return result, payload


def _init_repo(path: Path, *, initial_file: str = "README.md") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / initial_file).write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "add", initial_file], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), check=True, capture_output=True)


def _add_remote(path: Path, name: str, url: str) -> None:
    subprocess.run(["git", "remote", "add", name, url], cwd=str(path), check=True, capture_output=True)


@pytest.fixture
def bare_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _init_repo(repo)
    return repo


def _child_head(child: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(child), "rev-parse", "HEAD"],
        text=True,
    ).strip()


@pytest.fixture
def workspace_with_submodule(tmp_path: Path) -> Path:
    child = tmp_path / "child"
    _init_repo(child, initial_file="child.txt")
    child_url = f"file://{child.resolve()}"

    workspace = tmp_path / "workspace"
    _init_repo(workspace, initial_file="root.txt")
    (workspace / "sources").mkdir(parents=True, exist_ok=True)
    (workspace / ".gitmodules").write_text(
        '[submodule "child"]\n\tpath = sources/child\n\turl = '
        + child_url
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", ".gitmodules"], cwd=str(workspace), check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "update-index",
            "--add",
            "--cacheinfo",
            "160000",
            _child_head(child),
            "sources/child",
        ],
        cwd=str(workspace),
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "commit", "-m", "add submodule gitlink"], cwd=str(workspace), check=True, capture_output=True)
    return workspace


def test_probe_compact_json_structure() -> None:
    result, payload = _run_json(PROBE, "--compact")
    assert result.returncode in {0, 1}
    assert "submodules" in payload
    assert "repos" in payload
    assert set(payload["repos"]) == {"workspace", "motor", "vllm", "vllm-ascend"}
    assert "gh" in payload
    assert "lock" in payload
    assert "workspace_ready" in payload
    assert payload["workspace_ready"]["all_repos_initialized"] is True


def test_probe_compact_includes_head_commit(bare_repo: Path) -> None:
    info = inspect_repo(bare_repo, "workspace", None)
    from _repo_init_common import compact_repo_summary

    summary = compact_repo_summary(info)
    assert summary.get("head_commit")


def test_apply_configure_remotes_idempotent(bare_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import repo_init_apply

    origin = "git@github.com:user/repo.git"
    upstream = "git@github.com:org/upstream.git"
    monkeypatch.setitem(repo_init_apply.REPO_PATHS, "workspace", bare_repo)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "repo_init_apply.py",
            "--configure-remotes",
            "--repo",
            "workspace",
            "--origin-url",
            origin,
            "--upstream-url",
            upstream,
        ],
    )

    assert repo_init_apply.main() == 0
    assert repo_init_apply.main() == 0
    assert remote_names(bare_repo) == sorted(["origin", "upstream"])


def test_workspace_ready_facts_cover_roles() -> None:
    from repo_init_probe import workspace_ready_facts

    facts = workspace_ready_facts(
        repos={role: {"initialized": True} for role in REPO_ROLES},
        submodules={"all_initialized": False},
        gh={"installed": True, "logged_in": False},
        lock={"status": "warning", "errors": [], "warnings": ["x"]},
    )
    assert facts["all_repos_initialized"] is True
    assert facts["submodules_initialized"] is False
    assert facts["gh_available"] is True
    assert facts["gh_authenticated"] is False
    assert facts["lock_status"] == "warning"


def test_probe_progress_on_stderr() -> None:
    result, _ = _run_json(PROBE, "--compact")
    assert any(line.startswith(PROGRESS_PREFIX) for line in result.stderr.splitlines())


def test_probe_gh_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("_repo_init_common.which", lambda name: None if name == "gh" else "/usr/bin/git")
    state = gh_login()
    assert state["installed"] is False
    assert "logged_in" not in state or state.get("logged_in") is False


def test_probe_gh_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd, *, cwd=None, check=False):
        if cmd[:2] == ["gh", "auth"]:
            return 1, "", "not logged in"
        if cmd[:2] == ["gh", "api"]:
            raise AssertionError("gh api should not run when auth fails")
        return 0, "", ""

    monkeypatch.setattr("_repo_init_common.which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    monkeypatch.setattr("_repo_init_common.run", fake_run)
    state = gh_login()
    assert state["installed"] is True
    assert state["logged_in"] is False


def test_compact_submodule_uninitialized() -> None:
    rows = [
        {"state": "-", "commit": "abc123", "path": "sources/motor", "detail": ""},
        {"state": " ", "commit": "def456", "path": "sources/vllm", "detail": "(heads/main)"},
    ]
    summary = compact_submodule_summary(rows)
    assert summary["all_initialized"] is False
    assert len(summary["needs_attention"]) == 1
    assert summary["needs_attention"][0]["path"] == "sources/motor"


def test_probe_submodule_uninitialized_fixture(workspace_with_submodule: Path) -> None:
    result = subprocess.run(
        ["git", "submodule", "status", "--recursive"],
        cwd=str(workspace_with_submodule),
        check=False,
        text=True,
        capture_output=True,
    )
    assert "-" in result.stdout


def test_probe_read_only_no_profile_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import repo_init_probe

    profile_path = tmp_path / "machine-profile.json"
    monkeypatch.setattr(
        "mws_local_state.PROFILE_PATH",
        profile_path,
        raising=False,
    )
    saved: list[dict] = []

    def _load_profile(*, persist_missing: bool = True):
        assert persist_missing is False
        return {"workspace_id": "mws-test"}

    def _save_profile(data):
        saved.append(data)

    monkeypatch.setattr(repo_init_probe, "load_profile", _load_profile)
    monkeypatch.setattr(repo_init_probe, "save_profile", _save_profile, raising=False)
    monkeypatch.setattr(
        repo_init_probe,
        "verify_lock",
        lambda **_: {"status": "ok", "errors": [], "warnings": []},
    )
    monkeypatch.setattr("mws_local_state.LOCAL_ROOT", tmp_path / "state", raising=False)
    monkeypatch.setattr("mws_run_state.LOCAL_ROOT", tmp_path / "state", raising=False)
    monkeypatch.setattr(sys, "argv", ["repo_init_probe.py"])

    rc = repo_init_probe.main()
    assert rc in {0, 1}
    assert not profile_path.exists()
    assert saved == []


def test_parse_remote_url_github_and_gitcode() -> None:
    assert parse_remote_url("git@github.com:vllm-project/vllm.git") == "vllm-project/vllm"
    assert parse_remote_url("https://gitcode.com/Ascend/MindIE-Motor.git") == "Ascend/MindIE-Motor"


def test_classify_remote_motor_community() -> None:
    assert classify_remote("motor", "Ascend/MindIE-Motor", "someuser") == "community"
    assert classify_remote("vllm", "someuser/vllm", "someuser") == "user-fork"


def test_topology_configure_preserves_extra_remote(bare_repo: Path) -> None:
    _add_remote(bare_repo, "origin", "git@github.com:user/repo.git")
    _add_remote(bare_repo, "upstream2", "git@github.com:org/upstream2.git")

    configure_remotes(
        bare_repo,
        origin_url="git@github.com:user/repo.git",
        upstream_url="git@github.com:org/upstream.git",
    )

    names = remote_names(bare_repo)
    assert "upstream2" in names
    assert remote_names(bare_repo).count("upstream2") == 1


def test_topology_configure_idempotent(bare_repo: Path) -> None:
    origin = "git@github.com:user/repo.git"
    upstream = "git@github.com:org/upstream.git"
    first = configure_remotes(bare_repo, origin_url=origin, upstream_url=upstream)
    second = configure_remotes(bare_repo, origin_url=origin, upstream_url=upstream)
    assert first["actions"]
    assert second["actions"] == []


def test_topology_configure_updates_conflicting_origin(bare_repo: Path) -> None:
    _add_remote(bare_repo, "origin", "git@github.com:old/repo.git")
    desired = "git@github.com:user/repo.git"
    actions = configure_remotes(bare_repo, origin_url=desired)["actions"]
    assert any(item.get("action") == "set-fetch-url" for item in actions)
    assert remote_names(bare_repo) == ["origin"]


def test_topology_uninitialized_submodule_rejected(workspace_with_submodule: Path) -> None:
    submodule_path = workspace_with_submodule / "sources" / "child"
    assert not submodule_path.exists()
    with pytest.raises(RepoTopologyError, match="path does not exist|not a git repository"):
        resolve_repo(str(submodule_path))


def test_inspect_repo_uninitialized_submodule(workspace_with_submodule: Path) -> None:
    submodule_path = workspace_with_submodule / "sources" / "child"
    info = inspect_repo(submodule_path, "motor", None)
    assert info["exists"] is False or info["initialized"] is False or "error" in info


def test_apply_no_action_error() -> None:
    result, payload = _run_json(APPLY)
    assert result.returncode == 1
    assert payload["status"] == "failed"


def test_init_submodules_noop_without_gitmodules(bare_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import repo_init_apply

    monkeypatch.setattr(repo_init_apply, "REPO_ROOT", bare_repo)
    first = repo_init_apply.init_submodules()
    second = repo_init_apply.init_submodules()
    assert first["status"] == "ok"
    assert second["status"] == "ok"


def test_init_submodules_defaults_to_direct_only(monkeypatch: pytest.MonkeyPatch) -> None:
    import repo_init_apply

    calls: list[list[str]] = []

    def fake_git_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(repo_init_apply, "_git_run", fake_git_run)
    assert repo_init_apply.init_submodules()["status"] == "ok"
    assert calls == [["submodule", "sync"], ["submodule", "update", "--init"]]


def test_init_submodules_can_include_nested_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    import repo_init_apply

    calls: list[list[str]] = []

    def fake_git_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(repo_init_apply, "_git_run", fake_git_run)
    assert repo_init_apply.init_submodules(recursive=True)["status"] == "ok"
    assert calls == [
        ["submodule", "sync", "--recursive"],
        ["submodule", "update", "--init", "--recursive"],
    ]


def test_apply_configure_remotes_preserves_extra(bare_repo: Path) -> None:
    _add_remote(bare_repo, "origin", "git@github.com:old/repo.git")
    _add_remote(bare_repo, "extra", "git@github.com:other/extra.git")

    origin = "git@github.com:user/repo.git"
    upstream = "git@github.com:org/upstream.git"
    configure_remotes(bare_repo, origin_url=origin, upstream_url=upstream)

    assert "extra" in remote_names(bare_repo)
    assert remote_names(bare_repo) == sorted(["origin", "upstream", "extra"])
