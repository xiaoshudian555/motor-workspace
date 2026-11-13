---
name: motor-diagnosis
description: Collect live Motor deployment evidence with direct kubectl and remote artifact tools.
---

# motor-diagnosis

Use the selected endpoint, kube context, namespace, and current cluster state.
Do not require a deploy run, config bundle, digest, or workspace diagnosis
script.

Collect with `remote.bash` and remote artifact tools:

```bash
kubectl --context "$CTX" get all -n "$NS" -o wide
kubectl --context "$CTX" get events -n "$NS" --sort-by=.lastTimestamp
kubectl --context "$CTX" describe pod -n "$NS" <pod>
kubectl --context "$CTX" logs -n "$NS" <pod> --all-containers --timestamps
kubectl --context "$CTX" logs -n "$NS" <pod> --all-containers --previous --timestamps
```

Also inspect the native deployer's `--auto_log_collect` output when present.
For a deploy or startup failure, return the evidence to
`motor-startup-diagnosis` for cross-domain attribution; this Skill remains the
common collector and does not force an environment, deployer, config, or code
category.
Do not restart, delete, repair, or inject faults while collecting evidence.
Before the first `kubectl` or remote collection command, query experience cards
from this workspace:

```bash
python3 scaffold/tools/diagnosis_experience.py --symptom "<redacted symptom>"
```

The entry records `entry=motor-diagnosis`. Use at most two matched cards. A
`HIT` is only a clue: perform the `use_check` printed with it. After that
check, run the same query again with `--check <card id>=pass|fail` and
`--observed "<card id>=<short fact from this run>"` for that card only. Do not
submit `--check` without that observation. When the session id is already
known, pass `--session <id>`. The script only reads `CURSOR_CONVERSATION_ID`
when that variable is already set; do not guess another platform's variable
name. The flag does not apply any other
hit. `CHECK_FAILED` means do not use that card this time. Revise the card only
when the observation overturns its conclusion, not merely because this scene
does not match.
A source file that still exists is not itself a passed check. For a Conductor
KV miss, query this entry before treating a dropped `block_size=4096` event as
the cause.
`no_match` is a normal result; keep the `experience_query` line in the
diagnosis note. Do not open `failure_corpus.tsv` for this lookup.
Save artifacts only to a user-approved path or untracked
`.motor-workspace-local/`, with source command and timestamp. When logs match
precision auto-recovery terminate markers, recommend the corresponding
specialized diagnosis skill.

## Result categories

Pick one primary category and a confidence level:

| Category | Meaning |
|---|---|
| environment | cluster API, scheduler, operator, node, NPU resource, image, or mount |
| deployment-config | native Motor config or generated workload mismatch |
| workload-startup | Pod scheduling, image pull, container startup, probe, or crash |
| coordinator-control | Coordinator/Controller registration, topology, or convergence |
| inference-path | Service/endpoint/protocol/request routing or engine request failure |
| benchmark-client | load generator, dataset, config, rate/concurrency, or invalid result |
| accuracy-client | evaluator, dataset, generation parameters, or invalid accuracy artifact |
| scale-control | native `--update_instance_num`, topology convergence, or post-scale inference |
| performance | valid workload misses baseline; requires performance attribution |
| specialized-recovery | a supported recovery-specific diagnosis route matched |
| unknown | evidence is insufficient or contradictory |

The report must distinguish:

- **symptom**: the failed assertion or user-visible break;
- **proximate cause**: the closest evidenced failure;
- **root cause**: only when the evidence chain proves it;
- **repair**: never performed automatically by collection or by
  `motor-smoke-suite` failure routing.
