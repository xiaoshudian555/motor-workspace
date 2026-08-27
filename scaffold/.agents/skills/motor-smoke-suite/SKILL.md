---
name: motor-smoke-suite
description: Orchestrate the Pymotor post-deploy acceptance suite and automatically route failed stages into read-only diagnosis. Use for Pymotor 冒烟测试, 冒烟验收, full acceptance, or testing a deployed Motor service end to end. Minimal Coordinator readiness alone belongs to motor-smoke.
---

# Motor smoke suite

Orchestrate existing atomic Skills against the same endpoint, kube context,
namespace, native config, and live deployment. Do not reimplement their commands
or turn this Skill into another deployer.

Read `references/suite-profiles.md` for the stage list and per-stage pass
criteria. Read `references/failure-routing.md` before executing or diagnosing a
failed stage.

## Scope

This suite runs the **full Pymotor acceptance** profile only:

- Phase A — read-only status, readiness, and inference smoke.
- Phase B — authorized scaling, RAS fault experiments, accuracy, and performance.
  Each Phase B stage requires its owning Skill's explicit consent in the same run.

There is no separate `minimal` profile; minimal readiness alone belongs to
`motor-smoke`.

Route each stage to its owner:

| Stage | Owning Skill |
|---|---|
| Deploy or create the service when explicitly requested | `motor-deploy` dispatcher |
| Current K8s workload and Service status | `motor-k8s-deploy` status |
| Coordinator `GET /readiness` | `motor-smoke` |
| Non-stream and stream inference | `motor-functional` `inference-request` cases |
| Online instance scaling | `motor-scale` |
| Explicit RAS/fault-recovery scenario | `motor-reliability` |
| GPQA / accuracy execution and raw metric extraction | `motor-accuracy` |
| Explicit performance workload | `motor-benchmark` |
| Valid low-performance result attribution | `motor-performance-analysis` |
| Failure evidence | `motor-diagnosis` |

Do not include Phase B stages merely because the user says smoke. Add them only
when the user explicitly selects them and the owning Skill's prerequisites are
satisfied.

## Suite-owned assertions

The suite applies these acceptance gates on top of each owning Skill's raw
output:

| Stage | Raw evidence owner | Suite assertion |
|---|---|---|
| `motor-accuracy` GPQA | `motor-accuracy` | `abs(accuracy - 0.807) <= 0.01` **or** `accuracy > 0.807`; hard FAIL when `< 0.797` |
| `motor-benchmark` performance | `motor-benchmark` | baseline supplied or confirmed by the user; never invented |

Other stages pass only when their owning Skill's complete pass condition is
observed. The suite never converts missing evidence into PASS.

## Execution contract

1. Resolve the endpoint, kube context, namespace/job ID, native
   `user_config.json`, served model name, and current Coordinator Services once.
   Revalidate a fact if live state changes; do not guess missing values.
2. Present the `pymotor-acceptance` stage list from `suite-profiles.md`, including
   pass criteria, consent requirement, and owning Skill for each selected stage.
3. Before execution, show the selected stages, target endpoint, namespace,
   expected read-only evidence collection, and any separately authorized
   mutation, scaling, fault injection, or AISBench load.
4. If deployment is part of the request, enter the `motor-deploy` dispatcher.
   Preflight and dry-run are read-only; config mutation, deploy, restart, stop,
   scaling, fault injection, and AISBench load still require their owning
   workflow's explicit consent.
5. Run Phase A automatically unless excluded by the user. A blocking failure
   stops downstream stages whose prerequisites are no longer valid.
6. For Phase B, stop after Phase A unless the user approves the next mutating or
   long-running stage. Re-baseline topology and inference before each RAS or
   scale stage when live state may have changed.
7. On FAIL or unresolved timeout, immediately run the read-only failure route
   from `references/failure-routing.md`. This invocation authorizes only current
   state, event, log, endpoint, and artifact inspection. It does not authorize
   restart, delete, repair, config edit, scaling, or fault injection.
8. Always clean up temporary port-forwards and monitored client processes.
9. Keep raw evidence in the conversation unless the user supplied an artifact
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

## Profile
pymotor-acceptance

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
