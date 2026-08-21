---
name: motor-diagnosis-environment
description: Diagnose external MindCluster and Kubernetes platform causes of Motor failures, including API access, operators, scheduling, NPU resources, images, storage, node runtimes, and networking. Use when valid Motor deployment intent cannot be created, scheduled, started, or connected. This is read-only diagnosis, not configuration editing, node maintenance, or authorization to repair the cluster.
---

# Motor environment diagnosis

Locate the earliest platform contract that is proven not to hold. Preserve the
evidence that establishes the last successful handoff and the first failed
handoff, then distinguish the root cause from downstream symptoms.

Read [references/principles.md](references/principles.md) before diagnosing an
incident. It defines the stable responsibility model, symptom routing, and
evidence standard. Choose current inspection methods from the actual endpoint,
component deployment mode, and installed versions; do not rely on a fixed list
of commands or paths.

## Scope and routing

This Skill owns external platform prerequisites: Kubernetes API reachability,
authorization and admission; object expansion; cluster topology and fault
views; scheduling; NPU discovery and allocation; image, mount and storage
availability; node/runtime health; and cluster service or Pod networking.

- For a Motor startup failure, preserve the common evidence through
  `motor-startup-diagnosis`; this Skill is its environment-domain diagnosis.
- Do not classify a Motor registration failure as networking merely from its
  symptom. Effective configuration, endpoints, connectivity, and the first
  failing process must distinguish environment, configuration, and runtime-code
  causes.
- Fixed Prefill/Decode placement is deployment intent and belongs to
  `motor-config-edit`, including verification that the current Motor version
  supports the requested placement representation.
- Worker removal, reset, or rejoin is outside diagnosis. If it is later chosen
  as an approved recovery action, use the safety principles in
  [worker-node-rejoin.md](../../../docs/mindcluster-worker-node-rejoin.md).
- Diagnosis does not authorize configuration edits, workload deletion,
  component restart, fault-policy changes, CNI changes, or node maintenance.

## Incident context

Resolve the target endpoint and Kubernetes context before collecting evidence.
Record the deployment mode, namespace, affected objects, nodes and NPU IDs,
failure time window, impact scope, last known good state, and relevant recent
changes.

Run cluster inspections against the selected context. Run host inspections on
the intended cluster node, not on the local workstation or an arbitrary access
host. Redact credentials, registry secrets, tokens, and unrelated tenant data.

## Diagnosis workflow

1. State the observed failure without assigning a cause.
2. Map the affected path onto the responsibility model in the reference.
3. Establish which upstream contracts succeeded and identify the earliest
   contract with direct failure evidence.
4. For each plausible cause, record supporting evidence, conflicting or
   missing evidence, and the smallest read-only check that would distinguish it.
5. Expand collection only into the relevant namespace, component, node, device,
   or time window. Use cluster-wide collection only when the impact or evidence
   is genuinely cluster-wide.
6. Stop when one causal chain is supported, or when remaining hypotheses cannot
   be distinguished with available read-only evidence. In the latter case,
   return `unknown` and request the smallest additional evidence set.

Treat current state, Events, object ownership, effective specifications,
component logs, node/device views, and network observations as evidence only
when their source, object identity, and time window are clear. A `Running` Pod,
healthy-looking component process, restart-based recovery, or isolated log line
is not sufficient proof.

## Recovery guidance

Recommend a recovery action only after the failed contract is established.
Separate the proposal from execution and state its risk, rollback, and
verification path. Any mutation requires fresh approval for the exact target.

Never recommend hiding an NPU fault, disabling kubelet safety protections, or
reinstalling/resetting a cluster component merely to make the visible symptom
disappear. Verify recovery through the same contract path that failed and over
an observation window appropriate to the incident.

## Output

```markdown
## 结论
- 故障边界：
- 根因或当前最强假设：
- 置信度：高/中/低
- 影响范围：

## 证据链
| 时间 | 对象/来源 | 观察 | 推断 |
|---|---|---|---|

## 已排除与缺失证据
| 假设 | 支持/反证 | 最小判别检查 | 预期判别结果 |
|---|---|---|---|

## 恢复建议
| 操作意图 | 风险 | 回滚 | 验证 | 是否需要审批 |
|---|---|---|---|---|
```

Label inference as inference. Use exact object identities and timestamps when
available; do not turn a plausible component into a proven root cause.
