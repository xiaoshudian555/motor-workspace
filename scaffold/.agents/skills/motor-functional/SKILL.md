---
name: motor-functional
description: Compile and run catalog-backed Motor functional validation from a user's natural-language goal. Use for real non-stream/stream inference requests and deployed feature behavior such as metrics, tracing, TLS, parameter passthrough, overload control, or later API-key checks.
---

# motor-functional

Turn the user's description into a resolved `mws.functional.spec.v1`; do not ask
the user to write JSON/YAML. Read `references/case-catalog.json` when selecting
features or cases. Read `references/coordinator-endpoints.md` before manual curl,
endpoint discovery changes, or infer/mgmt port troubleshooting.

## Workflow

1. Extract the validation goal from the user's wording.
2. Select catalog feature IDs and the smallest case set that proves that goal.
   Use feature defaults when the user asks for the whole feature.
3. Ask only when ambiguity changes deployment mutation, risk, cost, or the pass
   criterion. Otherwise choose safe catalog defaults.
4. Compile the spec, or execute currently supported cases:

```bash
python3 .agents/skills/motor-functional/scripts/compile_spec.py \
  --machine <alias> \
  --deploy-run-id <id> \
  --request '<user wording>' \
  --feature api-key
```

```bash
python3 .agents/skills/motor-functional/scripts/functional_run.py \
  --machine <alias> \
  --deploy-run-id <id> \
  --request '验证真实推理请求' \
  --feature inference-request
```

Metrics and tracing use the same entrypoint. Metrics discovers the Coordinator
observability Service on port `1027`; tracing queries the repository's existing
Tempo stack on port `3200` at the configured OTLP export host by default. The
agent may pass `--tempo-host` when the Tempo query host differs from the OTLP
collector host:

```bash
python3 .agents/skills/motor-functional/scripts/functional_run.py \
  --machine <alias> \
  --deploy-run-id <id> \
  --request '验证 metrics 和 tracing' \
  --feature metrics \
  --feature tracing
```

Use repeated `--case` arguments to narrow the feature defaults. Use `--output`
to save an immutable resolved spec for a future functional run.

5. Present the resolved features, cases, evidence, and pass policy before any
   operation that would mutate a deployment or generate material load.
6. Dispatch each case by its catalog `adapter`. Record outcomes using existing
   `mws.result.v1` check statuses only: `ok`, `warning`, `error`, or
   `unavailable`.

## Coordinator access (standard)

Functional **must not** hand-craft `ClusterIP:NodePort` URLs. The standard path is:

1. `discover_coordinator_services(..., roles=("infer",))` — infer port **1025**
2. `kubectl port-forward` to the infer Service (see `functional_run.py`)
3. `resolve_model_name(user_config)` from the deploy config bundle — not top-level
   `served_model_name`

Inference cases POST **`/v1/completions`** with a `prompt` field. Do not use the
mgmt Service (1026) or its ClusterIP for inference. Pitfalls and manual curl
examples: `references/coordinator-endpoints.md`.

## Current boundary

- Real non-stream/stream inference, Coordinator Prometheus metrics, and
  Tempo-backed tracing correlation cases are implemented here after Coordinator
  readiness. API-key feature validation is explicitly deferred.
- Functional metrics checks prove endpoint/series behavior under a single
  controlled request. Resource monitoring and performance attribution under
  sustained load belong to Profiling, not Functional.
- Tracing injects a sampled W3C `traceparent` and requires both enabled Motor
  tracing and a queryable Tempo backend. Disabled tracing, zero remote-parent
  sampling, or a missing Tempo backend is recorded as `unavailable`.
- TLS, parameter-passthrough, overload, and API-key adapters remain unavailable
  until their concrete handlers are added.
- Never report an unimplemented adapter as passed; dispatch records it as
  `unavailable`.
- Do not put plaintext keys, tokens, or private keys in the spec. Refer to an
  environment variable or local secret path.
- Do not modify Motor deployment configuration automatically. Configuration
  changes remain in the deploy workflow and require its normal consent.
- Do not use Functional to claim protocol compliance, topology correctness,
  model accuracy, performance, capacity, stability, or reliability.

The runtime dispatcher lives in `mws_functional.dispatch_validation_spec` and
accepts an explicit `adapter -> handler` dictionary. Add catalog entries and
handlers together as real capabilities are implemented; do not add a plugin
registry or another lifecycle/status model.
