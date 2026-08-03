---
name: motor-k8s-deploy
description: Apply, status, stop, and restart Motor using an immutable deploy-config-ready bundle.
---

# motor-k8s-deploy

Third step of Motor Deploy (3+3 part 2). Consumes a successful
`deploy-config-ready` run and its immutable config bundle only. Does not
auto-run parity, render, substitute, or dry-run.

## Entry points

```bash
python3 .agents/skills/motor-k8s-deploy/scripts/deploy_apply.py \
  --machine <alias> --config-run-id <id> --approved-by-user

python3 .agents/skills/motor-k8s-deploy/scripts/deploy_restart.py \
  --machine <alias> --deploy-run-id <id> --approved-by-user

python3 .agents/skills/motor-k8s-deploy/scripts/deploy_status.py \
  --machine <alias> --deploy-run-id <id>

python3 .agents/skills/motor-k8s-deploy/scripts/deploy_stop.py \
  --machine <alias> --deploy-run-id <id> --approved-by-user
```

Legacy `deploy_plan.py` redirects to `motor-deploy-configure`.

Apply, stop, and restart require `--approved-by-user`.

All Kubernetes operations run `kubectl` on the selected remote machine over
SSH, using that machine's `kube_context`. Local bundle manifests are staged in
a unique remote temporary directory for apply/delete and removed afterwards;
the development host's kubectl and kubeconfig are never used.

## Responsibilities

- Validate config run, bundle digest, and fixed path binding before apply.
- Apply bundle manifests byte-for-byte.
- Wait for Pod Ready, verify minimal service endpoints, and collect runtime
  `__file__` paths for `motor`, `vllm`, and `vllm_ascend`.
- Associate restart/stop/status with the deploy run via `bundle_dir`.
- `deploy_restart` may run parity first for code-only updates, then restart
  workloads and re-collect Ready + runtime code path evidence.

## Does not

- Re-run environment preflight or config configure steps.
- Create diagnostic workloads before apply.
- Declare benchmark/profiling success.
