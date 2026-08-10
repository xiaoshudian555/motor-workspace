---
name: motor-deploy
description: "Thin dispatcher for Motor deployment work in the motor-workspace repository. Use for service launch and lifecycle requests such as 拉起一个服务, 拉起/启动/部署 Motor, apply 部署, 重启/停止/查看 Motor 服务; read-only feasibility requests such as 能不能起服务, 是否具备部署条件, 部署前检查, 检查部署环境; config preparation and post-deploy readiness; and reliability wording such as 构造故障, 故障注入, 验证故障恢复, which must stop as unsupported instead of routing to adjacent validators. Route to repo-local atomic skills under scaffold/.agents/skills; never use the legacy standalone deploy.py workflow."
---

# Motor Deploy Dispatcher

Act only as a thin router for the `motor-workspace` deployment workflow. Keep
deployment logic, commands, evidence contracts, and safety rules in the
repo-local atomic skills.

## Resolve the workspace

1. Starting from the current working directory, locate a parent containing both
   `scaffold/.agents/skills/` and `sources/motor/`.
2. Read that workspace's `AGENTS.md` before taking action.
3. If no matching workspace exists, stop and ask for the `motor-workspace` path.
   Do not fall back to the old standalone `examples/deployer/deploy.py` flow.

When the request says only “拉起一个服务” or another generic service phrase,
treat it as Motor deployment only when the current workspace was resolved by
the checks above. Otherwise ask which service/repository is intended.

## Route to atomic skills

Read each selected `SKILL.md` completely before following it.

| Intent or required evidence | Repo-local skill |
|---|---|
| Add, verify, or repair the target machine | `machine-management` |
| Synchronize the local source tree to fixed remote paths | `remote-code-parity` |
| Translate model, image, NPU, or feature intent into native config | `motor-config-edit` |
| Validate Kubernetes and MindCluster prerequisites | `motor-deploy-preflight` |
| Build or reuse the immutable validated config bundle | `motor-deploy-configure` |
| Apply, status, restart, stop, or component config rollout | `motor-k8s-deploy` |
| Validate Coordinator management readiness | `motor-smoke` |
| Send inference requests or validate feature behavior | `motor-functional` |
| Collect evidence for a failed deployment | `motor-diagnosis` |
| Check whether the machine/base environment can deploy without applying | `machine-management` then `motor-deploy-preflight` |
| Inject a fault or validate isolation/recovery/reliability | No implemented repo-local skill; stop at the unsupported boundary |

For a full “拉起/部署 Motor 服务” request, read
`scaffold/docs/motor-deploy.md`, inspect existing run-scoped evidence, and load
only the atomic skills needed to advance the chain:

```text
machine-ready + parity-complete
  → motor-config-edit
  → deploy-environment-ready
  → deploy-config-ready
  → deploy-complete
  → motor-smoke
```

Do not merge the stages or recreate their commands in this dispatcher. Consume
explicit run IDs and artifacts; do not infer readiness from ambiguous `last_*`
pointers.

## Execution boundaries

- Classify the request before routing:
  - Explain only when the user asks how deployment works.
  - Treat “能不能起服务”, “是否具备部署条件”, “部署前检查”, and equivalent
    wording as authorization to execute read-only feasibility checks.
  - Treat an unambiguous imperative such as “拉起 Motor 服务” as authorization
    to advance the requested deployment workflow. Still obey every consent
    gate in the selected atomic skill and stop for missing target-defining
    inputs.
- For read-only feasibility:
  - Resolve and verify the machine, then run base `motor-deploy-preflight`
    without `--config-dir` when only the environment is in scope.
  - Do not apply, restart, stop, create a namespace, overwrite remote parity
    directories, or modify the user's source config.
  - For config-specific feasibility, operate on a copied config because
    preflight may write NodePort conflict avoidance into `user_config.json`.
    Consume existing explicit parity evidence and run configure/server-side
    dry-run only when the user asks about that concrete config; never apply it.
  - Report “base environment is ready” after base preflight. Claim that a
    concrete config passed deployment validation only after its config-specific
    checks; never claim the service will certainly start without apply/runtime
    evidence.
- For active fault injection or reliability validation:
  - Treat controlled fault construction, isolation, recovery, continued
    traffic, and recovery-timeline proof as Reliability validation.
  - The current workspace has no implemented Reliability execution skill. Stop
    before fault injection and report the missing capability.
  - Never route fault injection to `motor-functional`; it does not prove
    reliability. Never route it to `motor-diagnosis`; diagnosis only collects
    evidence after a deploy failure or observed fault.
  - When reliability validation is combined with deployment, disclose the
    unsupported final stage before deployment mutation and ask whether to
    proceed with only the supported config/deploy portion.
- Run read-only state inspection first so an existing valid stage is not
  repeated blindly.
- Never call this package's former config generators, validators, dry-run shell
  script, or standalone `deploy.py` path.
- Never claim a stage succeeded without inspecting its real result and evidence.
- Keep runtime state in `.motor-workspace-local/`; do not write secrets to
  tracked files.

## Report

State which atomic skill was selected, which explicit inputs/run IDs it used,
what evidence it produced, and the next required stage. If blocked, report the
exact missing input or failed evidence instead of bypassing the workflow.
