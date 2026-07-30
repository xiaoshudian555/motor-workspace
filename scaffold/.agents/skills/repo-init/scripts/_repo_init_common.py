#!/usr/bin/env python3
"""Shared helpers for motor-workspace repo-init.

Adapted from vllm-ascend-workspace repo-init (MIT, commit 4a952fcc).
Motor-specific repo roles and GitCode URL parsing.
"""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

LIB_DIR = Path(__file__).resolve().parents[3] / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from repo_paths import (  # noqa: E402
    MOTOR_ROOT,
    REPO_ROOT,
    VLLM_ASCEND_ROOT,
    VLLM_ROOT,
)

# Community upstream identities (owner/repo or org/repo on GitHub/GitCode).
COMMUNITY: dict[str, str | None] = {
    "workspace": None,
    "motor": "Ascend/MindIE-Motor",
    "vllm": "vllm-project/vllm",
    "vllm-ascend": "vllm-project/vllm-ascend",
}

REPO_PATHS: dict[str, Path] = {
    "workspace": REPO_ROOT,
    "motor": MOTOR_ROOT,
    "vllm": VLLM_ROOT,
    "vllm-ascend": VLLM_ASCEND_ROOT,
}

REPO_ROLES = ("workspace", "motor", "vllm", "vllm-ascend")

GIT_URL_PATTERNS = [
    r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?$",
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?$",
    r"^ssh://git@github\.com/(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?$",
    r"^git@gitcode\.com:(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?$",
    r"^https://gitcode\.com/(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?$",
    r"^ssh://git@gitcode\.com/(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?$",
]


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    check: bool = False,
    timeout: float | None = None,
) -> tuple[int, str, str]:
    command = list(cmd)
    if command and command[0] == "git":
        command = ["git", "-c", "safe.directory=*", *command[1:]]
    if timeout is None and command and command[0] == "gh":
        timeout = 15.0
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        detail = f"command timed out after {timeout}s: {' '.join(command)}"
        if check:
            raise RuntimeError(detail) from exc
        stdout = (exc.stdout or "").strip() if isinstance(exc.stdout, str) else ""
        stderr = detail if not stdout else f"{stdout}\n{detail}"
        return 124, stdout, stderr
    if check and proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "command failed"
        raise RuntimeError(detail)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def which(name: str) -> str | None:
    return shutil.which(name)


def detect_wsl() -> bool:
    if platform.system() != "Linux":
        return False
    release = platform.release().lower()
    if "microsoft" in release or "wsl" in release:
        return True
    try:
        with open("/proc/version", "r", encoding="utf-8", errors="replace") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def os_release() -> dict[str, str]:
    data: dict[str, str] = {}
    if platform.system() != "Linux":
        return data
    path = Path("/etc/os-release")
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        data[key] = value.strip().strip('"')
    return data


def detect_platform() -> dict[str, Any]:
    system = platform.system()
    release_info = os_release()
    if system == "Darwin":
        kind = "macos"
    elif system == "Windows":
        kind = "windows"
    elif detect_wsl():
        kind = "wsl"
    else:
        kind = "linux"
    return {
        "kind": kind,
        "system": system,
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "os_release": release_info,
    }


def parse_remote_url(url: str | None) -> str | None:
    if not url:
        return None
    for pattern in GIT_URL_PATTERNS:
        match = re.match(pattern, url)
        if match:
            return f"{match.group('owner')}/{match.group('repo')}"
    return None


def git_root(path: Path | None = None) -> Path | None:
    rc, out, _ = run(["git", "rev-parse", "--show-toplevel"], cwd=path)
    if rc != 0 or not out:
        return None
    return Path(out).resolve()


def git_submodule_status(root: Path) -> list[dict[str, str]]:
    rc, out, err = run(["git", "submodule", "status", "--recursive"], cwd=root)
    if rc != 0:
        return [{"error": err or "unable to inspect submodules"}]
    rows: list[dict[str, str]] = []
    for line in out.splitlines():
        if not line:
            continue
        state = line[0]
        parts = line[1:].strip().split()
        if len(parts) < 2:
            rows.append({"raw": line})
            continue
        commit, path = parts[0], parts[1]
        branch = parts[2] if len(parts) > 2 else ""
        rows.append(
            {
                "state": state,
                "commit": commit,
                "path": path,
                "detail": branch,
            }
        )
    return rows


def branch_info(cwd: Path) -> dict[str, Any]:
    rc, branch, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    detached = branch == "HEAD" or rc != 0
    rc2, head, _ = run(["git", "rev-parse", "HEAD"], cwd=cwd)
    rc3, tracking, _ = run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        cwd=cwd,
    )
    ahead = behind = None
    if rc3 == 0:
        rc4, counts, _ = run(
            ["git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
            cwd=cwd,
        )
        if rc4 == 0 and counts:
            left, right = counts.split()
            ahead, behind = int(left), int(right)
    return {
        "current_branch": None if detached else branch,
        "detached_head": detached,
        "head_commit": head if rc2 == 0 else None,
        "tracking_branch": tracking if rc3 == 0 else None,
        "ahead_of_tracking": ahead,
        "behind_tracking": behind,
    }


def git_dirty(cwd: Path) -> dict[str, Any]:
    rc, out, err = run(["git", "status", "--porcelain"], cwd=cwd)
    if rc != 0:
        return {"error": err or "unable to inspect worktree"}
    rows = out.splitlines()
    return {
        "dirty": bool(rows),
        "entries": rows[:50],
        "entry_count": len(rows),
    }


def remotes(cwd: Path) -> dict[str, dict[str, Any]]:
    rc, out, err = run(["git", "remote"], cwd=cwd)
    if rc != 0:
        return {"error": {"message": err or "unable to inspect remotes"}}
    names = [line.strip() for line in out.splitlines() if line.strip()]
    data: dict[str, dict[str, Any]] = {}
    for name in names:
        fetch_rc, fetch_url, _ = run(["git", "remote", "get-url", name], cwd=cwd)
        push_rc, push_url, _ = run(["git", "remote", "get-url", "--push", name], cwd=cwd)
        fetch_value = fetch_url if fetch_rc == 0 else None
        push_value = push_url if push_rc == 0 else None
        data[name] = {
            "fetch_url": fetch_value,
            "push_url": push_value,
            "fetch_repo": parse_remote_url(fetch_value),
            "push_repo": parse_remote_url(push_value),
        }
    return data


def classify_remote(
    repo_role: str,
    full_name: str | None,
    user_login: str | None,
) -> str:
    if not full_name:
        return "missing"
    community = COMMUNITY.get(repo_role)
    if community and full_name == community:
        return "community"
    if community and user_login:
        repo_basename = community.split("/", 1)[1]
        if full_name == f"{user_login}/{repo_basename}":
            return "user-fork"
    if user_login and full_name.startswith(f"{user_login}/"):
        return "user-fork"
    return "other"


def inspect_repo(
    repo_path: Path,
    repo_role: str,
    user_login: str | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(repo_path),
        "exists": repo_path.exists(),
        "initialized": False,
    }
    if not repo_path.exists():
        return result

    rc, _, err = run(["git", "rev-parse", "--git-dir"], cwd=repo_path)
    if rc != 0:
        result["error"] = err or "not a git repository"
        return result

    rc_top, top, top_err = run(["git", "rev-parse", "--show-toplevel"], cwd=repo_path)
    if rc_top != 0 or Path(top).resolve() != repo_path.resolve():
        result["error"] = (
            top_err or "path is inside a parent repository but is not an initialized submodule"
        )
        return result

    result["initialized"] = True
    result["branch"] = branch_info(repo_path)
    result["worktree"] = git_dirty(repo_path)
    remote_data = remotes(repo_path)
    result["remotes"] = remote_data

    if "error" not in remote_data:
        origin_fetch = remote_data.get("origin", {}).get("fetch_repo")
        upstream_fetch = remote_data.get("upstream", {}).get("fetch_repo")
        result["origin_kind"] = classify_remote(repo_role, origin_fetch, user_login)
        result["upstream_kind"] = classify_remote(repo_role, upstream_fetch, user_login)

    return result


def gh_login() -> dict[str, Any]:
    gh_path = which("gh")
    if not gh_path:
        return {"installed": False}

    rc, out, err = run(["gh", "auth", "status", "--hostname", "github.com"])
    status: dict[str, Any] = {
        "installed": True,
        "path": gh_path,
        "logged_in": rc == 0,
        "auth_status_stdout": out,
        "auth_status_stderr": err,
    }
    if rc != 0:
        return status

    rc2, login, _ = run(["gh", "api", "user", "--jq", ".login"])
    if rc2 == 0 and login:
        status["user_login"] = login

    rc3, protocol, _ = run(["gh", "config", "get", "git_protocol", "--host", "github.com"])
    if rc3 == 0 and protocol:
        status["git_protocol"] = protocol

    return status


def gh_fork_info(user_login: str | None) -> dict[str, Any]:
    if not user_login or not which("gh"):
        return {}

    info: dict[str, Any] = {}
    for role in ("vllm", "vllm-ascend"):
        community = COMMUNITY.get(role)
        if not community:
            continue
        repo_name = community.split("/", 1)[1]
        full_name = f"{user_login}/{repo_name}"
        rc, out, err = run(["gh", "api", f"repos/{full_name}"])
        if rc != 0:
            info[role] = {"exists": False, "error": err}
            continue
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            info[role] = {"exists": True, "error": "unable to decode gh api output"}
            continue

        parent = payload.get("parent") or {}
        info[role] = {
            "exists": True,
            "full_name": payload.get("full_name"),
            "is_fork": bool(payload.get("fork")),
            "parent_full_name": parent.get("full_name"),
            "default_branch": payload.get("default_branch"),
            "ssh_url": payload.get("ssh_url"),
            "clone_url": payload.get("clone_url"),
        }
    return info


def gh_install_plan(platform_info: dict[str, Any]) -> dict[str, Any]:
    kind = platform_info["kind"]
    has_brew = bool(which("brew"))
    has_apt = bool(which("apt"))
    has_winget = bool(which("winget"))

    if kind == "macos":
        preferred = {
            "label": "Homebrew",
            "commands": ["brew install gh"],
            "requires_privilege": False,
        } if has_brew else {
            "label": "Homebrew bootstrap + Homebrew install",
            "commands": [
                '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"',
                "brew install gh",
            ],
            "requires_privilege": True,
        }
        fallback = {
            "label": "manual install",
            "commands": ["see https://cli.github.com/manual/installation"],
            "requires_privilege": False,
        }
    elif kind in {"linux", "wsl"} and has_apt:
        preferred = {
            "label": "official GitHub CLI Debian packages",
            "commands": ["see https://github.com/cli/cli/blob/trunk/docs/install_linux.md"],
            "requires_privilege": True,
        }
        fallback = {
            "label": "manual install",
            "commands": ["see https://cli.github.com/manual/installation"],
            "requires_privilege": False,
        }
    elif kind == "windows" and has_winget:
        preferred = {
            "label": "WinGet",
            "commands": ["winget install --id GitHub.cli"],
            "requires_privilege": False,
        }
        fallback = {
            "label": "manual install",
            "commands": ["see https://cli.github.com/manual/installation"],
            "requires_privilege": False,
        }
    else:
        preferred = {
            "label": "manual install",
            "commands": ["see https://cli.github.com/manual/installation"],
            "requires_privilege": False,
        }
        fallback = preferred

    return {"preferred": preferred, "fallback": fallback}


def tool_state() -> dict[str, str | None]:
    names = [
        "git",
        "gh",
        "ssh",
        "ssh-keygen",
        "brew",
        "apt",
        "winget",
        "sudo",
        "python3",
        "python",
    ]
    return {name: which(name) for name in names}


def compact_repo_summary(repo: dict[str, Any]) -> dict[str, Any]:
    branch = repo.get("branch") or {}
    worktree = repo.get("worktree") or {}
    remote_data = repo.get("remotes") or {}
    origin = remote_data.get("origin") or {}
    upstream = remote_data.get("upstream") or {}
    summary: dict[str, Any] = {
        "path": repo.get("path"),
        "exists": repo.get("exists"),
        "initialized": repo.get("initialized"),
    }
    if repo.get("error"):
        summary["error"] = repo.get("error")
        return summary

    summary.update(
        {
            "head_commit": branch.get("head_commit"),
            "current_branch": branch.get("current_branch"),
            "tracking_branch": branch.get("tracking_branch"),
            "detached_head": branch.get("detached_head"),
            "dirty": worktree.get("dirty"),
            "dirty_entries": worktree.get("entry_count"),
            "origin_repo": origin.get("fetch_repo"),
            "origin_kind": repo.get("origin_kind"),
            "upstream_repo": upstream.get("fetch_repo"),
            "upstream_kind": repo.get("upstream_kind"),
            "extra_remotes": sorted(
                name for name in remote_data if name not in {"origin", "upstream"}
            ),
        }
    )
    return summary


def compact_submodule_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    for row in rows:
        state = row.get("state")
        if state and state != " ":
            issues.append(
                {
                    "path": row.get("path", ""),
                    "state": state,
                    "detail": row.get("detail", ""),
                }
            )
        if row.get("error"):
            issues.append({"error": row["error"]})
    return {
        "count": len(rows),
        "all_initialized": len(rows) > 0 and len(issues) == 0,
        "needs_attention": issues,
    }


def compact_fork_summary(forks: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for role, info in forks.items():
        summary[role] = {
            "exists": info.get("exists"),
            "full_name": info.get("full_name"),
            "parent_full_name": info.get("parent_full_name"),
            "default_branch": info.get("default_branch"),
        }
    return summary
