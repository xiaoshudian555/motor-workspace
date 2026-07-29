---
name: motor-k8s-deploy
description: Plan, apply, status, stop Motor on Kubernetes via upstream deployer. Auto-runs parity before start.
---

# motor-k8s-deploy

Thin wrapper around `motor/examples/deployer`. Injects session `PYTHONPATH` after
render. Does not rewrite P/D controller logic.

## Entry points

```bash
python3 .agents/skills/motor-k8s-deploy/scripts/deploy_plan.py --session-id <id> --profile profiles/a2-dev.yaml
python3 .agents/skills/motor-k8s-deploy/scripts/deploy_apply.py --session-id <id> --profile profiles/a2-dev.yaml --approved-by-user
python3 .agents/skills/motor-k8s-deploy/scripts/deploy_status.py --session-id <id> --profile profiles/a2-dev.yaml
```

Apply requires `--approved-by-user`.
