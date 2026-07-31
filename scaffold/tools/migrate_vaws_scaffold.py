#!/usr/bin/env python3
"""One-shot VAWS → motor-workspace scaffold migration helper."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

VAWS = Path("/home/h00906152/projects/vllm/vllm-ascend-workspace")
SCAFFOLD = Path("/home/h00906152/projects/pymotor/motor-workspace/scaffold")
REPO_ROOT = SCAFFOLD.parent

# Bulk text replacements applied to copied content (order matters).
REPLACEMENTS: list[tuple[str, str]] = [
    (".vaws-local", ".motor-workspace-local"),
    ("__VAWS_", "__MWS_"),
    ("vaws_remote_toolbox", "mws_remote_toolbox"),
    ("vaws_session_state", "mws_session_state"),
    ("vaws_session_id", "mws_session_id"),
    ("vaws_validate", "mws_validate"),
    ("vaws_local_state", "mws_local_state"),
    ("VAWS target resolver", "MWS target resolver"),
    ("failed to import VAWS", "failed to import MWS"),
    ('source={"vaws_target"', 'source={"mws_target"'),
    ("vaws_target", "mws_target"),
    ("Managed VAWS", "Managed MWS"),
    ("managed VAWS", "managed MWS"),
    ("for VAWS", "for MWS"),
    ("VAWS Remote", "MWS Remote"),
    ("VAWS agent", "MWS agent"),
    ("VAWS scaffold", "MWS scaffold"),
    ("VAWS managed", "MWS managed"),
    ("VAWS session", "MWS session"),
    ("VAWS runtime", "MWS runtime"),
    ("VAWS build", "MWS build"),
    ("VAWS machine", "MWS machine"),
    ("VAWS parity", "MWS parity"),
    ("vaws-machine-management", "mws-machine-management"),
    ('CONTAINER_PREFIX = "vaws-"', 'CONTAINER_PREFIX = "mws-"'),
    ('"vaws-"', '"mws-"'),
    ("/vllm-workspace", "/mnt/motor-workspace"),
    ('REMOTE_DEV_DEFAULT_ROOT", "/"', 'REMOTE_DEV_DEFAULT_ROOT", "/mnt"'),
    ('REMOTE_DEV_DEFAULT_ROOT", "/vllm-workspace"', 'REMOTE_DEV_DEFAULT_ROOT", "/mnt"'),
    ('REMOTE_DEV_DEFAULT_CWD", "/vllm-workspace"', 'REMOTE_DEV_DEFAULT_CWD", "/mnt/motor-workspace"'),
    ("test_vaws_scaffold_safety", "test_mws_scaffold_safety"),
    ("_vaws_", "_mws_"),
]

SKIP_REMOTE_DEV = {".pytest_cache", "state"}


def adapt_text(content: str, *, extra: list[tuple[str, str]] | None = None) -> str:
    for old, new in REPLACEMENTS + (extra or []):
        content = content.replace(old, new)
    return content


def copy_tree(
    src: Path,
    dst: Path,
    *,
    skip_names: set[str] | None = None,
    adapt: bool = True,
) -> None:
    skip = skip_names or set()
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        if any(part in skip for part in rel.parts):
            continue
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if adapt and item.suffix in {".py", ".md", ".mdc", ".json", ".yaml", ".yml", ".sh", ".ps1"}:
            text = item.read_text(encoding="utf-8")
            target.write_text(adapt_text(text), encoding="utf-8")
        else:
            shutil.copy2(item, target)


def copy_lib_files() -> None:
    lib_dst = SCAFFOLD / ".agents" / "lib"
    mapping = {
        "vaws_local_state.py": "mws_local_state.py",
        "vaws_validate.py": "mws_validate.py",
        "vaws_remote_toolbox.py": "mws_remote_toolbox.py",
        "vaws_session_id.py": "mws_session_id.py",
        "vaws_session_state.py": "mws_session_state.py",
    }
    for src_name, dst_name in mapping.items():
        raw = (VAWS / ".agents" / "lib" / src_name).read_text(encoding="utf-8")
        text = adapt_text(raw)
        if dst_name == "mws_local_state.py":
            text = _adapt_local_state(text)
        if dst_name == "mws_remote_toolbox.py":
            text = _adapt_remote_toolbox_defaults(text)
        (lib_dst / dst_name).write_text(text, encoding="utf-8")


def _adapt_local_state(text: str) -> str:
    """Point state/helpers at motor-workspace repo root via repo_paths."""
    text = re.sub(
        r'ROOT = Path\(__file__\)\.resolve\(\)\.parents\[2\]',
        "from repo_paths import REPO_ROOT, SCAFFOLD_ROOT\n\nROOT = REPO_ROOT\nSCAFFOLD = SCAFFOLD_ROOT",
        text,
        count=1,
    )
    text = text.replace(
        '"""Local untracked state helpers for vllm-ascend-workspace.',
        '"""Local untracked state helpers for motor-workspace.',
    )
    return text


def _adapt_remote_toolbox_defaults(text: str) -> str:
    text = text.replace('DEFAULT_WORKDIR = "/mnt/motor-workspace"', 'DEFAULT_WORKDIR = "/mnt/motor-workspace"')
    return text


def adapt_skill_scripts(skill: str) -> None:
    scripts = SCAFFOLD / ".agents" / "skills" / skill / "scripts"
    for path in scripts.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        # VAWS skills live one level shallower (no scaffold/); bump repo-root parents only.
        text = re.sub(
            r"Path\(__file__\)\.resolve\(\)\.parents\[4\]\nLIB_DIR = .*? / \"lib\"",
            "SCAFFOLD = Path(__file__).resolve().parents[4]\nROOT = SCAFFOLD.parent\nLIB_DIR = SCAFFOLD / \".agents\" / \"lib\"",
            text,
            flags=re.DOTALL,
        )
        text = re.sub(
            r"Path\(__file__\)\.resolve\(\)\.parents\[4\]\nMM_SCRIPTS",
            "SCAFFOLD = Path(__file__).resolve().parents[4]\nROOT = SCAFFOLD.parent\nMM_SCRIPTS",
            text,
        )
        # parity_sync repo root only
        text = re.sub(
            r"^ROOT = Path\(__file__\)\.resolve\(\)\.parents\[4\]$",
            "ROOT = Path(__file__).resolve().parents[5]",
            text,
            flags=re.M,
        )
        path.write_text(text, encoding="utf-8")


def adapt_repo_init_probe() -> None:
    probe = SCAFFOLD / ".agents" / "skills" / "repo-init" / "scripts" / "repo_init_probe.py"
    if not probe.exists():
        return
    text = probe.read_text(encoding="utf-8")
    text = text.replace(
        'COMMUNITY = {\n    "workspace": "maoxx241/vllm-ascend-workspace",\n    "vllm": "vllm-project/vllm",\n    "vllm-ascend": "vllm-project/vllm-ascend",\n}',
        'COMMUNITY = {\n    "workspace": "Ascend/motor-workspace",\n    "motor": "Ascend/MindIE-Motor",\n    "vllm": "vllm-project/vllm",\n    "vllm-ascend": "vllm-project/vllm-ascend",\n}',
    )
    text = text.replace(
        'REPO_PATHS = {\n    "workspace": ".",\n    "vllm": "vllm",\n    "vllm-ascend": "vllm-ascend",\n}',
        'REPO_PATHS = {\n    "workspace": ".",\n    "motor": "sources/motor",\n    "vllm": "sources/vllm",\n    "vllm-ascend": "sources/vllm-ascend",\n}',
    )
    text = text.replace(
        'for role in ("workspace", "vllm", "vllm-ascend")',
        'for role in ("workspace", "motor", "vllm", "vllm-ascend")',
    )
    text = text.replace(
        'Path(__file__).resolve().parents[3]',
        'Path(__file__).resolve().parents[5]',
    )
    probe.write_text(text, encoding="utf-8")


def patch_endpoint_import() -> None:
    path = SCAFFOLD / ".remote-dev" / "core" / "endpoint.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "from mws_remote_toolbox import resolve_remote_target",
        "from mws_remote_toolbox import resolve_remote_target",
    )
    if 'DEFAULT_ROOT = os.environ.get("REMOTE_DEV_DEFAULT_ROOT", "/mnt")' not in text:
        text = re.sub(
            r'DEFAULT_ROOT = os\.environ\.get\("REMOTE_DEV_DEFAULT_ROOT", "[^"]+"\)',
            'DEFAULT_ROOT = os.environ.get("REMOTE_DEV_DEFAULT_ROOT", "/mnt")',
            text,
        )
        text = re.sub(
            r'DEFAULT_CWD = os\.environ\.get\("REMOTE_DEV_DEFAULT_CWD", "[^"]+"\)',
            'DEFAULT_CWD = os.environ.get("REMOTE_DEV_DEFAULT_CWD", "/mnt/motor-workspace")',
            text,
        )
    path.write_text(text, encoding="utf-8")


def retire_duplicate_lib() -> None:
    # mws_transport kept for motor-domain callers until mws_machine_target migrates.
    pass


def main() -> None:
    # 1. .remote-dev/
    rd_src = VAWS / ".remote-dev"
    rd_dst = SCAFFOLD / ".remote-dev"
    state_backup = None
    if (rd_dst / "state").exists():
        state_backup = rd_dst / "state"
        tmp = rd_dst / ".state_backup_migrate"
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.move(str(state_backup), str(tmp))
        state_backup = tmp
    copy_tree(rd_src, rd_dst, skip_names=SKIP_REMOTE_DEV)
    if state_backup and state_backup.exists():
        final_state = rd_dst / "state"
        if final_state.exists():
            shutil.rmtree(final_state)
        shutil.move(str(state_backup), str(final_state))
    patch_endpoint_import()

    # 2. lib/
    copy_lib_files()

    # 3. four generic skills scripts + references
    motor_repo_init_keep = [
        SCAFFOLD / ".agents" / "skills" / "repo-init" / "scripts" / "repo_init_apply.py",
        SCAFFOLD / ".agents" / "skills" / "repo-init" / "scripts" / "_repo_init_common.py",
    ]
    kept_repo_init: dict[str, str] = {}
    for path in motor_repo_init_keep:
        if path.exists():
            kept_repo_init[path.name] = path.read_text(encoding="utf-8")

    for skill in (
        "remote-code-parity",
        "machine-management",
        "repo-init",
        "remote-toolbox",
    ):
        for sub in ("scripts", "references"):
            src = VAWS / ".agents" / "skills" / skill / sub
            if src.exists():
                copy_tree(src, SCAFFOLD / ".agents" / "skills" / skill / sub)
        adapt_skill_scripts(skill)
    adapt_repo_init_probe()
    repo_init_scripts = SCAFFOLD / ".agents" / "skills" / "repo-init" / "scripts"
    for name, content in kept_repo_init.items():
        (repo_init_scripts / name).write_text(content, encoding="utf-8")

    # 4. session-management
    for sub in ("scripts", "references"):
        src = VAWS / ".agents" / "skills" / "session-management" / sub
        copy_tree(src, SCAFFOLD / ".agents" / "skills" / "session-management" / sub)
    sm_skill = VAWS / ".agents" / "skills" / "session-management" / "SKILL.md"
    if sm_skill.exists():
        (SCAFFOLD / ".agents" / "skills" / "session-management" / "SKILL.md").write_text(
            adapt_text(sm_skill.read_text(encoding="utf-8")), encoding="utf-8"
        )
    adapt_skill_scripts("session-management")

    # 5. .agents/scripts/
    copy_tree(VAWS / ".agents" / "scripts", SCAFFOLD / ".agents" / "scripts")

    # 6. misc
    copy_tree(VAWS / ".remote-dev" / "hooks", SCAFFOLD / ".remote-dev" / "hooks")
    tools_dst = SCAFFOLD / ".remote-dev" / "tools"
    for name in ("sync_claude_skills.py",):
        src = VAWS / ".remote-dev" / "tools" / name
        if src.exists():
            tools_dst.mkdir(parents=True, exist_ok=True)
            (tools_dst / name).write_text(adapt_text(src.read_text(encoding="utf-8")), encoding="utf-8")

    tests_dst = SCAFFOLD / ".agents" / "tests"
    tests_dst.mkdir(parents=True, exist_ok=True)
    src_test = VAWS / ".agents" / "tests" / "test_vaws_scaffold_safety.py"
    (tests_dst / "test_mws_scaffold_safety.py").write_text(
        adapt_text(src_test.read_text(encoding="utf-8")), encoding="utf-8"
    )

    cursor_dst = REPO_ROOT / ".cursor" / "rules"
    cursor_dst.mkdir(parents=True, exist_ok=True)
    for name in ("skill-maintenance.mdc", "commit-conventions.mdc", "submodule-context.mdc"):
        src = VAWS / ".cursor" / "rules" / name
        if src.exists():
            (cursor_dst / name).write_text(adapt_text(src.read_text(encoding="utf-8")), encoding="utf-8")

    retire_duplicate_lib()
    print("migration complete")


if __name__ == "__main__":
    main()
