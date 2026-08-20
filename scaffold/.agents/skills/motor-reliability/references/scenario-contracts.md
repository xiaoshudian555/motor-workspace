# Motor reliability scenario contracts

Read only the selected scenario section plus the shared assertion rules.

## Shared assertion rules

- Derive deadlines from relevant live settings—ETCD lease/check intervals,
  Kubernetes probe timing, health/fault thresholds, and engine restart policy—or
  ask the user to confirm a deadline. Do not replace convergence polling with a
  fixed 30-second sleep.
- Record the Coordinator log transition
  `Refresh instances done: E=…, P=…, D=…, U=…` when relevant, but do not use it
  as sole proof. Correlate it with Pod/process/endpoint state and inference.
- A scenario passes only when its pre-fault baseline, intended fault effect,
  recovery transition, final functional state, and cleanup/restoration all have
  evidence.
- If an expected transient state is too brief to observe, require another
  trustworthy transition signal. Do not reinject merely to obtain prettier
  evidence.

## `coordinator-failover`

### Preconditions

- Native config has
  `motor_coordinator_config.standby_config.enable_master_standby=true`.
- ETCD is reachable and the configured lease/check settings are known.
- Exactly one Coordinator is the active Service endpoint and at least one
  distinct Coordinator is demonstrably standby. Pod `Ready` alone is not role
  proof; use endpoint membership, per-Pod management readiness, and role logs.
- Baseline Coordinator readiness and controlled inference pass.

### Injection plan

Identify the active Coordinator Pod/container and the unique
`motor.coordinator.main` daemon PID. Record full identity, then recheck it
immediately before sending `SIGKILL`. Do not kill a standby, the management
child alone, every replica, or a PID selected from an ambiguous process list.

### Required evidence

1. Before: active and standby Pod UIDs, roles, Service endpoint membership,
   readiness, restart counts, daemon PID/command, and inference.
2. During: injection timestamp; old active endpoint removal or readiness loss;
   ETCD/role evidence showing the former standby became master; Service endpoint
   transition.
3. After: exactly one active endpoint; Coordinator readiness `ready=true`;
   controlled inference succeeds; the failed replica is recreated/restarted or
   returns as standby according to the workload policy.
4. Availability claim: only when a low-impact continuous probe spans the
   transition; report request count, failures, longest outage, and the
   predeclared error budget. Without that probe, report recovery but not
   “service uninterrupted.”

Cleanup stops the probe/port-forward and verifies a stable one-active plus
standby topology. No manual restart is intrinsic to this scenario.

Primary source semantics:
`sources/motor/docs/zh/design/fault_tolerance/standby.md`.

## `decode-engine-restart`

### Preconditions

- Resolve one intended Decode instance, Pod, container, NodeManager, and unique
  engine process from live state. Record instance/job identifiers as well as
  PID identity.
- Baseline topology contains the expected D count; readiness and controlled
  inference pass.
- Confirm the live NodeManager/engine policy is expected to restart the killed
  engine. If the installed revision or engine backend does not support it,
  report BLOCKED rather than deleting the Pod as a substitute.

### Injection plan

Send `SIGKILL` once to the uniquely identified Decode engine-server process.
The executable name/case differs by revision and backend; discover it from the
full process tree and owning NodeManager rather than assuming a `grep
EngineServer` match. Recheck PID identity immediately before the signal.

### Required evidence

1. Before: Decode instance/job ID, Pod UID, container restart count,
   NodeManager and engine PID/command, D topology, readiness, and inference.
2. During: old PID exit; engine/NodeManager restart records; D removal,
   unavailability, or another trustworthy state transition if observable.
3. After: a new engine PID/start identity; the intended Decode instance is
   registered and available; expected D count restored; readiness and
   controlled inference pass.

A Pod that stayed Running or a final `D=1` line does not alone prove process
restart. Cleanup stops observers and verifies no injected child/probe remains.
No Pod deletion or rollout restart is intrinsic restoration.

Relevant source overview:
`sources/motor/docs/zh/developer_guide/components/node_manager.md` and
`sources/motor/docs/zh/design/fault_tolerance/overview.md`.

## `prefill-link-isolation`

### Preconditions

- Resolve the intended Prefill instance to exact Pod UID, node, physical NPU,
  device index/BDF, and parameter-plane link. Prove the device/link is not
  shared with unrelated workloads.
- Read the installed `hccn_tool` help and query the current link state using the
  installed version's syntax. Save the complete pre-state and verify an exact,
  operator-accepted restoration command before injection. If the pre-state
  cannot be read or restoration cannot be proven, report BLOCKED.
- Confirm the test topology and redundancy policy can replace the isolated P.
  If starting from 1P1D, explicitly declare whether the expected transient is
  `P=0` and inference failure before a replacement returns.
- Verify required health/fault detection configuration from the live native
  config. For vLLM virtual inference, follow the installed revision's
  `health_check_config.enable_virtual_inference` constraints; do not mutate it
  inside this Skill.
- Baseline expected P count, readiness, physical link state, and inference pass.

### Injection and restoration transaction

The plan must show the exact installed-version command that changes only the
selected link from its recorded state to the fault state, and the exact command
that restores the recorded original state. Obtain consent for this pair as one
transaction. Never use a guessed `hccn_tool ... up` command.

### Required evidence

1. Before: complete target mapping, link state, P topology, redundancy target,
   health configuration, readiness, and inference.
2. During: fault command result; read-back proving the link changed; Controller,
   FaultManager, Coordinator, and instance evidence showing the intended P was
   detected and isolated; expected P/topology and request behavior.
3. Recovery: redundant P appears on the expected distinct resources, registers
   as available, expected P count returns, readiness is true, and inference
   succeeds.
4. Restoration: original physical link state is restored and read back; no
   unresolved node/card fault or forced isolation remains. If restoration or
   verification fails, result is `RESTORATION FAILED` even if service recovered
   through redundancy.

Do not interpret virtual-inference health success as proof that the physical
link was restored. Relevant source semantics:
`sources/motor/docs/zh/design/fault_tolerance/overview.md` and
`sources/motor/docs/zh/user_guide/features/sim_inference.md`.

