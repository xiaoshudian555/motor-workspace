---
name: motor-diagnosis-config
description: Diagnose Motor startup failures caused by invalid, inconsistent, or ineffective native user_config.json and env.json values. Use when config, generated YAML, ConfigMap, or Pod effective state may differ or violate Motor requirements.
---

# Motor configuration diagnosis

Treat native `user_config.json` and `env.json` as the desired deployment input.
Trace only the configuration relevant to the failure through:

```text
native config -> generated manifest -> live workload/ConfigMap -> Pod effective state
```

Compare model and mount paths, deployment mode, image, namespace/job ID,
P/D topology and NPU counts, selectors, ports, engine type, served model name,
parallelism, KV settings, component addresses, and enabled feature dependencies
when they are implicated. Use current Motor docs and config source definitions
to distinguish an invalid field from an optional field using its default.

Redact secrets and credentials in all output. Do not treat any difference as a
failure by itself: prove that the invalid, inconsistent, missing, stale, or
ineffective value caused the observed deploy/startup error. If the desired
config is valid but generated YAML loses or changes it, route to
`motor-diagnosis-deployer`. If the valid effective state cannot be satisfied by
the cluster, route to `motor-diagnosis-environment`. If valid effective config
reaches a runtime exception, route to `motor-diagnosis-runtime-code`.

Report the exact dotted config path, redacted desired/generated/effective
values, causal evidence, confidence, and the smallest safe correction proposal.
Do not edit the native config, ConfigMap, manifest, or live workload.
