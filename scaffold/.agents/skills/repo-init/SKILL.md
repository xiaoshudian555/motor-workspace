---
name: repo-init
description: Initialize a motor-workspace clone with Git, gh, submodules, and fork remotes. Use for 初始化仓库, 配置 workspace, 配置 remotes, first-time setup.
---

# repo-init

This is an Agent procedure, not a Python workflow. Use native local Git and
`gh` commands. Do not create `workspace-ready` records.

## Read-only probe

From the workspace root, inspect all facts before proposing a mutation:

```bash
git rev-parse --show-toplevel
git status --short
git remote -v
git submodule status
gh --version
gh auth status
```

Repeat `git status --short`, `git branch --show-current`, and `git remote -v`
inside each initialized repository under `sources/`. Report missing tools,
authentication, submodules, and unexpected remotes directly.

## Mutations

Ask once for each requested mutation category, then run only that category:

```bash
git submodule update --init
git submodule update --init --recursive   # only when explicitly requested
git remote add upstream <verified-url>
git remote set-url origin <verified-url>
```

- Preserve unknown remotes such as `upstream2`.
- Keep `.gitmodules` on community upstream URLs.
- Never install `gh` from repository code. If it is missing, give the official
  package-manager command and stop on download/network failure.
- Never commit, merge, push, rewrite branches, or delete remotes implicitly.
- Machine and Kubernetes setup belong to later skills.

## Result

Report the current branch, dirty state, remotes, submodule state, `gh` auth
state, commands actually executed, and remaining manual actions. No local run
ID or JSON evidence is required.
