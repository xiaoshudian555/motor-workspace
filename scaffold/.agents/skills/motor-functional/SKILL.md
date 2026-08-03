---
name: motor-functional
description: Compile a user's natural-language Motor functional validation goal into catalog-backed cases and a deterministic validation spec. Use for deployed Motor feature checks such as API key, TLS, metrics, tracing, parameter passthrough, or overload-control response behavior.
---

# motor-functional

Turn the user's description into a resolved `mws.functional.spec.v1`; do not ask
the user to write JSON/YAML. Read `references/case-catalog.json` when selecting
features or cases.

## Workflow

1. Extract the validation goal from the user's wording.
2. Select catalog feature IDs and the smallest case set that proves that goal.
   Use feature defaults when the user asks for the whole feature.
3. Ask only when ambiguity changes deployment mutation, risk, cost, or the pass
   criterion. Otherwise choose safe catalog defaults.
4. Compile the spec:

```bash
python3 .agents/skills/motor-functional/scripts/compile_spec.py \
  --machine <alias> \
  --deploy-run-id <id> \
  --request '<user wording>' \
  --feature api-key
```

Use repeated `--case` arguments to narrow the feature defaults. Use `--output`
to save an immutable resolved spec for a future functional run.

5. Present the resolved features, cases, evidence, and pass policy before any
   operation that would mutate a deployment or generate material load.
6. Dispatch each case by its catalog `adapter`. Record outcomes using existing
   `mws.result.v1` check statuses only: `ok`, `warning`, `error`, or
   `unavailable`.

## Current boundary

- This first version compiles and dispatches specs; real HTTP, TLS, metrics,
  tracing, and load adapters are not implemented yet.
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
