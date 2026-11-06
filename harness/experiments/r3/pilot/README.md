# R3 pilot tasks

The pilot verifies the pipeline: the builders, the loop, the tools, scoring and the analysis, in both languages,
before R3a. Its tasks are never reported as results (pre-registration, "Pilot-only tasks").

## Decisions recorded before the pilot (2026-11-06)

**Family N's pilot is not run.** "Family N's pilot is not run. The pilot window yielded 1 candidate; mining
April–September 2024 added 1 more. The cause is the docstring/JSDoc rule, which is the task's spec — relaxing it would
change what Family N tests. Family N is deferred to R3b with a proper mining plan: different repos, or a different
spec source (PR description), or acceptance that 'implement to spec' is a rare real-world pattern in these repos. The
pilot runs Family B only."
- Family N's definition is not amended, and the docstring rule is not relaxed. It is the spec: without it the task is
  not defined.
- The one gated Family N task (`tasks_N.json`) is kept as a record and not run.

**The pilot window is widened for JS/TS Family B (amendment R3-6).** "Pilot window widened to 2024-04-01–2024-11-30 to
admit JS/TS Family B tasks, so the pipeline can be verified in both languages. R3a's main window is unchanged. The
pilot's role is pipeline verification, not scientific claim."
- The widened window applies to the JS/TS repositories only. The Python repositories keep 2024-10-01 to 2024-11-30.
- Target: 10 Family B tasks, at least 2 of them JS/TS. If the seeded draw does not reach 2 JS/TS tasks, that is
  reported, not forced.

**Depth is a pilot limitation, and a requirement on R3a's mining.** "The pilot's Family B tasks all have exactly one
changed caller. The pilot verifies the pipeline, not depth behavior. R3a's mining must report the caller-count
distribution; if the main window skews to 1-caller tasks, the scaling test is unrunnable and mining must be
re-strategized before R3a's hash-commit."

**Known limitations of the scoring** (correct applications of the pre-registration; recorded, not changed):
- "The first turn at which every F2P test passes" is not computed. The hidden tests are never in the tree during a
  run.
- A run that stops at the turn cap or the budget is unresolved even when its final tree passes. `tree_resolved` is a
  diagnostic column only.
- The file-F1 answer key is every non-test file the commit changed, changelogs and lockfiles included.
- The visible tests are the parent's versions. When the commit changed them, a correct change can fail some visible
  tests (pilot-B-08 of the first gate: 2 of 11). This is a known limitation of parent-revision tests, noted and not
  fixed.

## Files

- **First gate** (pilot window 2024-10-01 to 2024-11-30, both families; superseded for Family B by the widened gate
  below):
  - `yield.json`, `candidates_B.json`, `candidates_N.json`;
  - `gate_B.jsonl`, `gate_N.jsonl`;
  - `attrition_B.json`, `attrition_N.json`;
  - `tasks_B.json` (10, sha256 of the task list `bf87d595…`), `tasks_N.json` (1, not run).
- **Widened gate** (Family B; amendment R3-6): `widened/`.
