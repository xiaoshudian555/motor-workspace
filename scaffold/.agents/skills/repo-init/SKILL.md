---
name: repo-init
description: Initialize motor-workspace after clone — submodules, lock verify, fork topology hints. Use for "初始化仓库", "配置 workspace", first-time setup.
---

# repo-init

Initialize the three-submodule motor-workspace and verify lock alignment.

## Entry points

```bash
python3 .agents/skills/repo-init/scripts/repo_init_probe.py --compact
python3 .agents/skills/repo-init/scripts/repo_init_apply.py --submodules
```

Progress on stderr as `__MWS_PROGRESS__=<json>`, final JSON on stdout.

## Rules

- Probe-first; do not mutate remotes without explicit user consent.
- Keep secrets out of tracked files.
- State lives under `.motor-workspace-local/`.
