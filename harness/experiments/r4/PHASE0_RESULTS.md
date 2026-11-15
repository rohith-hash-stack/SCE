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

## Task validity, first10 batch (checked 2026-11-15, before the first10 stage)

**The batch:** delete-01, feature-01, delete-02, feature-02, delete-03. All their earlier scored runs were checked
(sanity 1–4 and the pilot).

**The method:** a pass-to-pass test is "pre-existing broken" when it is not `passed` in a run that left the
repository unchanged. Such a run's score reflects the start state in the run environment.

| Task | Unchanged runs | Pre-existing broken pass-to-pass tests | Finding |
|---|---|---|---|
| delete-01 (zod) | 4 (sanity 1, 2; both arms) | **1** (`recursive type with \`id\` meta`); it is broken in all 10 runs of every round | **confirmed** |
| delete-02 (instructor) | 2 (sanity 2 PRISM, sanity 3 BASELINE: edits attempted, none kept) | **57**, the identical set in both: `tests/v2/test_request_local_streaming.py`, 7 test functions × provider handlers (overlap, cancellation, stream/non-stream) | **confirmed** |
| feature-01 (hono) | 4 | 0 | clean |
| feature-02 (litestar) | 4 | 0 | clean |
| delete-03 (pydantic-ai) | **0** | unknown | **not established** (see below) |

**What delete-01 and delete-02 have in common:**
- **They passed at gating.** Gating classes a test as pass-to-pass only when it passed at the start and at the
  commit, in the gating environment.
- **They fail in every Kaggle run.** So the breakage is a difference between the gating environment and the run
  environment, not the agent.
  - **delete-01:** a duplicate test ID in zod's test reporting is the likely mechanism. One ID fails while its
    prefixed twin passes.
  - **delete-02:** the async streaming tests point to timing or concurrency in the run environment. That is a guess,
    not checked.
- **delete-02's runs that broke more than 57** (58, 63, 64) did so through their own edits.

**delete-03:**
- **All 69 pass-to-pass tests are `missing`** (no result) in all 8 earlier runs.
- **Every one of those runs edited `evaluators/common.py`.** Sanity 1's root cause R4 records agents removing the
  class and then `run_tests` failing on an import, which would produce exactly this.
- **So the missing results may be the agents' own doing.** Without a run that leaves the repository unchanged, this
  cannot be told apart from a broken environment.
- **Its secondary score equals the primary** until a start-state scoring run is made.

**Secondary score of the pilot,** under the rule recorded in AMENDMENTS.md:
- **delete-01 PRISM: resolved.** The target is absent; with the one pre-existing test set aside, 0 pass-to-pass tests
  are broken.
- **delete-01 BASELINE: unresolved,** with 48 tests broken by its own edits.
- **The primary score is unchanged:** 0 of 2.
- **Order of events:** the rule was set from the unchanged-run evidence above, and applied to the pilot afterwards.

## PRISM tool calls: invalid symbol names (devstral)

**What happened:** all 3 of the pilot's PRISM calls (`prism_change_context` ×2, `prism_blast_radius`) were rejected
before any lookup.

| Call | Symbol devstral sent | Result |
|---|---|---|
| turn 5 | `packages/zod/src/v4/classic/schemas.object` | `error: invalid symbol name` |
| turn 6 | `zod/src/v4/classic/schemas.object` | same |
| turn 7 | `zod/src/v4/classic/schemas.object` | same |

**What PRISM expects:**
- **The validator** (`src/prism/mcp/security.py:88`, `validate_symbol_name`) allows only `^[\w.\-]+$`: a dotted name
  with no `/`.
- **devstral sent a file path with a `.symbol` suffix,** and the `/` is rejected.
- **Accepted forms in earlier rounds** (86 successful calls) were dotted names: `Crawler.crawl`,
  `scrapy.crawler.Crawler.crawl`, `pydantic_ai_slim.pydantic_ai.models.fallback.FallbackModel`.
- **For this target** a bare `object` or a dotted module path would pass the validator. Which form PRISM then
  resolves for zod is not checked.

**This failure is new with devstral:** in sanity 1–4 (Qwen models), no PRISM call was rejected as an invalid name.
The 99 earlier errors were "not found" lookups.

**What the message does not say:** `invalid symbol name` never states the accepted format.

**Possible later fix in the PRISM tool layer** (not made; Phase 0 runs unchanged):
- the error message states the expected form, with an example;
- or the tool layer maps `path/to/file.ext` plus a name onto the dotted form.

**Tracked through Phase 0:** the share of PRISM calls rejected as invalid names, per run.
