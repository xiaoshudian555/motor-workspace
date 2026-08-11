# Motor performance evidence semantics

Use these meanings for the current repository revision. Re-check the source for
logs produced by another revision.

## Coordinator timing stages

| Evidence | Meaning | Attribution limit |
|---|---|---|
| `stage=select_and_allocate` | Scheduler selection and allocation call for one role and attempt | Motor scheduling path; retain role and retry number |
| `Scheduling role=... total_ms=` | Total `prepare_resource` duration, including retries and local request bookkeeping | Composite Motor resource preparation; do not add it to `select_and_allocate` |
| `stage=late_select_d` | Lazy Decode resource preparation during P-to-D handoff | Motor scheduling path; currently emitted for every late selection rather than the sampled path |
| `stage=get_http_client` | HTTP client-pool acquisition | Motor-side plumbing, available at DEBUG level |
| `stage=forward_to_engine_connect` | Time until the upstream streaming response is opened | Mixed Motor client, network, and downstream acceptance time |
| `stage=forward_to_engine_first_chunk` | Time from forwarding until the first non-empty upstream byte chunk | Downstream composite: network, queueing, P/D work, transfer, and response delivery; never label it Motor overhead |
| `stage=forward_to_engine` | Entire non-streaming upstream request | Downstream composite; never label it Motor overhead |

`select_and_allocate`, resource `total_ms`, connect, and first-chunk timings can
overlap or contain one another. Do not sum percentile values. Correlate by a
request trace or explicit request identifier before building a per-request
critical path.

The sampled scheduling logs use `hash(req_id) % 100 == 0`, approximately 1% at
high QPS. Always report the observed sample count and measurement window. Do not
present sampled percentiles as complete request coverage. Python hash seeding and
process restarts can also change which request identifiers are selected.

## Tracer fields

The current stream router increments `count_token` once per non-first byte chunk
returned by `httpx`, not once per decoded token. Consequently:

- `count_token` is a transport-chunk count, not output token length.
- Coordinator `TTOT` is elapsed time after the first chunk divided by that chunk
  count. It is not total output time and is not a trustworthy token-level TPOT.
- Prefer benchmark TPOT/ITL or vLLM's token-aware histogram for token latency.
- Never bucket requests by output length using Coordinator `count_token`.

## Metrics

- `motor:prompt_tokens_per_second` and
  `motor:generation_tokens_per_second` are rates derived from vLLM token counter
  deltas. They describe engine service throughput at the reported aggregation
  scope, not kernel speed and not Motor's own compute throughput.
- `motor:active_prefill_workers`, `motor:active_decode_workers`, and their
  inactive counterparts describe service topology and availability.
- `vllm:time_to_first_token_seconds`,
  `vllm:time_per_output_token_seconds`, and
  `vllm:e2e_request_latency_seconds` are histogram-derived latency evidence.
- `vllm:request_queue_time_seconds`, `vllm:request_prefill_time_seconds`, and
  `vllm:request_decode_time_seconds` help separate engine phases only when their
  labels and aggregation scope match the target workload.
- Running, waiting, KV-cache, and deferred gauges are state signals. Interpret
  their combination and trend; no single gauge proves a root cause. In
  particular, `Deferred > 0` is not synonymous with KV cache exhaustion.

For every aggregate, record labels, aggregation scope, scrape interval, missing
series, and counter resets. Do not multiply an already aggregated value by the
number of workers.
