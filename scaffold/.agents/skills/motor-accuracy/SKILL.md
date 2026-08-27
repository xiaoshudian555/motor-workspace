---
name: motor-accuracy
description: Run repeatable AISBench accuracy evaluation against a live Motor deployment and extract the raw accuracy metric. Use for GPQA, 精度测试, accuracy, correctness, pymotor_acc_0008, or model-answer quality validation. Baseline assertion and suite-level pass/fail belong to motor-smoke-suite.
---

# Motor accuracy

Generate controlled AISBench accuracy evidence against a live Motor deployment.
This Skill executes the evaluation and extracts the raw metric; it does not apply
the suite baseline, attribute performance bottlenecks, or prove RAS behavior.

Read `references/gpqa-profile.md` before constructing commands. Also read
`../motor-benchmark/references/aisbench.md` for shared AISBench runtime,
version, and evidence rules.

## Supported profiles

| Profile | Contract |
|---|---|
| GPQA diamond 0-shot COT chat (legacy pymotor_acc_0008) | `gpqa-diamond-0shot-cot-chat` |

Other datasets and scoring modes are outside this Skill until a contract is
added. Route throughput/latency work to `motor-benchmark`; route inference
smoke to `motor-functional`; route suite-level pass/fail to `motor-smoke-suite`.

## Resolve facts before asking

Derive from native `user_config.json`, machine inventory, and live K8s:

- served model name; all active engine sections must agree;
- namespace / `job_id` and a reachable Coordinator inference endpoint;
- `max_model_len`; tokenizer/model path used by the evaluator;
- P/D topology, hardware, and software revisions.

Do not guess AISBench container, installation root, dataset path, or runtime
root. Ask when they cannot be derived.

The profile, generation parameters, and dataset revision must be supplied or
confirmed. Do not invent a workload.

## Preflight and capability gate

1. Map the request to the selected profile using `gpqa-profile.md`.
2. Require `input_len + max_out_len <= max_model_len` for the profile's longest
   prompt shape when known.
3. Require Coordinator `/readiness` HTTP 200 with `ready=true`, inference
   reachability from the evaluator environment, and exact served-model-name
   agreement.
4. Probe, but never install or upgrade, the existing AISBench environment.
   Record Python and AISBench versions, installation/source revision, supported
   accuracy output schema, and dataset availability.
5. Use a run-scoped, user-approved mutable runtime directory. Never edit tracked
   AISBench source or share one mutable workspace between concurrent runs.
6. API keys must remain reference-based. Fail closed when authentication would
   persist secrets into generated Python configs.

## Execute

Before load, show the resolved profile, smoke command, formal command, output
paths, runtime mutations, and stop conditions.

- Run a smoke accuracy workload smaller than the formal profile when the
  installed AISBench supports it; do not invent a fixed smoke shape unrelated to
  the profile.
- Formal native AISBench accuracy runs must not use `--debug`; it can make the
  evaluator client-limited.
- Run long evaluations as one monitored remote job. Do not time out a
  synchronous command and start a duplicate.
- Preserve complete native AISBench timestamped output, especially dumped configs,
  logs, per-sample results, and aggregate metrics JSON.

## Result extraction gate

A formal run is valid only when all of the following hold:

- explicit dataset identity and revision/checksum were recorded;
- aggregate accuracy was extracted from the authoritative native artifact, not
  from a stale or partial grep;
- generation parameters in the run-scoped config match the confirmed profile;
- the evaluation completed without empty results, all-failed traffic, or mixed
  artifacts from an earlier run.

## Evidence and report

Archive immediately under:

```text
.motor-workspace-local/accuracy-runs/<namespace>-<profile>-<timestamp>/
```

Preserve a manifest, exact command, redacted resolved configs, environment/version
fingerprint, dataset checksums, complete native AISBench output, and extracted
aggregate accuracy.

Report profile name, aggregate accuracy, raw output paths, evaluator/version
fingerprint, and comparability metadata. Do **not** apply the legacy 0.807
threshold here; pass/fail assertion is owned by `motor-smoke-suite` or another
explicit caller. Do not route accuracy execution failure to
`motor-performance-analysis`.
