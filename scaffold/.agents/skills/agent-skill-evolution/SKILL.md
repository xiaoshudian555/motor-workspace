---
name: agent-skill-evolution
description: Capture verified Agent or Skill decision failures as reusable lessons and promote repeated lessons into workspace rules, references, Skills, tools, or regression tests. Use after a user correction, wrong Skill routing, unsafe or ineffective workflow, repeated Agent mistake, or an explicit request to improve Skills from experience. Do not use for an ordinary product or environment failure unless evidence demonstrates an Agent or Skill gap.
---

# Agent Skill evolution

Turn verified Agent mistakes into small, reviewable changes that prevent the
same mistake without constraining unrelated work. This is a learning control
plane; it does not replace the owning execution or diagnosis Skill.

## Entry and authority

Enter only when evidence shows at least one of these:

- the user corrected an Agent decision or factual assumption;
- the wrong Skill was selected, or a required dispatcher was bypassed;
- a Skill instructed an unsafe, ineffective, stale, or incomplete workflow;
- the same Agent workaround or failure has recurred;
- source, tool behavior, and a Skill reference are proven inconsistent.

A product failure alone is not a learning event. First use the owning Skill to
diagnose or fix it. A read-only diagnosis request does not authorize tracked
Skill changes: report a lesson candidate and wait for an explicit improvement
request. Never broaden deployment, remote mutation, or fault-injection consent.

## Learning loop

1. Read `references/lessons/INDEX.md` and reuse or update a matching lesson
   instead of duplicating it.
2. Build a short evidence chain: expected Agent decision, actual decision,
   observed consequence, and the demonstrated decision or Skill gap. Separate
   the Agent mistake from the underlying product failure.
3. Reject guesses, one-off environment failures, simple typos, preferences, and
   lessons without a reproducible scenario or other reliable verification.
4. When recording a lesson, read `references/lesson-schema.md`. Store raw logs
   only under an allowed untracked state directory such as
   `.motor-workspace-local/`; tracked lessons contain only minimal, sanitized
   evidence.
5. Add the cheapest durable interception: a behavioral test, validator, routing
   case, explicit invariant, or reference correction. Do not rely on prose when
   a mechanical check is practical.
6. When changing shared instructions, read `references/promotion-policy.md` and
   make the narrowest change at the owning layer. One verified case normally
   remains a lesson; repeated or high-confidence cases may be promoted.
7. Validate the affected Skill and interception, synchronize generated Claude
   shims, then add or update the INDEX row. Report what was learned, what changed,
   and what remains unverified.

## Guardrails

- Never let a Skill edit itself merely because its execution failed.
- Never record secrets, tokens, passwords, private endpoints, full logs, or
  transient cluster state in tracked files.
- Do not turn a single example into a global rule without scope and
  counterexamples.
- Preserve authorization boundaries; a lesson cannot retroactively authorize a
  mutation.
- Retire or supersede obsolete lessons instead of silently deleting history.
- Source code and live behavior are ground truth. Update stale references with
  the same change that proves the mismatch.

## Output

Return a compact learning report:

```markdown
## Lesson
{verified Agent/Skill gap, or rejected candidate and why}

## Scope
{owning Skill and local/domain/global scope}

## Interception
{test, validator, reference, Skill, dispatcher, or AGENTS.md change}

## Verification
{checks run and remaining uncertainty}
```
