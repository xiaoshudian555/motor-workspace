---
name: motor-diagnosis
description: Collect run-scoped Motor deploy diagnostic artifacts from deploy/config/bundle runs.
---

# motor-diagnosis

Collects Pod/Event evidence and the recorded upstream `--auto_log_collect`
session for a **deploy run** using explicit `config_run_id`, `bundle_dir`, and
`bundle_digest` — not legacy `plan_dir`.

```bash
python3 .agents/skills/motor-diagnosis/scripts/diagnosis_collect.py \
  --machine <alias> \
  --deploy-run-id <id>
```

Progress on stderr; `mws.result.v1` envelope on stdout (`kind=deploy-diagnosis`).
Artifacts land under `.motor-workspace-local/validation-runs/{diagnosis_run_id}/`.
Pod logs are copied beneath `logs/<auto_log_collect_session>/`; the manifest
records their remote source, digest, and any PyMotor diagnosis route match.

**Requires:** existing deploy run (ready or failed) with `config_run_id` and bundle
references. Fails closed on machine mismatch or bundle digest tampering.

Pod/Event collection runs `kubectl` on the selected remote machine with its
recorded kube context; it never uses the development host's kubeconfig.

## PyMotor routing

When collected logs match precision auto-recovery markers, the result recommends
`motor-diagnosis-controller-recovery-terminate`. cmotor diagnosis skills are not
part of this workspace package.
