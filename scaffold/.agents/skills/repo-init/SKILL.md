---
name: repo-init
description: Initialize motor-workspace after clone — gh/GitHub auth probe, submodules, lock verify, fork topology hints for workspace + motor + vllm + vllm-ascend. Use for "初始化仓库", "配置 workspace", "配置 remotes", first-time setup.
---

# repo-init

Prepare a fresh or drifted motor-workspace clone for development.

Machine inventory, remote NPU attach, and parity sync belong to later skills.

## Entry points

```bash
python3 scaffold/.agents/skills/repo-init/scripts/repo_init_probe.py --compact
python3 scaffold/.agents/skills/repo-init/scripts/repo_init_apply.py --submodules
python3 scaffold/.agents/skills/repo-init/scripts/repo_topology.py configure --repo <path> [--origin-url URL] [--upstream-url URL]
```

Progress on stderr as `__MWS_PROGRESS__=<json>`, final JSON on stdout.

## Four-repo view

| Role | Path | Community upstream |
| --- | --- | --- |
| workspace | repo root | (no fixed community remote) |
| motor | `sources/motor` | `Ascend/MindIE-Motor` on GitCode |
| vllm | `sources/vllm` | `vllm-project/vllm` |
| vllm-ascend | `sources/vllm-ascend` | `vllm-project/vllm-ascend` |

## Critical rules

- Probe first; probe is read-only (no profile creation, no remote writes).
- Ask before every mutation category; apply requires explicit CLI flags.
- Preserve extra remotes such as `upstream2`; never delete unknown remotes.
- Keep secrets out of tracked files.
- State lives under `.motor-workspace-local/`; `workspace.lock.yaml` is diagnostic only.
- Submodule init must complete before configuring submodule remotes.
- Machine username/profile setup belongs to `machine-management`, not repo-init.

## Workflow

1. Run `repo_init_probe.py --compact` and summarize gh/auth/submodules/lock/remotes.
2. Stop for user consent on topology mode and submodule init when the task is broad init.
3. Apply approved changes with explicit flags on `repo_init_apply.py` or `repo_topology.py`.
4. Report workspace-ready facts: four-repo paths, HEAD/dirty/remotes, lock warnings.

## References

- `references/behavior.md`
- `references/acceptance.md`
- `references/command-recipes.md`
