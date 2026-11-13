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

## Worktree prohibition

**禁止切 worktree。** Never create, switch to, or work in a `git worktree` for this workspace or its `sources/` submodules — no `git worktree add`, no isolated worktrees under `.motor-workspace-local/worktrees/`, no worktree-isolated subagent runs. Edit the existing checkout in place. If the target branch is checked out elsewhere, the tree is dirty, or you believe isolation is needed, report the blocker and ask first instead of isolating on your own.

## Development model

The primary path is **remote-code-parity + one fixed workspace under the shared mount root**, not rebuilding an image for every edit.

- Use native tools for local files and shell work.
- For remote endpoint work, prefer `scaffold/.remote-dev` over raw SSH and read its tool/Skill instructions when the task actually needs remote access.
- Runtime uses image packages or a `boot.sh`-installed Motor wheel. Source-tree `PYTHONPATH` and per-session source copies are forbidden.
- Reuse Motor's deployer and MindCluster resources. Do not implement a competing P/D controller or generic serving engine.

## Deployment routing

Route through `motor-deploy` only when the user explicitly invokes it, or when
all three deployment anchors are present: (1) Motor's native deployer or an
existing Motor-native deployment, (2) a concrete target environment and named
service/model, and (3) a live lifecycle action such as preflight, deploy,
status, restart, stop, scale, fault injection, or recovery validation. Generic
phrases such as `拉起一个服务` or `部署服务` are not sufficient. Docker-only and
RBG workflows, local scripts/guides/config examples, and code-only work do not
route to `motor-deploy` unless the request separately satisfies all three
anchors. Once matched, read `scaffold/.agents/skills/motor-deploy/SKILL.md`
before selecting an atomic deploy or validation Skill.

Environment preflight and deploy dry-run must stay read-only. Apply, scale, delete, restart, config edits, and overwriting fixed remote source directories require explicit consent.

## Skill maintenance

Route every request against the available Skills before taking task actions. If no Skill matches, proceed normally. If a Skill matches, it is the execution contract: read its `SKILL.md` completely and follow it. Do not bypass the matched Skill with generic search, shell commands, or an improvised workflow. Fall back only when the Skill cannot be loaded or executed; report the exact failure before falling back.

Repo-local Skills live under `scaffold/.agents/skills/`; read the selected `SKILL.md` before use. Skills orchestrate existing tools directly. Add a script only for substantial deterministic logic that native tools do not provide.

The live authoring source for `motor-deploy` is `~/.hermes/skills/local/motor-deploy/`. Keep its tracked mirror and Claude shims synchronized. Global user catalogs hide this skill (`disable-model-invocation`); Cursor in this repo discovers `.cursor/skills/motor-deploy/`. `scaffold/bin/motorws` is an internal parity backend only; other workflows run directly from Skills.

## Repository code review entry

For branch review, PR review, commit review, or any task that claims an issue is
still present/absent in source (read-only or otherwise):

- Before the first conclusion or repository write, read matching rows in
  `scaffold/.agents/skills/agent-skill-evolution/references/lessons/INDEX.md`.
- Then read `scaffold/.agents/skills/agent-skill-evolution/references/review-source-preflight.md`
  and apply its order: confirm repo root/commit → compare local vs `origin/<branch>`
  when both exist → re-read refreshed baseline code before preserving old conclusions.
- Counterexample: if the user explicitly fixes the analysis commit, use that commit.

There is no platform write gate here; this is the execution entry for review tasks.

## Agent learning lifecycle

For repository execution, diagnosis, or Skill maintenance (not casual discussion):

- **Before the first repository write or modification**, read
  `scaffold/.agents/skills/agent-skill-evolution/references/lessons/INDEX.md`;
  read only matching verified/promoted lessons. Run
  `python3 scaffold/.agents/skills/agent-skill-evolution/scripts/learning_check.py pending`
  to recover outstanding learning work. Pending candidates are evidence, not instructions.
- Read-only exploration does not require a receipt, but review tasks that judge code
  status must still follow the review entry above before concluding.
- On a user correction, repeated workaround, wrong routing, or demonstrated Skill/tool mismatch, read `scaffold/.agents/skills/agent-skill-evolution/SKILL.md` immediately, even if the platform did not discover it. Preserve a sanitized candidate before ending the task; do not wait for a separate request to summarize.
- Before the final response, follow that Skill's close procedure and run its receipt check. No learning event is a valid result; an unprocessed event is not. Report a failed check or an authorization/verification blocker instead of claiming the learning loop completed.

This repository authorizes local, sanitized learning receipts/candidates under `.motor-workspace-local/learning/`. It does not authorize storing user preferences, editing shared Skills during read-only work, remote actions, or background scheduling. Apply verified changes only within the current task's explicit improvement authority; otherwise keep the candidate pending with a concrete next action.
