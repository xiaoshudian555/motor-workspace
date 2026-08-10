---
name: motor-k8s-deploy
description: Apply, status, stop, and restart Motor using an immutable deploy-config-ready bundle.
---

# motor-k8s-deploy

Fourth step of Motor Deploy (3+3 part 2). Consumes a successful
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

For a user-approved live configuration change that must restart only Controller
or only Coordinator, read
[`references/component-config-rollout.md`](references/component-config-rollout.md).
That maintenance path is intentionally separate from `deploy_restart.py`, which
restarts the deploy run's full workload set.

Apply, stop, and restart require `--approved-by-user`.

All Kubernetes operations run `kubectl` on the selected remote machine over
SSH, using that machine's `kube_context`. Local bundle manifests are staged in
a unique remote temporary directory for apply/delete and removed afterwards;
the development host's kubectl and kubeconfig are never used.

## Responsibilities

- Validate config run, bundle digest, and fixed path binding before apply.
- Apply bundle manifests byte-for-byte.
- Wait for deploy-scoped Deployment/StatefulSet rollouts
  (`kubectl rollout status` per bundle `workload_names`) and collect runtime
  `__file__` paths for `motor`, `vllm`, and `vllm_ascend`.
- Runtime package policy has exactly two modes. Image mode requires Motor,
  vLLM, and vllm-ascend to load from image-installed site/dist-packages. Motor
  wheel mode requires Motor to load from the wheel installation and vLLM plus
  vllm-ascend to remain image-installed. Any fixed source-tree import path is
  rejected in both modes.
- `deploy-complete status=ready` means apply + rollout + runtime code paths;
  Coordinator service readiness (`GET /readiness` body `ready=true`) is validated
  by `motor-smoke`, not this skill.
- Associate restart/stop/status with the deploy run via `bundle_dir`.
- `deploy_restart` may run parity first for code-only updates, then restart
  workloads and re-collect Ready + runtime code path evidence.
- Component-scoped live configuration maintenance preserves the existing
  `motor-config` ConfigMap, changes only the requested JSON field, and rolls out
  only the selected Controller or Coordinator Deployment.

## Does not

- Re-run environment preflight or config configure steps.
- Create diagnostic workloads before apply.
- Declare benchmark/profiling success.
