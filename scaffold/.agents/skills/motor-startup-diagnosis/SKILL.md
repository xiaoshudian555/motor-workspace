---
name: motor-startup-diagnosis
description: Automatically diagnose a failed Motor deploy or startup and route evidence across environment, deployer, configuration, and runtime-code causes. Use when deploy.py fails, workloads do not become Ready, endpoints are missing, or Coordinator readiness times out.
---

# Motor startup diagnosis

This is the read-only failure entrypoint for the Motor startup path. Reuse
`motor-diagnosis` for common evidence collection, then load only the domain
diagnosis Skills needed by the observed failure. Do not create another deployer
or require a workspace run ID.

## Entry conditions

Enter automatically when any selected startup stage fails or reaches its bounded
timeout:

- native `deploy.py` (including `--dry-run`) exits non-zero;
- expected workloads are absent or not Ready;
- required Services or endpoints are absent;
- Coordinator `/readiness` does not reach `ready=true`.

Preserve the first failure before any retry or mutation. Record the endpoint,
kube context, namespace, failed stage, UTC time window, exact command and exit
status when available, expected result, and observed result. A failure before
the namespace or Pods exist is still diagnosable; collect only evidence that
can exist at that stage.

## Routing

First use `motor-diagnosis` to collect current resources, events, describes,
current/previous logs, and native log-collector artifacts when available. Also
preserve deploy stdout/stderr and the generated manifests when the deployer ran.

Choose one or more domain Skills from the evidence; do not run all four by
default:

| Evidence or failed stage | Candidate Skill |
|---|---|
| Cluster API, RBAC, operator, scheduler, node, NPU, image access, mount, storage, or network | `motor-diagnosis-environment` |
| Argument handling, deployer traceback, template/YAML generation, or apply orchestration | `motor-diagnosis-deployer` |
| Native config validity or intent/config/YAML/ConfigMap/Pod drift | `motor-diagnosis-config` |
| A Motor component or engine process starts and then crashes, hangs, or fails registration | `motor-diagnosis-runtime-code` |

The failed stage is a routing hint, not proof. Expand into another domain when
new evidence crosses the boundary. Prefer the owner of the smallest corrective
change as the primary category, and report other proven factors as contributing
causes. Examples: a bad image value is config; valid image plus registry access
failure is environment; a correct image dropped during YAML generation is
deployer.

## Freedom and stopping rule

The Agent may choose the most discriminating read-only commands, correlate
evidence across components, inspect native config and generated YAML, and search
the checked-out Motor/vLLM/vLLM-Ascend source. Do not force a category, rely on
an exhaustive regex list, or infer a code defect from a symptom alone.

Stop when either a causal evidence chain supports a root cause, or the remaining
hypotheses cannot be distinguished with available read-only evidence. In the
second case return `unknown`, the missing evidence, and the smallest next safe
check. Diagnosis never authorizes retry, restart, delete, repair, config edit,
scale, namespace creation, or fault injection.

## Output

```markdown
## 结论
{failed stage} — {primary category or unknown} — {one-line cause}

## 分类
| Role | Category | Confidence | Reason |
|---|---|---|---|

## 证据链
| Time | Source | Observation | Inference |
|---|---|---|---|

## 已排除与缺失证据
{alternatives ruled out; missing evidence}

## 下一步
{smallest safe action; mark any mutation as requiring new consent}
```
