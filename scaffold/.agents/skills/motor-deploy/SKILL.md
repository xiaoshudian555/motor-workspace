---
name: motor-deploy
description: "Motor deployment dispatcher for 拉起一个服务, 启动/部署/重启/停止/查看 Motor, 部署前检查, 检查部署环境, wheel/whl 替换, 构造故障, 故障注入, or 验证故障恢复. Route to repo-local skills and Motor's native deployer."
---

# Motor Deploy Dispatcher

Resolve a workspace containing `scaffold/.agents/skills/` and `sources/motor/`,
read its `AGENTS.md`, classify the request, then load only the relevant atomic
skills.

| Intent | Skill / tool |
|---|---|
| Endpoint metadata or connectivity | `machine-management` |
| Copy local dirty source to fixed `/mnt` paths | `remote-code-parity` |
| Build a Motor wheel | `motor-build-wheel` |
| Read-only K8s/MindCluster checks | `motor-deploy-preflight` |
| Validate native Motor config and generated YAML | `motor-deploy-configure` |
| Deploy, status, restart, stop | `motor-k8s-deploy` |
| Coordinator readiness | `motor-smoke` |
| Inference or feature behavior | `motor-functional` |
| Performance workload | `motor-benchmark` |
| Failure evidence | `motor-diagnosis` |

## Normal flow

```text
resolve endpoint
→ parity only when local source must replace fixed remote source
→ wheel build only when Motor package replacement is requested
→ edit/review native user_config.json + env.json
→ read-only preflight
→ Motor deploy.py --dry-run
→ explicit user consent
→ Motor deploy.py
→ readiness / functional validation
```

There are no `workspace-ready`, `machine-ready`, `deploy-environment-ready`,
`deploy-config-ready`, or `deploy-complete` run gates. Inspect current files,
the current endpoint, and the current cluster every time.

## Boundaries

- Read-only feasibility authorizes probes and dry-run, never parity overwrite,
  config edits, apply, restart, stop, namespace creation, or fault injection.
- Parity overwrite, config mutation, deploy, restart, and stop each require
  explicit consent for the concrete target.
- Use the Motor source tree's native
  `examples/deployer/deploy.py` / `delete.sh`; do not recreate a second deploy
  engine or immutable bundle format in this workspace.
- Never inject source-tree `PYTHONPATH`. Runtime uses image packages or the
  explicitly built Motor wheel through the existing `boot.sh` mechanism.
- Reliability fault injection is not implemented. Stop at that boundary; do
  not relabel functional or diagnosis checks as recovery validation.
- Report commands, endpoint, namespace, observed resources, and artifacts
  directly. Do not manufacture local workflow run IDs.
