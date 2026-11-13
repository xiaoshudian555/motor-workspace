# 2026-09-11 — Compare current source before review conclusions

## Trigger
User corrected branch reviews that used stale local snapshots or skipped
refreshed baseline code before saying an issue was still missing.

## Evidence
- 2026-09-10 precision-sampling review: local branch lagged `origin/` and the
  token-width fix was already present remotely.
- 2026-09-11 deployer review: user refreshed Motor and prior explanation missed
  existing namespace/selector backfill logic at current `HEAD`.

## Agent decision gap
Review tasks jumped to conclusions from the last checked snapshot instead of
confirming the current commit relationship or refreshed baseline first.

## Lesson
Before saying an issue is still unfixed in a branch review, compare local and
remote tips when both exist, or re-read current `HEAD` when the user says the
source was refreshed. Apply `references/review-source-preflight.md`.

## Scope and counterexamples
Repository read-only review of branches/commits/submodules in this workspace.
Does not apply when the user explicitly fixes the analysis commit.

## Interception
`AGENTS.md` review entry checklist; `references/review-source-preflight.md`.

## Verification
2026-09-11: merged candidates `stale-branch-review-precision-sampling` and
`refreshed-source-review-staleness`; review preflight added to repository entry.
Behavior observation on the next real review task remains pending.

## Status
verified — entry integrated; next real review still needed for behavior proof
