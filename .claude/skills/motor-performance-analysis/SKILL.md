<!-- Generated Claude Code shim from scaffold/.agents/skills/motor-performance-analysis/SKILL.md. Do not edit. -->
---
name: motor-performance-analysis
description: Analyze Motor inference performance anomalies from benchmark results, Coordinator logs, Motor/vLLM metrics, engine logs, and traces. Quantify Motor scheduling cost, characterize Prefill/Decode service behavior, narrow the owning layer, and decide whether deeper vLLM-Ascend profiling is justified. Use for Motor performance regressions, high TTFT/TPOT/E2E latency, low throughput, suspected scheduling overhead, P/D imbalance, or questions about whether a bottleneck belongs to Motor or vLLM-Ascend. Do not use for deployment, generic startup/failure diagnosis, or log-quality review.
---

# Motor performance analysis

Canonical skill source:

`scaffold/.agents/skills/motor-performance-analysis/SKILL.md`

Before using this skill:

1. Read the canonical skill file above.
2. Follow its routing rules, entrypoints, guardrails, and acceptance criteria.
3. Use `.remote-dev` companion tools for ordinary remote endpoint read/edit/bash/search/patch work.
4. Use this Claude project skill only for the domain workflow described by the canonical source.
