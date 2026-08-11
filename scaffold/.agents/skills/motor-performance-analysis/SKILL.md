---
name: motor-performance-analysis
description: Analyze Motor inference performance anomalies from benchmark results, Coordinator logs, Motor/vLLM metrics, engine logs, and traces. Quantify Motor scheduling cost, characterize Prefill/Decode service behavior, narrow the owning layer, and decide whether deeper vLLM-Ascend profiling is justified. Use for Motor performance regressions, high TTFT/TPOT/E2E latency, low throughput, suspected scheduling overhead, P/D imbalance, or questions about whether a bottleneck belongs to Motor or vLLM-Ascend. Do not use for deployment, generic startup/failure diagnosis, or log-quality review.
---

# Motor performance analysis

Perform read-only performance triage. Identify the responsible layer before
recommending profiler collection or code changes. Do not act as a deployment
dispatcher or a deep operator profiler.

Read these references before interpreting evidence:

- `references/metric-semantics.md` for current Motor log and metric meanings.
- `references/responsibility-boundary.md` for attribution and escalation rules.
- `references/report-contract.md` before writing the result.

Re-read the matching source code when the Motor, vLLM, or vLLM-Ascend revision
differs from the revision that produced the evidence. Treat source semantics as
versioned facts, not permanent conventions.

## Workflow

1. Inventory the evidence and record its source, time range, software revision,
   model, hardware, topology, engine config, workload, and warmup state. Prefer
   raw benchmark output, Coordinator logs, P/D engine logs, metrics snapshots,
   and request traces over summaries.
2. Validate the performance observation. Confirm successful request counts and
   workload validity. Compare runs only when hardware, model, topology, engine
   config, input/output distribution, concurrency or request rate, dataset, and
   benchmark version are compatible. Otherwise report absolute behavior and the
   specific comparability gaps.
3. Align all artifacts to the formal measurement window. Separate startup,
   warmup, steady state, and teardown. Never combine timestamps from unrelated
   runs merely because filenames look related.
4. Establish the client-visible symptom using QPS, token throughput, TTFT,
   TPOT/ITL, E2E latency, tail latency, and success rate. Preserve the raw metric
   names and units.
5. Estimate Motor-attributable scheduling cost from `select_and_allocate`,
   `late_select_d`, and, when DEBUG evidence exists, `get_http_client`. Report
   stage distributions, roles, retries, sample count, and sampling coverage.
   Give an exact per-request sum only when request-correlated trace evidence
   proves the stages are additive; otherwise report their scale and bounds.
6. Characterize downstream service behavior using active/inactive P/D workers,
   prompt/generation TPS, queue/running gauges, engine latency histograms, and
   topology changes. State whether each value is endpoint-, instance-, role-, or
   service-scoped.
7. Check handoff and transfer evidence only when timestamps or traces span both
   P and D. Absence of such evidence means the P/D transfer layer is unresolved,
   not healthy.
8. Classify the anomaly using the responsibility domains in
   `references/responsibility-boundary.md`. For every candidate, list supporting,
   refuting, and missing evidence with a confidence level.
9. Recommend the smallest next measurement that can discriminate the remaining
   candidates. Escalate to deep vLLM-Ascend profiling only when its gate passes.

## Routing and safety

- Use `motor-benchmark` when a controlled reproduction or compatible baseline
  must be generated; do not silently start load from this Skill.
- Use `motor-diagnosis` for failed requests, unhealthy Pods, crashes, timeouts,
  or missing service evidence before interpreting performance.
- Keep collection read-only by default. Profiler collection, new benchmark load,
  restart, scaling, config edits, or fault injection require the authorization
  of their owning workflow.
- Do not claim an operator, kernel, HCCL, memory, or vLLM-Ascend root cause from
  Coordinator and aggregate metrics alone.
