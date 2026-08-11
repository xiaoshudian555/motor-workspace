# Performance triage report contract

Lead with the ownership conclusion. Use `insufficient evidence` when the data
cannot support attribution.

## Required sections

### 1. Conclusion

- State whether a performance anomaly is established.
- Name the primary responsibility domain and confidence: high, medium, or low.
- State whether deep vLLM-Ascend analysis is justified now.

### 2. Evidence quality

Record the measurement window, revisions, model, hardware, topology, workload,
warmup, success/failure counts, baseline compatibility, missing artifacts, and
Coordinator scheduling sample count. Separate observed fact from inference.

### 3. Cross-layer decomposition

Use one row per non-overlapping observation where possible:

| Layer or stage | Metric | Distribution/trend | Scope | Interpretation |
|---|---|---|---|---|

At minimum cover client outcome, Motor scheduling, P/D topology, Prefill service,
Decode service, and handoff/transfer. Mark unavailable dimensions explicitly.
Do not manufacture an additive latency budget from overlapping stages.

### 4. Candidate evidence chain

For every plausible domain, provide:

| Candidate | Supporting evidence | Refuting evidence | Missing discriminator | Confidence |
|---|---|---|---|---|

Attach each material fact to its artifact path, timestamp or line, metric labels,
and extraction query or calculation. Avoid generic advice not tied to evidence.

### 5. Next action

Recommend the smallest discriminating measurement or experiment, including its
hypothesis, fixed variables, observed metric, and pass/fail interpretation. If
the deep-analysis gate passes, provide the profiler handoff contract rather than
claiming a bottom-layer root cause.

## Hard correctness checks

- Never treat `forward_to_engine_first_chunk` as pure Motor overhead.
- Never treat Coordinator `count_token` as output token count.
- Never describe Coordinator `TTOT` as total output time or token-aware TPOT.
- Never equate `Deferred > 0` with full KV cache.
- Never compare incompatible workloads as a performance regression.
- Never infer operator/kernel/HCCL root cause without corresponding deep evidence.
