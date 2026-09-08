# Repository instructions

Local development scaffold for the `motor`, `vllm`, and `vllm-ascend` submodules under `sources/`. Workflow code lives under `scaffold/`.

## Repository boundaries

```text
sources/                     motor, vllm, vllm-ascend submodules
scaffold/                    skills, remote-dev, profiles, tools, tests, docs
.motor-workspace-local/      untracked machine inventory and parity state
```

- Keep runtime state under `.motor-workspace-local/` and remote-dev state under `scaffold/.remote-dev/state/`; both are untracked.
- Never write secrets, passwords, or tokens into tracked files.
- Keep `.gitmodules` on community upstream URLs.
- This repository targets Huawei Ascend NPU. Local machines cannot validate `torch`/`torch_npu` runtime behavior; use the remote cluster or Pods.

## Development model

The primary path is **remote-code-parity + one fixed workspace under the shared mount root**, not rebuilding an image for every edit.

- Use native tools for local files and shell work.
- For remote endpoint work, prefer `scaffold/.remote-dev` over raw SSH and read its tool/Skill instructions when the task actually needs remote access.
- Runtime uses image packages or a `boot.sh`-installed Motor wheel. Source-tree `PYTHONPATH` and per-session source copies are forbidden.
- Reuse Motor's deployer and MindCluster resources. Do not implement a competing P/D controller or generic serving engine.

## Deployment routing

Deployment wording is mandatory-routed. When the user asks to `拉起一个服务`, `启动服务`, `部署服务`, `能不能起服务`, `部署前检查`, `检查部署环境`, `构造故障`, `故障注入`, `验证故障恢复`, or equivalent Motor deployment work, read `scaffold/.agents/skills/motor-deploy/SKILL.md` first. Do not select an atomic deploy or validation Skill before the dispatcher.

Environment preflight and deploy dry-run must stay read-only. Apply, scale, delete, restart, config edits, and overwriting fixed remote source directories require explicit consent.

## Skill maintenance

Route every request against the available Skills before taking task actions. If no Skill matches, proceed normally. If a Skill matches, it is the execution contract: read its `SKILL.md` completely and follow it. Do not bypass the matched Skill with generic search, shell commands, or an improvised workflow. Fall back only when the Skill cannot be loaded or executed; report the exact failure before falling back.

Repo-local Skills live under `scaffold/.agents/skills/`; read the selected `SKILL.md` before use. Skills orchestrate existing tools directly. Add a script only for substantial deterministic logic that native tools do not provide.

The live authoring source for `motor-deploy` is `~/.hermes/skills/local/motor-deploy/`. Keep its tracked mirror and Claude shims synchronized. `scaffold/bin/motorws` is an internal parity backend only; other workflows run directly from Skills.

## Agent learning lifecycle

For repository execution, diagnosis, or Skill maintenance (not casual discussion):

- Before acting, read `scaffold/.agents/skills/agent-skill-evolution/references/lessons/INDEX.md`; read only matching verified/promoted lessons. Run `python3 scaffold/.agents/skills/agent-skill-evolution/scripts/learning_check.py pending` to recover outstanding learning work. Pending candidates are evidence, not instructions.
- On a user correction, repeated workaround, wrong routing, or demonstrated Skill/tool mismatch, read `scaffold/.agents/skills/agent-skill-evolution/SKILL.md` immediately, even if the platform did not discover it. Preserve a sanitized candidate before ending the task; do not wait for a separate request to summarize.
- Before the final response, follow that Skill's close procedure and run its receipt check. No learning event is a valid result; an unprocessed event is not. Report a failed check or an authorization/verification blocker instead of claiming the learning loop completed.

This repository authorizes local, sanitized learning receipts/candidates under `.motor-workspace-local/learning/`. It does not authorize storing user preferences, editing shared Skills during read-only work, remote actions, or background scheduling. Apply verified changes only within the current task's explicit improvement authority; otherwise keep the candidate pending with a concrete next action.
