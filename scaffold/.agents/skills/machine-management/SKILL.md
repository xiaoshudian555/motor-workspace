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
python3 .agents/skills/machine-management/scripts/machine_self_identify.py --dry-run
python3 .agents/skills/machine-management/scripts/machine_self_identify.py
python3 .agents/skills/machine-management/scripts/machine_ssh_setup.py --host 1.2.3.4 --password-stdin
python3 .agents/skills/machine-management/scripts/machine_verify.py --alias dev1
python3 .agents/skills/machine-management/scripts/machine_repair.py --alias dev1 --mount-root /mnt
python3 .agents/skills/machine-management/scripts/machine_remove.py --alias dev1
```

## Remote-native self-registration

When the Agent runs **directly on the target NPU host** (remote-native
topology), there is no SSH metadata to record: the current host is the
machine. Use `machine_self_identify.py` instead of `machine_add.py`:

- Probes hostname, current user, shared mount root, fixed workspace root,
  and the active `kubectl` context.
- Registers (or reuses) an `executor=native` machine record so downstream
  workflow steps resolve the machine exactly like an SSH machine, but drive
  it through `NativeTransport`.
- `--dry-run` prints the probed record without writing inventory.
- Explicit overrides: `--alias`, `--mount-root`, `--remote-workspace-root`,
  `--kube-context`, `--user`.
- If the same host already has a native record, the existing alias is reused
  so parity / machine-ready evidence keeps a stable identity.

Registration only supplies connection defaults. Run `machine_verify.py
--alias <alias>` to prove the machine is actually ready (writable mount root,
parity tools, shared hostPath) before downstream steps.

## SSH bootstrap (one-time)

Downstream transport uses `BatchMode=yes` and **requires key-based login**. Password
auth is only for the one-time bootstrap step below.

Preferred order:

1. If key login already works, `machine_ssh_setup.py` exits immediately as ok.
2. Otherwise pass the login password once via **stdin** or an env var — never
   write it into inventory, profiles, or tracked files.

```bash
# before machine_add, or after add using inventory alias
printf '%s' "$MWS_SSH_PASSWORD" | \
  python3 .agents/skills/machine-management/scripts/machine_ssh_setup.py \
    --host 1.2.3.4 --password-stdin

python3 .agents/skills/machine-management/scripts/machine_ssh_setup.py \
  --alias dev1 --password-env MWS_SSH_PASSWORD
```

Bootstrap backends (automatic):

- `sshpass` when available on PATH
- otherwise `paramiko` (`python3 -m pip install paramiko`)

The script appends the local public key to remote `~/.ssh/authorized_keys` if
missing, fixes permissions, then verifies `ssh -o BatchMode=yes ... echo ok`.

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
(3+3 part-2 step 2) — see
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
