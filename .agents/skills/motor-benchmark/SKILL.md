---
name: motor-benchmark
description: Run benchmarks against a successful motor-k8s-deploy run (second phase).
---

# motor-benchmark

Second-phase skill. Input is a successful deploy run with OpenAI endpoint ready.

Placeholder wrapper — extend when P3 environment validation begins.

```bash
python3 .agents/skills/motor-benchmark/scripts/bench_plan.py --machine <alias> --deploy-run-id <id>
```
