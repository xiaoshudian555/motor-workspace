# `.remote-dev` VAWS diff audit (Work Package A)

VAWS reference: `maoxx241/vllm-ascend-workspace@4a952fcc`  
Motor layout: `scaffold/.remote-dev/` (VAWS: repo-root `.remote-dev/`)

## Summary

- **73 tests pass** (`python3 -m pytest tests/ -q` from this directory).
- **67 source files identical** to VAWS at 4a952fcc (core ops, hooks, MCP server, JSON schemas, remote CLI tools, most tests).
- **10 files intentionally diverge** — all Motor custom (scaffold nesting, direct endpoint model); no VAWS-only bug fixes pending cherry-pick.
- **1 file fixed in WP-A** — `tests/test_cli_help.py` (refactor typo `SCAFFOLD_SCAFFOLD_ROOT`, shim path assertion).

Endpoint contracts verified by tests: `remote.read/write/edit/bash/glob/grep`, jobs, artifacts, MCP resources, result envelope.

## Per-file diff table

| File | Status | Notes |
|------|--------|-------|
| `core/endpoint.py` | Motor custom | `repo_root()` → `substrate.parent.parent`; removed `_endpoint_from_managed` / session_id / session_file / machine |
| `mcp/schemas.py` | Motor custom | Endpoint selector: `host+port` or `alias` only |
| `mcp/tools.py` | Motor custom | Job tools skip managed selector keys |
| `tools/_cli.py` | Motor custom | Dropped `--session-id`, `--session-file`, `--machine` |
| `tools/sync_claude_skills.py` | Motor custom | Paths: `scaffold/.agents/skills` → repo `.claude/skills` |
| `tools/validate_remote_dev_scaffold.py` | Motor custom | Scaffold-relative paths; pytest for agents; no session flags |
| `README.md` | Motor custom | Docs: direct endpoint + alias; machine inventory via `.agents/lib` |
| `tests/test_hook_guard.py` | Motor custom | `REPO_ROOT = parents[3]` (scaffold nesting) |
| `tests/test_mcp_schema.py` | Motor custom | No `session_id` in selector; MCP server path via `scaffold/.remote-dev` |
| `tests/test_cli_help.py` | **Fixed WP-A** | `SCAFFOLD_SCAFFOLD_ROOT` → `REMOTE_DEV`; shim path `scaffold/.agents/skills/...` |
| All other `.py`, `.json`, `.md` | Identical | No action |

## Not restored (by design)

- Managed `session_id` / `session_file` / `machine` endpoint resolution
- VAWS `vaws_remote_toolbox` import path in `core/endpoint.py`

## Ops note

Run `python3 tools/sync_claude_skills.py` from repo root when new skills are added under `scaffold/.agents/skills/` (generates `.claude/skills/` shims outside this directory).
