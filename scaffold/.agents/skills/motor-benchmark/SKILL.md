---
name: motor-benchmark
description: Run repeatable aisbench online-serving performance validation against a successful Motor deploy. Use for throughput, latency, prefix-cache, fixed-concurrency or fixed-request-rate benchmarks; use stress-capacity for saturation testing and profiling for bottleneck attribution.
---

# motor-benchmark

Consume a successful `deploy-complete` run and execute an explicitly defined
online-serving workload. Read `references/aisbench.md` before preparing or
running aisbench.

## Workflow

1. Validate the deploy evidence chain and machine binding:

```bash
python3 .agents/skills/motor-benchmark/scripts/bench_plan.py \
  --machine <alias> \
  --deploy-run-id <id>
```

This command validates only the upstream deploy. It does not execute a
benchmark and must not be reported as `benchmark-complete`.

2. Resolve values from repository evidence before asking the user:
   - model name, namespace, config bundle, and `max_model_len` from the deploy
     run and immutable `user_config.json`;
   - target Coordinator inference Service from the deployed namespace;
   - machine endpoint from inventory.
3. Ask only for values that cannot be derived: benchmark container or host,
   aisbench working directory, dataset, workload shape, and comparison
   baseline. Never invent paths, model names, DP size, or target addresses.
4. Present the exact workload and pass/stop criteria before generating material
   load. Record input/output lengths, request count, concurrency, request rate,
   dataset mode, prefix-cache settings, warmup, model, topology, and hardware.
5. Run a small smoke workload, then the formal workload. Use `.remote-dev`
   tools or the skill wrappers for remote operations; do not use raw SSH.
6. Stop on a gate or repeated deterministic failure. Do not keep retrying an
   invalid request or summarize an all-failed run as a performance result.
7. Save the command, resolved config, environment fingerprint, raw CSV/log,
   aggregated metrics, service logs, and conclusion under the run-scoped
   validation evidence directory.

## Required result

Report at least QPS, output and total throughput, TTFT average/P90, TPOT
average, E2E average, and successful/failed request counts. Keep absolute
performance results separate from regression conclusions; compare only against
a baseline with matching hardware, model, topology, config, and workload.

## Boundaries

- Require `input_len + output_len <= max_model_len` before any workload.
- Require Coordinator/gateway readiness and an exact served model name.
- Treat HTTP 400/500, `RECV=0`, empty metrics, and repeated identical bad
  requests as failed evidence, not low performance.
- Use `--prefix_test` when prefix-cache hit-rate evidence is required. In P/D
  deployments, configure `POD_INFO` and DP scope before claiming cache metrics.
- Do not install or upgrade aisbench automatically. If the tool or dependency is
  missing, report the exact blocked command and target environment.
- Do not use this skill to find the saturation point, attribute bottlenecks,
  claim model accuracy, or declare a dummy-weight result representative of a
  real model.
