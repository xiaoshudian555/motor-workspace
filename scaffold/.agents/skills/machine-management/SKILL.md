---
name: machine-management
description: Register or inspect an existing remote NPU host endpoint. Use for 添加服务器, 登记机器, machine inventory, or checking remote connectivity.
---

# machine-management

Keep only endpoint metadata in
`.motor-workspace-local/machine-inventory.json`. There is no machine lifecycle
service, automatic repair, SSH installer, or `machine-ready` run.

## Minimal record

```json
{
  "schema_version": 1,
  "machines": {
    "dev1": {
      "alias": "dev1",
      "host": "1.2.3.4",
      "port": 22,
      "user": "root",
      "mount_root": "/mnt",
      "remote_workspace_root": "/mnt/motor-workspace",
      "kube_context": "",
      "parity_backend": "shared-hostpath",
      "executor": "ssh",
      "candidate_nodes": []
    }
  }
}
```

For remote-native operation use `executor: native`; obtain values with
`hostname`, `whoami`, and `kubectl config current-context`. Before writing,
read the latest inventory and preserve unrelated machines. Never store a
password, private key, token, or kubeconfig content.

## Verification

Resolve the record to a direct endpoint and use `remote.probe` / `remote.bash`
or the equivalent `.remote-dev` tools to check:

```text
connectivity
mount_root and remote_workspace_root exist or can be created
remote_workspace_root stays below mount_root
fixed workspace is writable (create/read/delete one unique temporary file)
git is available for parity
kubectl context exists when deployment is requested
```

Verification is current-state evidence only. Do not persist a readiness run.
If a check fails, report the exact field or command; do not implement an
automatic `repair` workflow. SSH key bootstrap is an environment prerequisite,
not repository functionality.

Removing a machine means deleting only its inventory entry after explicit
consent. Never delete remote directories or Kubernetes resources here.
