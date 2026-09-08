# 2026-09-07 — Learning instructions without lifecycle integration

## Trigger
User reported that the existing self-improvement Skill had not actually formed
a learning loop and explicitly requested implementation.

## Evidence
Before this change the learning INDEX had no entries. The Skill described what
to do after invocation; repository entry instructions had no learning start or
close step. References to the Skill were principally its README and Claude shims.
No claim is made that no learning ever occurred outside this path.

## Agent decision gap
Having a Skill package was treated as sufficient for automatic learning. Normal
execution had no explicit route to capture, recover, validate and consume it.

## Lesson
For this repository, follow the AGENTS.md learning lifecycle. Keep pending
evidence separate from executable guidance. A valid receipt is not proof that
a model's decisions improved; report the behavioral evidence separately.

## Scope and counterexamples
Repository execution, diagnosis and Skill maintenance. Casual conversation and
ordinary environment failures do not require invented learning events. Missing
authority is a pending state, not permission to change shared instructions.

## Interception
AGENTS.md start/event/close entry, Cursor always-applied pointer, existing Claude
AGENTS.md entry; learning_check.py and scaffold/tests/test_learning_loop.py.

## Promotion
Lifecycle wiring was explicitly authorized and implemented. No MCP runtime was
modified. The learning gate checks records, not model honesty or client hook
delivery. Fresh-session behavioral acceptance remains pending in local state.

## Status
verified — structural gap confirmed. Local mechanical tests passed; do not infer
cross-client autonomous behavior or unattended background execution.
