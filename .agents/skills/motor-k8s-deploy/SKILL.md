---
name: motor-k8s-deploy
description: Plan, apply, status, stop, and restart Motor on Kubernetes via upstream deployer. Auto-runs parity before first plan.
---

# motor-k8s-deploy

Thin wrapper around `motor/examples/deployer`. Processes only newly generated
dry-run YAML, verifies/reuses `/mnt` hostPath, injects machine `PYTHONPATH` on
runtime containers, and applies after user approval. Does not rewrite P/D
controller logic.

## Entry points

```bash
python3 .agents/skills/motor-k8s-deploy/scripts/deploy_plan.py --machine <alias> --profile profiles/a2-dev.yaml
python3 .agents/skills/motor-k8s-deploy/scripts/deploy_apply.py --machine <alias> --deploy-run-id <id> --approved-by-user
python3 .agents/skills/motor-k8s-deploy/scripts/deploy_restart.py --machine <alias> --deploy-run-id <id> --approved-by-user
python3 .agents/skills/motor-k8s-deploy/scripts/deploy_status.py --machine <alias> --deploy-run-id <id> --profile profiles/a2-dev.yaml
python3 .agents/skills/motor-k8s-deploy/scripts/deploy_stop.py --machine <alias> --deploy-run-id <id> --approved-by-user
```

Apply, stop, and restart require `--approved-by-user`.
Daily code changes use `deploy_restart` after parity sync; no plan digest gate.
