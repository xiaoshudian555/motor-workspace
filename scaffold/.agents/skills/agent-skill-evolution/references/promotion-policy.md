# Lesson promotion policy

Read this reference only when a verified lesson may change shared instructions.

Choose the narrowest durable owner:

| Demonstrated gap | Promotion target |
|---|---|
| One verified, scenario-specific mistake | Keep as indexed lesson |
| Stable component knowledge needed during work | Owning Skill `references/` |
| Execution decision inside one workflow | Owning atomic Skill |
| Request classification or domain handoff | Dispatcher Skill |
| Deterministic condition | Validator, tool, or regression test |
| Cross-domain safety or repository invariant | `AGENTS.md` |

Promotion requires evidence, an affected scenario, scope, counterexamples, and a
durable interception. Repetition strengthens the case but is not mandatory for
a proven high-severity safety gap. Preferences and stylistic disagreements do
not become global rules.

After promotion:

1. mark the lesson `promoted` and link the owning change;
2. run the narrow behavioral or structural check first, then the relevant suite;
3. validate the changed Skill with the repository's Skill validator;
4. synchronize and check generated Claude shims;
5. keep rollback possible by making the change small and attributable.
