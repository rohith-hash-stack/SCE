# Phase 0 results (devstral-small-2, AMENDMENTS.md "Phase 0 on a local model")

## Checkpoint 2: the pilot (2026-11-15)

**Run:** `r4s-delete-01` (zod, delete the getter `object.shape`), both arms, seed 42, code `ac0d77ac`.
**Results:** `reports/r4/phase0/`.

| | PRISM | BASELINE |
|---|---|---|
| End | `done`, gate accepted, turn 13 | turn cap (28), outcome `stuck` |
| Scorer | **unresolved**: target absent; 1 pass-to-pass test broken (see below) | unresolved: 49 pass-to-pass tests broken |
| Parse errors | 0 / 13 turns | 0 / 28 turns |
| Edits applied / attempted | 3 / 4 (1 ambiguous-match error, fixed next turn) | 5 / 7 (1 ambiguous match, 1 not found) |
| Tests run after last edit, before done | yes (turn 12, all visible tests pass) | – (no done) |
| PRISM tools | 3 calls, **all errors** (`invalid symbol name`) | – |
| Wall time (run + scoring) | 3.4 min | 5.2 min |

**Infrastructure:**
- **Deaths:** 0 of 2 runs.
- **Smoke test:** the JSON tool-call smoke test passed.
- **Model time:** 2.5–14 s per turn, at prompts of up to 12,127 tokens. No slowdown appeared as prompts grew, but the
  16K window was not reached.

**What each arm did:**
- **PRISM:**
  - It replaced the lazy getter with an eager `shape: util.objectClone(shape)` in `object`, `strictObject` and
    `looseObject`, then ran all five visible test files (all pass) and called `done`.
  - Its three PRISM calls guessed the symbol format (`packages/zod/src/v4/classic/schemas.object`) and were rejected;
    PRISM contributed nothing.
- **BASELINE:**
  - It deleted the getters outright, leaving `def` without `shape`. 28 `object.test.ts` tests failed at its turn-19
    test run.
  - From turn 14 it read the same lines (1195–1210) six times and never repaired them: a loop, until the turn cap.

**Measurement 8 (gate accepted, scorer unresolved): the PRISM run's one broken test is pre-existing.**
- **The test:** `recursive-types.test.ts::recursive type with \`id\` meta`. It comes from the hidden patch (the
  upstream commit's new test) and is listed as pass-to-pass.
- **It fails in every delete-01 run of every round,** including the four sanity-1/2 runs that made no edit at all.
  Its duplicate ID (`...::src/v4/classic/tests/recursive-types.test.ts recursive type with \`id\` meta`) passes in
  all of them.
- **So it behaves as a fail-to-pass test** that the agent can neither see (it is hidden) nor be told about.
- **Consequence:** delete-01 can only score as resolved if the agent's change happens to make that hidden test pass.
  Whether the PRISM run's eager clone would have done so cannot be told, because the test fails before any edit.
- **This is a task-validity issue, not a harness or model failure.**
- **Not changed:** changing the task or its scoring now would be a post-hoc change to the pre-registered task set,
  so it is reported here and left to the user.

**Related observation (not established):** the same no-edit check flags `r4s-delete-02` (instructor, 57
pass-to-pass tests broken with no agent edit in two runs). Not yet examined.

**Projection:**
- **Per run:** ~4.3 min mean over the two runs (a 28-turn run took 5.2 min).
- **40 runs:** about 3–4 h of runs, plus per-repository setup. One Kaggle session; stop rule (b) (two sessions) is
  not near.
- **Caveat:** an estimate from two runs of one task.
