# AISBench native-first online benchmark contract

## Contents

- [Backend selection](#backend-selection)
- [Inputs and workload mapping](#inputs-and-workload-mapping)
- [Shared gates](#shared-gates)
- [Native AISBench path](#native-aisbench-path)
- [Prefix wrapper path](#prefix-wrapper-path)
- [Result validation](#result-validation)
- [Evidence layout](#evidence-layout)
- [Stop conditions](#stop-conditions)

## Backend selection

Use native `ais_bench` unless the requested result needs a wrapper-only capability.

| Capability | Native `ais_bench` | Prefix wrapper |
|---|---:|---:|
| Basic streaming performance | Preferred | Do not use |
| Fixed or distributed input/output shape | Preferred | Supported but unnecessary |
| Supplied compatible dataset | Preferred | Only GSM8K-format input |
| Request count, concurrency, arrival rate | Preferred | Supported but unnecessary |
| Stable-stage summarization | Preferred | Partial passthrough only |
| Common-prefix fraction, multiple prefix pools, seed | Insufficient for the current Motor SOP | Required |
| Per-DP prefix warmup plus HBM/external hit-rate collection | Not integrated | Required |

The wrapper is
[`rayn-zzz/aisbench_auto_tools_prefix`](https://github.com/rayn-zzz/aisbench_auto_tools_prefix).
Its current role is a narrow prefix-cache adapter, not the general benchmark frontend.

## Inputs and workload mapping

Resolve these from native Motor config and live state when possible:

| Value | Requirement |
|---|---|
| `MODEL_NAME` | All active engines' `served_model_name` agree |
| endpoint | Coordinator inference endpoint reachable from the load generator |
| `max_model_len` | All active engine sections agree |
| topology | P/D instances, DP domains, hardware, image/package revisions |
| tokenizer/model path | Existing path used for exact dataset tokenization |
| formal shape | User-confirmed count, input/output lengths or distributions, concurrency and rate |

Do not guess `BENCH_CONTAINER`, native AISBench root, dataset path, runtime root, wrapper root, or
wrapper `WORK_PATH`.

### Natural language mapping

| User intent | Native setting | Wrapper setting |
|---|---|---|
| request/sample count | synthetic `RequestCount` and/or `--num-prompts N` | `--data_num N` |
| input length | run-scoped synthetic dataset config | `--input_len N` |
| output length | model `max_out_len`, synthetic output config, `ignore_eos=True` when exact length is required | `--output_len N` |
| concurrency | model `batch_size` | `--concurrency N` |
| arrival rate | model `request_rate` | `--request_rate N` |
| stable stage | `--summarizer stable_stage` | `DEFAULT_PERFORMANCE_TEST=stable_stage`; native is preferred |
| common-prefix fraction | not the Motor primary path | `--repeat_rate R` |
| prefix pools / seed / DP warmup | not integrated | `--prefix_num`, `--seed`, `--dp`, `--prefix_test` |

`request_rate` is the sending-side target, not measured QPS. Confirm its semantics from the
installed AISBench version; current upstream treats values below `0.1` as unlimited. Timestamp
or traffic-distribution scheduling can override it.

## Shared gates

### Context length

Require:

```text
maximum_requested_input + maximum_requested_output <= max_model_len
```

For nominal synthetic lengths, also inspect smoke artifacts. Tokenizer encode/decode and chat
templates can change the actual prompt length. Stop if any actual request exceeds the context
limit or if exact output length was requested but `ignore_eos`/backend behavior did not produce
it.

### Service

- Coordinator management `/readiness`: HTTP 200 and JSON `ready=true`.
- Inference endpoint: reachable from the actual benchmark environment.
- Served model: exact match.
- Streaming performance: use a streaming service model/backend.
- Warmup: recorded and excluded from the formal measurement.

### Version capability

Capture before constructing commands:

```text
python3 --version
python3 -m pip show ais-bench-benchmark
ais_bench --help
AISBench source revision when available
```

Confirm support for the selected model class, `--mode perf`, `--work-dir`, `--num-prompts`,
`--num-warmups`, summarizer, and output schema. Do not install or upgrade packages.

For a wrapper run, additionally capture:

```text
python3 aisbench_test.py --help
git revision, or SHA256 of README.md, aisbench_test.py, generate_dataset.py,
cal_prefix_hit_rate.py, save_file.py, config.py, and default_api.py
```

## Native AISBench path

### Runtime config

Create a unique config/output area under the user-approved benchmark runtime root. Locate the
installed version's streaming vLLM model and dataset configs with `--search`; copy the smallest
required configs into the run-scoped area and edit only those copies. Do not edit the installed
AISBench config tree.

The resolved model config must record:

- tokenizer/model path and served model name;
- stream enabled, endpoint, request rate, batch size, retry, timeout behavior;
- maximum output length and all generation kwargs;
- secret reference mechanism, if authentication is required.

The resolved dataset config must record the dataset path or synthetic generation parameters,
including the requested count and input/output distribution.

### Commands

First validate config discovery/dry-run using the installed CLI. A representative formal shape
is:

```bash
ais_bench \
  --config-dir <RUN_CONFIG_DIR> \
  --models <RUN_MODEL_CONFIG> \
  --datasets <RUN_DATASET_CONFIG> \
  --mode perf \
  --summarizer <default_perf|stable_stage> \
  --work-dir <RUN_OUTPUT_DIR> \
  --num-prompts <DATA_NUM> \
  --num-warmups <CONFIRMED_WARMUPS>
```

Only include flags shown by the installed `--help`. Formal native runs must not include
`--debug`. Do not add accuracy flags to the performance path.

Archive the complete timestamped AISBench output, especially dumped configs, logs, performance
CSV/JSON, request details JSONL, and concurrency visualization.

## Prefix wrapper path

Use the wrapper only when the request needs prefix construction or automatic prefix metrics.

### Confirmed prefix semantics

`repeat_rate` accepts a decimal or percentage in `[0, 1]` / `[0%, 100%]`. It is the constructed
common-prefix fraction of each target input. For a fixed-length request the wrapper constructs
approximately:

```text
prefix_len = int(input_len * repeat_rate)
common prefix + 3 seed-controlled random tokens + suffix
```

It is not an observed cache hit rate. The observed rate is the delta:

```text
(hits_after - hits_before) / (queries_after - queries_before)
```

### Runtime isolation and mutation list

Use dedicated, non-shared, mutable copies of both wrapper and AISBench `WORK_PATH`. Before load,
show the exact paths that may be created or replaced:

- wrapper: `temp_api.py`, `aisbench.log`, `aisbench_all.log`, `aisbench_result.csv`, generated
  datasets, `picked_ids.txt`;
- AISBench `WORK_PATH`: model config `vllm_api_chat_temp.py`, GSM8K `test.jsonl`, and possibly
  `train.jsonl` plus timestamped output.

Never point the wrapper at tracked source or a shared `WORK_PATH`. The current wrapper persists
`API_KEY` in generated Python; authenticated runs fail closed unless the run-scoped copy has a
verified non-persisting secret-reference adaptation.

### Formal command

```bash
python3 aisbench_test.py \
  --input_len <INPUT_LEN> \
  --output_len <OUTPUT_LEN> \
  --data_num <DATA_NUM> \
  --concurrency <CONCURRENCY> \
  --request_rate <REQUEST_RATE> \
  --dataset_type prefix_cache \
  --repeat_rate <R> \
  --prefix_num <PREFIX_COUNT> \
  --seed <SEED> \
  --prefix_test \
  --dp <DP_SIZE>
```

The upstream wrapper hard-codes native AISBench `--debug`. A formal prefix run must use a
run-scoped wrapper copy with only that formal-load flag removed; save the exact diff and verify
the generated command. If the installed AISBench rejects `--num-warmups`, remove that unsupported
flag only in the run-scoped copy and record the compatibility diff. Never modify the shared
wrapper or third-party installation.

Do not use `--repeat > 1` for formal evidence. Run repetitions independently because the wrapper
convenience CSV represents only the last readily parsed result while logs and CSVs are also
append/overwrite prone.

### Prefix evidence

Save raw `/metrics` payloads immediately before and after both stages:

1. prefix warmup;
2. formal full-dataset workload.

Report `warmup_prefix_hit_rate` and `formal_prefix_hit_rate` separately for HBM and external
cache. Require exact expected Pod and engine sets and a positive warmup query delta for every DP
domain. Reject negative deltas/counter resets, duplicate model series, concurrent unrelated
traffic, skipped Pods, or incomplete series; in those cases hit rate is unavailable.

Only delete the exact run-local `picked_ids.txt` after confirming the dedicated wrapper root.

## Result validation

Native AISBench artifacts are authoritative. When using the wrapper, cross-check its CSV against
the latest native artifact in the same measurement window.

Required fields include:

- Total Requests, Success Requests, Failed Requests;
- POST/RECV/FINISH/FAIL or equivalent raw status evidence;
- Benchmark Duration, Request Throughput;
- actual average/max concurrency and achieved request rate;
- Input/Output/Total Token Throughput and Prefill Token Throughput;
- E2EL, TTFT, TPOT/ITL distributions;
- actual InputTokens and OutputTokens distributions.

`total_req` is never a substitute for success count. The wrapper parser's `99999` and `9999`
defaults are failure sentinels, not measurements. Missing fields, sentinels, parse tracebacks, or
wrapper/native disagreement invalidate the run.

If actual concurrency/rate materially misses the target, label the result `client-limited` and
do not treat it as Motor service capacity. Record load-generator CPU, memory, worker mode, and
network path when this gate fails.

## Evidence layout

Copy evidence immediately to:

```text
.motor-workspace-local/benchmark-runs/<namespace>-<timestamp>/
```

Recommended contents:

```text
manifest.json
command.txt
resolved-config/
environment.json
workload.json
dataset-checksums.txt
native-output/
wrapper-output/          # only when selected
prefix-metrics/          # only for prefix runs
summary.json
```

The manifest records backend selection and reason, revisions/hashes, start/end and measurement
window, model/tokenizer, images/packages, hardware/topology/DP, warmup/cache state, runtime
mutations, dataset/generation parameters, client environment, and artifact checksums. Redact
secret values while retaining their reference names.

## Stop conditions

Stop formal load and retain failure evidence on any of these conditions:

- context-length or served-model mismatch;
- readiness/reachability failure;
- unsupported CLI flag/model class/output schema;
- authenticated wrapper request without a non-persisting secret path;
- shared or tracked mutable `WORK_PATH`;
- 100% HTTP 4xx/5xx, `RECV=0`, all requests failed, or repeated identical Bad Request;
- missing explicit success/failure counts;
- empty native result, wrapper sentinel, parse traceback, or stale/mismatched artifact;
- formal command contains `--debug`;
- achieved load is client-limited;
- incomplete or invalid prefix metrics when hit rate is required.
