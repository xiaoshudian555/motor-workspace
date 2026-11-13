# Review source preflight

Use this before any repository code review, branch review, or "issue still present"
conclusion. Read-only tasks only; no write authorization required.

## When this applies

- User asks to review a branch, PR, commit, or refreshed checkout
- User says the repository/source was refreshed or rebased
- You need to say whether a reported issue is still unfixed

## Counterexamples

- User explicitly asks to analyze one fixed historical commit → use that commit
- Pure documentation discussion with no code-status claim

## Required order

1. Confirm which repository root and submodule commit you are reviewing.
2. If the branch has a remote-tracking counterpart, compare local tip vs `origin/<branch>` before claiming an issue is still absent or present.
3. If the user says the source was refreshed, re-read current `HEAD` and the relevant pre-existing implementation before preserving earlier conclusions.
4. Only after steps 1–3, state whether the issue remains, was already fixed elsewhere, or needs a new change.

## Failure conditions

- Concluding from a stale local snapshot while `origin/<branch>` is ahead
- Claiming missing behavior without checking refreshed baseline code the user already identified

## Platform note

There is no universal write gate in Cursor/Claude today. This file is the
execution entry for review tasks; `AGENTS.md` requires reading matching lessons
from `references/lessons/INDEX.md` before the first repository write.
