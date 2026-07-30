---
name: machine-management
description: Add, verify, repair, or remove remote NPU machines and kube context references. Use for "添加服务器", "登记机器", machine inventory.
---

# machine-management

Maintains `.motor-workspace-local/machine-inventory.json`.

## Entry points

```bash
python3 .agents/skills/machine-management/scripts/inventory.py list
python3 .agents/skills/machine-management/scripts/machine_add.py --alias dev1 --host 1.2.3.4 --mount-root /mnt
python3 .agents/skills/machine-management/scripts/machine_verify.py --alias dev1
```

Current implementation note: `machine_verify.py` still mixes in
MindCluster/K8s checks. This is legacy behavior, not the target contract.
The target keeps machine verification limited to remote development/parity
readiness and moves cluster checks to the three-step Motor workflow described
in `scaffold/docs/motor-deploy.md`.
