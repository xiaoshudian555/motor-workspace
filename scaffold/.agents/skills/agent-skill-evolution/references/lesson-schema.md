# Lesson schema

Read this reference only when creating or updating a tracked lesson.

Create `references/lessons/<short-name>.md` with kebab-case naming and this
structure:

```markdown
# YYYY-MM-DD — Short title

## Trigger
The user correction, repeated failure, routing error, or proven mismatch.

## Evidence
Minimal sanitized observations that verify the conclusion. Link stable source
or tests; keep raw logs untracked.

## Agent decision gap
Expected decision, actual decision, consequence, and why the reasoning or Skill
instruction allowed it.

## Lesson
The reusable decision rule, stated narrowly.

## Scope and counterexamples
Owning Skill; local, domain, or global scope; cases where the rule must not be
applied.

## Interception
Test, validator, routing case, reference correction, or other durable guard.

## Promotion
Current target: lesson, reference, atomic Skill, dispatcher, AGENTS.md, or tool.

## Status
candidate, verified, promoted, superseded, or retired; include replacement when
superseded.
```

Add one row to `references/lessons/INDEX.md`. Use `candidate` only when the user
asked to preserve an unresolved learning item; candidates must not change Agent
behavior until verified.
