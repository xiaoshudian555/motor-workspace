---
name: session-management
description: Create, inspect, or remove isolated agent sessions with local worktree, remote session directory, namespace/job-id and leases.
---

# session-management

Each session binds:

- local worktree path (optional metadata)
- remote session root under shared mount root
- namespace + job-id for deploy
- `--session-id` passed to parity and deploy skills

## Entry points

```bash
python3 .agents/skills/session-management/scripts/session_create.py --machine dev1
python3 .agents/skills/session-management/scripts/session_list.py
python3 .agents/skills/session-management/scripts/session_status.py --session-id <id>
python3 .agents/skills/session-management/scripts/session_remove.py --session-id <id>
```
