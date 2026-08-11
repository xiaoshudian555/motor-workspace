---
name: motor-benchmark
description: Run repeatable aisbench online-serving performance validation against a live Motor deployment.
---

# motor-benchmark

Read `references/aisbench.md`. Inspect the native config and live K8s state
directly; there is no benchmark-plan script or deploy-run prerequisite.

Resolve model name, namespace, endpoint, `max_model_len`, topology, and hardware
before asking the user. Ask only for values that cannot be derived. Show the
exact workload and stop criteria before load. Require
`input_len + output_len <= max_model_len`, run a small smoke workload, then the
formal workload with `remote.bash` or a monitored remote job. Do not install or
upgrade aisbench automatically.

Report the exact command, config, raw output, successful/failed request counts,
QPS, throughput, TTFT, TPOT, and E2E metrics. Treat HTTP errors, `RECV=0`, and
repeated invalid requests as failed evidence. Compare only matching hardware,
model, topology, config, and workload.
