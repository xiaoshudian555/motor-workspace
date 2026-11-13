---
name: motor-deploy
description: "Use only when the request explicitly invokes $motor-deploy, or explicitly asks to use Motor's native deployer for a named service/model in a concrete target environment and perform a live lifecycle action. Covers Motor preflight, deploy, status, restart, stop, scale, fault injection, and recovery validation. Do not trigger for generic start/deploy wording, Docker-only or RBG workflows, local scripts/guides/config examples, or code-only work."
---

# Motor Deploy Dispatcher

Resolve a workspace containing `scaffold/.agents/skills/` and `sources/motor/`,
read its `AGENTS.md`, classify the request, then load only the relevant atomic
skills.

## Entry gate

Enter this dispatcher only when the request explicitly invokes
`$motor-deploy`, or when **all three** anchors are present:

1. **Motor-native anchor:** the user explicitly names Motor's native deployer,
   `deploy.py`, or an existing Motor-native deployment. Mentioning Motor source
   code, a Coordinator process, or a deployment-shaped artifact is not enough.
2. **Concrete target and service:** the request identifies where the action
   applies (for example an endpoint, cluster, namespace, or host set) and which
   service/model is affected.
3. **Live lifecycle action:** the user asks to preflight, deploy, inspect live
   status, restart, stop, scale, inject a fault into, or validate recovery of
   that deployment.

If any anchor is missing, do not load atomic deployment skills. Continue as a
normal repository, Docker, RBG, documentation, or artifact-authoring task; ask
for the missing target only when the user's intent is clearly a live Motor
deployment but underspecified.

| Request | Route here? | Reason |
|---|---:|---|
| `在 120 集群用 Motor 部署 DeepSeek-V4 1P1D 服务` | Yes | Motor-native + concrete target/service + live deploy action |
| `检查 120 集群 namespace X 上的 Motor DSv4 部署环境` | Yes | Concrete Motor preflight target |
| `重启 endpoint X 上的 Motor coordinator deployment` | Yes | Concrete existing Motor deployment |
| `拉起一个服务` / `部署服务` | No | Generic deployment wording lacks all anchors |
| `起 Docker-only 1P1D` | No | Docker-only is not Motor-native deployment |
| `起 RBG` / `改 RBG 配置` | No | RBG has its own workflow |
| `整理 Coordinator 一键脚本和适配指南` | No | Local artifact authoring, not a live action |
| `找一个部署示例配置` / `看 Motor 部署代码` | No | Example discovery or code-only work |

| Intent | Skill / tool |
|---|---|
| Endpoint metadata or connectivity | `machine-management` |
| Copy local dirty source to fixed `/mnt` paths | `remote-code-parity` |
| Build a Motor wheel | `motor-build-wheel` |
| Edit native deployment intent, including P/D placement | `motor-config-edit` |
| Read-only K8s/MindCluster checks | `motor-deploy-preflight` |
| Validate native Motor config and generated YAML | `motor-deploy-configure` |
| Deploy, status, restart, stop | `motor-k8s-deploy` |
| Coordinator readiness | `motor-smoke` |
| Inference or feature behavior | `motor-functional` |
| Pymotor smoke / full acceptance orchestration | `motor-smoke-suite` |
| Authorized online instance scaling | `motor-scale` |
| Authorized RAS fault and recovery validation | `motor-reliability` |
| GPQA / accuracy evaluation | `motor-accuracy` |
| Performance workload | `motor-benchmark` |
| Failure evidence | `motor-diagnosis` |

## Related experience

Before a live lifecycle action, query experience. Do not open a card by filename.

```bash
python3 ~/projects/doc/scripts/lookup-experience.py --entry motor-deploy "<redacted task question>"
```

A `HIT` is only a clue. Read the printed card path and perform its `use_check`. Repeat the same query with `--check <card id>=pass|fail` and `--observed "<card id>=<short fact from this run>"`. Do not submit `--check` without that observation. When the session id is already known, pass `--session <id>`. The script only reads `CURSOR_CONVERSATION_ID` when that variable is already set; do not guess another platform's variable name. `CHECK_FAILED` means do not apply the card this time.

## Normal flow

```text
resolve endpoint
→ parity only when local source must replace fixed remote source
→ wheel build only when Motor package replacement is requested
→ edit/review native user_config.json + env.json
→ read-only preflight
→ Motor deploy.py --dry-run
→ authorize (interactive consent, or suite unattended pre-authorization)
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
  authorization for the concrete target:
  - **Interactive:** explicit user consent in the same turn.
  - **Suite unattended:** only when `motor-smoke-suite` supplies a valid signed
    profile pre-authorization whose `allowed_actions` cover the exact action
    (`deploy`, `delete_owned`, etc.) and target. Do not re-prompt inside that
    scope; refuse with `BLOCKED` when the request is outside it. Ordinary chat
    deploy requests are never treated as unattended.
- Use the Motor source tree's native
  `examples/deployer/deploy.py` / `delete.sh`; do not recreate a second deploy
  engine or immutable bundle format in this workspace.
- Never inject source-tree `PYTHONPATH`. Runtime uses image packages or the
  explicitly built Motor wheel through the existing `boot.sh` mechanism.
- Reliability fault injection belongs to `motor-reliability`. Its read-only
  plan and baseline do not authorize injection; obtain interactive consent or a
  matching suite `reliability:*` pre-authorization for the exact target and
  mandatory restoration transaction.
- Report commands, endpoint, namespace, observed resources, and artifacts
  directly. Do not manufacture local workflow run IDs.
