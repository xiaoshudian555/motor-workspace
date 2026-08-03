---
name: motor-diagnosis
description: Collect run-scoped Motor deploy diagnostic artifacts from deploy/config/bundle runs.
---

# motor-diagnosis

Collects Pod/Event evidence for a **deploy run** using explicit `config_run_id`,
`bundle_dir`, and `bundle_digest` — not legacy `plan_dir`.

```bash
python3 .agents/skills/motor-diagnosis/scripts/diagnosis_collect.py \
  --machine <alias> \
  --deploy-run-id <id>
```

Progress on stderr; `mws.result.v1` envelope on stdout (`kind=deploy-diagnosis`).
Artifacts land under `.motor-workspace-local/validation-runs/{diagnosis_run_id}/`.

**Requires:** existing deploy run (ready or failed) with `config_run_id` and bundle
references. Fails closed on machine mismatch or bundle digest tampering.

Pod/Event collection runs `kubectl` on the selected remote machine with its
recorded kube context; it never uses the development host's kubeconfig.
