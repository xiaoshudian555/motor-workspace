---
name: machine-management
description: Add, verify, repair, or remove remote NPU machines and kube context references. Use for "添加服务器", "登记机器", machine inventory.
---

# machine-management

Maintains `.motor-workspace-local/machine-inventory.json` for **existing SSH
machines** with a shared mount root and fixed remote workspace directories.

This skill prepares the remote development / parity substrate only. It does
**not** create Docker containers, modify Kubernetes, or prove MindCluster /
deploy readiness.

## Entry points

```bash
python3 .agents/skills/machine-management/scripts/inventory.py list
python3 .agents/skills/machine-management/scripts/inventory.py get dev1
python3 .agents/skills/machine-management/scripts/machine_add.py --alias dev1 --host 1.2.3.4 --mount-root /mnt
python3 .agents/skills/machine-management/scripts/machine_verify.py --alias dev1
python3 .agents/skills/machine-management/scripts/machine_repair.py --alias dev1 --mount-root /mnt
python3 .agents/skills/machine-management/scripts/machine_remove.py --alias dev1
```

## machine-ready boundary

`machine_verify.py` checks:

- SSH connectivity
- `mount_root` writable with cleanup
- `remote_workspace_root` stays under `mount_root`
- remote workspace writable with cleanup
- parity tools available remotely (`tar`, `mkdir`)
- shared hostPath root visible on login host
- optional kube context metadata consistency with deploy profile

It does **not** check namespace RBAC, CRDs, Volcano, Pod readiness, or other
Kubernetes / MindCluster deployment facts. Those belong to **`motor-deploy-preflight`**
(3+3 part-2 step 1) — see
`.agents/skills/motor-deploy-preflight/SKILL.md`.

`last_verified_at` in inventory is diagnostic metadata only; downstream steps
must consume explicit machine-ready run evidence, not this timestamp alone.

## Safety boundaries

- Inventory writes use file locks and atomic JSON replacement.
- `repair` only updates inventory fields when explicitly passed on the CLI.
- `remove` only drops the local inventory record; it does not delete remote
  directories or Kubernetes resources.
- Destructive remote operations require separate user consent outside this
  skill.
