---
name: motor-smoke-suite
description: Orchestrate a Pymotor post-deploy smoke acceptance suite and automatically route failed stages into read-only diagnosis. Use for Pymotor 冒烟测试, 冒烟验收, or testing a deployed Motor service end to end. Minimal Coordinator readiness alone belongs to motor-smoke.
---

# Motor smoke suite

Orchestrate existing atomic Skills against the same endpoint, kube context,
namespace, native config, and live deployment. Do not reimplement their commands
or turn this Skill into another deployer.

Read `references/failure-routing.md` before executing or diagnosing a failed
stage.

## Scope

The default suite for an already deployed service is:

```text
read-only deployment status
→ Coordinator management readiness
→ non-stream inference
→ stream inference
```

Route each stage to its owner:

| Stage | Owning Skill |
|---|---|
| Deploy or create the service when explicitly requested | `motor-deploy` dispatcher |
| Current K8s workload and Service status | `motor-k8s-deploy` status |
| Coordinator `GET /readiness` | `motor-smoke` |
| Non-stream and stream inference | `motor-functional` `inference-request` cases |
| Explicit RAS/fault-recovery scenario | `motor-reliability` |
| Explicit performance workload | `motor-benchmark` |
| Valid low-performance result attribution | `motor-performance-analysis` |
| Failure evidence | `motor-diagnosis` |

Do not include benchmark merely because the user says smoke. Add it only when
the user supplies or confirms the formal workload and baseline required by
`motor-benchmark`.

Reliability is available only for scenarios explicitly supported by
`motor-reliability`; it is never part of the default suite and requires the
scenario's separate fault-injection consent. GPQA-style correctness is not
currently implemented. Report unsupported scenarios as **NOT RUN / CAPABILITY
GAP**; never infer them from readiness or inference success.

## Execution contract

1. Resolve the endpoint, kube context, namespace/job ID, native
   `user_config.json`, served model name, and current Coordinator Services once.
   Revalidate a fact if live state changes; do not guess missing values.
2. Before execution, show the selected stages, pass criteria, target endpoint,
   namespace, expected read-only evidence collection, and any separately
   authorized mutation or load.
3. If deployment is part of the request, enter the `motor-deploy` dispatcher.
   Preflight and dry-run are read-only; config mutation, deploy, restart, stop,
   scaling, and fault injection still require their owning workflow's explicit
   consent.
4. For an existing deployment, run the default suite in order. A blocking
   failure stops downstream functional or benchmark stages whose prerequisites
   are no longer valid.
5. On FAIL or unresolved timeout, immediately run the read-only failure route
   from `references/failure-routing.md`. This invocation authorizes only current
   state, event, log, endpoint, and artifact inspection. It does not authorize
   restart, delete, repair, config edit, scaling, or fault injection.
6. Always clean up temporary port-forwards and monitored client processes.
7. Keep raw evidence in the conversation unless the user supplied an artifact
   path. Failure evidence may use an untracked
   `.motor-workspace-local/diagnosis/<namespace>-<timestamp>/` directory as
   allowed by `motor-diagnosis`. Do not create a workflow gate or make later
   Skills depend on a local run ID.

## Stage semantics

- **PASS**: the owning atomic Skill's complete pass condition was observed.
- **FAIL**: a required assertion failed after its bounded wait or valid
  execution.
- **BLOCKED**: a prerequisite, permission, path, feature, or tool is missing.
- **NOT RUN**: excluded by scope or skipped because an earlier stage blocked it.
- **CAPABILITY GAP**: the workspace has no trustworthy execution contract for
  the requested scenario.

Overall PASS requires every selected required stage to PASS. Optional stages do
not change the overall result unless the user made them required. Never report
PASS when a required stage is BLOCKED, NOT RUN, or a CAPABILITY GAP.

## Output

Return one compact report:

```markdown
## 结论
PASS | FAIL | BLOCKED — {one-sentence reason}

## 阶段结果
| Stage | Status | Expected | Observed | Evidence |
|---|---|---|---|---|

## 失败定位
{failed stage, diagnosis category, strongest evidence, confidence}

## 下一步
{smallest safe action; explicitly mark any action that needs new consent}
```

When diagnosis cannot identify a root cause, say `未定位到根因`, list the
missing evidence, and recommend the smallest discriminating read-only check.
Do not recommend code changes from symptoms alone.
