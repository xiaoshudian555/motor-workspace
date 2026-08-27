# Motor smoke suite profiles

This suite runs only the full `pymotor-acceptance` profile. Minimal readiness
alone belongs to `motor-smoke`.

## Profile `pymotor-acceptance`

Maps the legacy MindIE Pymotor smoke suite to atomic Skills. Stages run in order
unless the user excludes them. Each stage keeps its owning Skill's pass criteria;
the suite adds only the suite-owned gates listed below.

### Phase A — read-only and functional (required unless excluded)

| Order | Stage | Owning Skill | Legacy case |
|---|---|---|---|
| A1 | Current K8s workload and Service status | `motor-k8s-deploy` status | common baseline |
| A2 | Coordinator `GET /readiness` | `motor-smoke` | service ready |
| A3 | Non-stream inference | `motor-functional` `inference-request.non-stream` | curl `/v1/completions` |
| A4 | Stream inference | `motor-functional` `inference-request.stream` | streaming survival |

A blocking failure in A2–A4 stops downstream stages whose prerequisites are no
longer valid.

### Phase B — authorized mutation and load (optional unless user makes required)

Each stage below requires its owning Skill's explicit consent in the same run.
Obtain consent per stage; one approval does not cover later stages.

| Order | Stage | Owning Skill | Contract / profile | Legacy case |
|---|---|---|---|---|
| B1 | Online Prefill scale-up 1→2 | `motor-scale` | `prefill-scale-up-1-to-2` | `pymotor_ras_up_down_scale_0001` |
| B2 | Coordinator active/standby failover | `motor-reliability` | `coordinator-failover` | `ras_coordinator_active_passive_0004` |
| B3 | Decode engine process restart | `motor-reliability` | `decode-engine-restart` | `ras_pd_restart_0002` |
| B4 | Prefill NPU link fault isolation and redundant recovery | `motor-reliability` | `prefill-link-isolation` | `ras_p_redundant_0001` |
| B5 | GPQA accuracy | `motor-accuracy` | `gpqa-diamond-0shot-cot-chat` | `pymotor_acc_0008` |
| B6 | GSM8K performance | `motor-benchmark` | user-confirmed formal workload | `pymotor_performance_0009` |

Default behavior:

- Run Phase A automatically.
- Present Phase B as an optional continuation with per-stage consent. Do not
  inject faults, scale, or start AISBench load without approval.
- When the user says “全量 / 完整 / 全部 / full acceptance including RAS and
  benchmark”, still collect consent stage-by-stage; show the full planned order
  up front.

### Suite-owned gates

| Stage | Suite assertion |
|---|---|
| B5 GPQA accuracy | `abs(accuracy - 0.807) <= 0.01` **or** `accuracy > 0.807`; hard FAIL when `accuracy < 0.797` |
| B6 performance | baseline must be supplied or confirmed by the user; never invented |

`motor-accuracy` and `motor-benchmark` produce raw metrics only. The suite owns
the final pass/fail for those stages.

### Phase B ordering notes

- Run B1 before B2–B4 when all are selected; scaling changes topology that later
  RAS stages must re-baseline.
- B2–B4 are independent fault experiments; run only the scenarios the user
  selects. Never run multiple injections in one transaction.
- B5 and B6 are long-running AISBench stages. Require confirmed evaluator paths,
  dataset availability, and workload parameters before consent.
- B6 follows `motor-benchmark` formal rules: no `--debug`, no invented baseline.
  Legacy `7400 * 0.97` throughput gate is a reference hint only until the user
  confirms a comparable formal workload.

## Overall result rules

- Overall `PASS` requires every **selected required** stage to `PASS`.
- Optional stages excluded by scope are `NOT RUN`, not `PASS`.
- Unsupported or unimplemented stage requests are `CAPABILITY GAP`.
- Never report overall `PASS` when a required stage is `BLOCKED`, `FAIL`, or
  `CAPABILITY GAP`.

## Evidence

Each stage inherits its owning Skill's artifact directory convention:

| Owner | Default artifact root |
|---|---|
| `motor-scale` | `.motor-workspace-local/scale/...` |
| `motor-reliability` | `.motor-workspace-local/reliability/...` |
| `motor-accuracy` | `.motor-workspace-local/accuracy-runs/...` |
| `motor-benchmark` | `.motor-workspace-local/benchmark-runs/...` |
| failure routing | `.motor-workspace-local/diagnosis/...` |

The suite itself does not introduce a separate workflow gate or run ID.
