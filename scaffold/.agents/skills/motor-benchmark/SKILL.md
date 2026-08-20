---
name: motor-benchmark
description: Run repeatable AISBench online-serving performance validation against a live Motor deployment. Use for 压测, AISBench, 打流, prefix cache, QPS, TTFT, or TPOT. Prefer native ais_bench; use the legacy prefix wrapper only for capabilities native AISBench cannot provide. Performance attribution belongs to motor-performance-analysis.
---

# motor-benchmark

Read `references/aisbench.md` before constructing commands. This Skill generates controlled
online load and evidence; it does not attribute a performance bottleneck. There is no
`bench_plan.py` or `deploy-complete` prerequisite.

## Route the request

| Request | Route |
|---|---|
| Basic performance, fixed/variable length, supplied dataset, rate/concurrency, stable-stage | Native `ais_bench` |
| Prefix ratio/count, seeded prefix dataset, per-DP prefix warmup, automatic HBM/external hit-rate evidence | `aisbench_auto_tools_prefix` wrapper |
| High TTFT/TPOT, low throughput, regression attribution | `motor-performance-analysis`; do not start load here |
| Failed requests, unhealthy Pods, unreachable service | `motor-smoke` or `motor-diagnosis` first |

Native AISBench is the default. Do not select the wrapper because it is familiar or shorter.
If native AISBench can express the workload and produce the required evidence, using the
wrapper is unnecessary. Record `EXECUTION_BACKEND=native|prefix-wrapper` and why.

## Resolve facts before asking

Derive from the current native `user_config.json`, machine inventory, and live K8s:

- served model name; all active engine sections must agree;
- namespace / `job_id` and a reachable Coordinator inference endpoint;
- `max_model_len`; all active engine sections must agree;
- P/D topology, DP, instance count, hardware, and software revisions.

Do not guess paths. Ask when the selected backend's container, AISBench root, tokenizer/model
path, dataset path, or dedicated runtime root cannot be derived. The wrapper additionally
requires its root and a dedicated mutable AISBench `WORK_PATH`.

The formal request count, input/output shape, concurrency, and arrival rate must be supplied or
confirmed. Do not invent a formal profile or baseline.

## Preflight and capability gate

1. Map the request to the selected backend using `aisbench.md`.
2. Require `input_len + output_len <= max_model_len`; for a distribution use its maximum.
   After smoke, validate the actual token distribution because tokenizer round-trips and chat
   templates can change the nominal input length.
3. Require Coordinator `/readiness` HTTP 200 with `ready=true`, inference reachability from the
   load generator, and exact served-model-name agreement.
4. Probe, but never install or upgrade, the existing environment. Record Python and AISBench
   versions, installation/source revision, `ais_bench --help`, and the supported output schema.
   For the wrapper, also record its commit or file hashes and `aisbench_test.py --help`.
5. Use a run-scoped, user-approved mutable runtime directory. Never edit tracked AISBench or
   wrapper source, and never share one mutable `WORK_PATH` between concurrent runs.
6. API keys must remain reference-based. The current prefix wrapper writes `API_KEY` into
   generated Python; until a runtime copy reads it from a secret reference or environment
   variable without persisting it, authenticated wrapper runs fail closed.

## Execute

Before load, show the resolved config, smoke command, formal command, output paths, selected
backend, runtime mutations, and stop conditions.

- Run a smoke workload smaller in both request count and concurrency than the confirmed formal
  workload. Do not use an invented fixed smoke profile.
- Formal native AISBench must not use `--debug`; it can make the load generator single-core and
  client-limited.
- The upstream prefix wrapper hard-codes `--debug`. For a formal wrapper run, use only a
  run-scoped runtime copy with that flag removed and the exact diff recorded. Do not patch the
  shared wrapper or AISBench installation.
- Run long workloads as one monitored remote job. Do not time out a synchronous command and
  start a duplicate.
- Do not use wrapper `--repeat > 1` for formal evidence; invoke and archive each repetition as a
  separate run.

## Result correctness gate

Treat native AISBench JSON/JSONL/CSV as authoritative. Wrapper `aisbench_result.csv` is a
secondary convenience summary.

A formal run is valid only when all of the following hold:

- explicit total, success, and failed request counts were extracted; `total_req` is not a
  success count;
- no core wrapper field remains at sentinel `99999` or `9999`, and no required field is empty;
- wrapper CSV values agree with the corresponding native AISBench artifact when the wrapper is
  used;
- target versus achieved request rate and average/max concurrency were recorded; a formal run
  that does not reach the target is labeled `client-limited`, not a Motor result;
- actual input/output token distributions fit the context gate;
- the measurement contains no repeated Bad Request, all-failed traffic, empty perf result, or
  stale artifact from an earlier run.

For prefix runs, `repeat_rate` is the constructed common-prefix fraction (`0.5` and `50%` both
mean 50%), not the observed hit rate. Preserve raw before/after metrics. Report warmup and formal
hit rates separately, require every expected Pod/engine to be covered, and mark the rate
unavailable on counter reset, mixed traffic, skipped scope, or incomplete evidence.

## Evidence and report

Archive immediately under:

```text
.motor-workspace-local/benchmark-runs/<namespace>-<timestamp>/
```

Preserve a manifest, exact command, redacted resolved configs, environment/version fingerprint,
dataset generation parameters and checksums, complete native AISBench timestamped output,
wrapper log/CSV when used, raw prefix metrics, and aggregated metrics.

Report success/failure counts, benchmark duration, achieved concurrency/rate, request and token
throughput, TTFT, TPOT/ITL, E2E, actual token distributions, raw output paths, and comparability
gaps. Compare to a baseline only when hardware, model/tokenizer, software revisions, topology,
engine config, workload, generation parameters, warmup/cache state, client environment, and
benchmark backend/version match.
