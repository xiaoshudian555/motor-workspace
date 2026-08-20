---
name: motor-diagnosis-runtime-code
description: Diagnose Motor startup failures after component processes begin running, including crashes, hangs, registration failures, and Motor/vLLM/vLLM-Ascend/Ascend runtime integration errors. Use only after common evidence reaches the runtime process boundary.
---

# Motor runtime-code diagnosis

Start from the first component process that failed, not from the last dependent
component reporting unready. Correlate current and previous container logs,
restart/exit state, probes, and Controller/Coordinator/Engine registration by
timestamp and instance identity. Build the shortest causal chain across
components.

When available, record read-only runtime provenance: image or digest, installed
package version, module `__file__`, wheel/source replacement facts, and the
relevant Motor, vLLM, and vLLM-Ascend revisions. Search the matching checked-out
source to map a traceback or explicit error path. Lack of a traceback does not
prove a hang or code defect; identify the last confirmed progress point and the
missing expected event.

Return a suspected owner from `motor`, `vllm`, `vllm-ascend`,
`ascend-runtime`, or `unknown-runtime`. Claim a code root cause only when valid
effective configuration and required external prerequisites reached a failing
runtime code path with direct evidence. Route bad effective values to
`motor-diagnosis-config`, generated workload errors to
`motor-diagnosis-deployer`, and unavailable runtime prerequisites to
`motor-diagnosis-environment`.

Report the first failing component, timeline, exception or stalled stage,
suspected owner, source location when proven, confidence, and missing evidence.
Do not patch code, replace a wheel, restart a Pod, or redeploy during diagnosis.
