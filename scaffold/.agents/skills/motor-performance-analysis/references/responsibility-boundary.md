# Performance responsibility boundary

Assign the narrowest domain supported by evidence. Multiple domains may
contribute; distinguish the primary limiting layer from secondary effects.

| Domain | Supporting evidence | Evidence that weakens it |
|---|---|---|
| Client or workload | Invalid/failed requests, client pacing limit, changed length mix, incompatible baseline, timeout/disconnect pattern | Same valid workload reproduces against a controlled baseline |
| Motor scheduling | `select_and_allocate` or `late_select_d` is large, variable, retry-heavy, and material relative to E2E/TTFT; scheduler/resource contention aligns with the regression | Motor-attributable stages are small and stable while downstream time grows |
| Topology or worker availability | Active P/D count drops, inactive count rises, endpoints churn, load is persistently imbalanced | Worker population and distribution remain stable during the anomaly |
| P/D handoff or KV transfer | Cross-role trace shows a gap after Prefill and before Decode; transfer/connector evidence aligns with it | No aligned P-to-D evidence; this leaves the domain unresolved rather than refuted |
| Prefill service | Prompt TPS falls or prefill/queue time rises for a stable prompt workload, concentrated on P workers | Prefill metrics are stable while Decode and client output latency degrade |
| Decode service | Generation TPS falls, TPOT/decode time or Decode queue rises for a stable output workload | Decode metrics are stable while only Prefill/TTFT degrades |
| vLLM/vLLM-Ascend internal candidate | Valid regression is inside P/D service, Motor scheduling and topology are not dominant, and surface evidence cannot separate operator, communication, memory, or engine scheduling causes | The anomaly is already explained by workload, Motor, topology, or handoff evidence |
| Insufficient evidence | Time windows, workload, labels, baseline, or cross-layer correlation are missing or contradictory | A reproducible evidence chain isolates one of the domains above |

## Deep-analysis escalation gate

Recommend vLLM-Ascend benchmark/profiling analysis only when all applicable
conditions hold:

1. A valid workload reproduces the client-visible anomaly, or a compatible
   baseline demonstrates a regression.
2. Motor-attributable scheduling stages are measured and are not the dominant
   contribution, or Motor evidence explicitly cannot explain the delta.
3. P/D topology and worker availability are known for the measurement window.
4. The anomaly is visible in engine queue, Prefill, Decode, TPOT, TPS, transfer,
   or resource evidence.
5. Existing surface evidence cannot distinguish an internal engine, operator,
   HCCL, memory, or kernel cause.

If the gate fails, request the missing top-layer evidence instead of collecting
a profiler trace. If it passes and a repository-local deep Skill is available,
hand it the reproduction workload, exact time window, target P/D ranks, suspected
phase, baseline, and the evidence supporting escalation.

Imported deep-analysis Skills must preserve the provider repository, original
Skill path, source commit, license, and a concise list of Motor-local changes.
Do not copy their machine/session management when the existing motor-workspace
remote tools already provide that substrate.
