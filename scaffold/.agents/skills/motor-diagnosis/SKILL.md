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
Do not restart, delete, repair, or inject faults while collecting evidence.
Save artifacts only to a user-approved path or untracked
`.motor-workspace-local/`, with source command and timestamp. When logs match
precision auto-recovery terminate markers, recommend the corresponding
specialized diagnosis skill.
