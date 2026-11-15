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

## Checkpoint 3: the first 10 runs (2026-11-15)

**Runs:** 5 tasks × 2 arms, code `ac0d77ac`. Results in `reports/r4/phase0/`; 0.94 run-hours in total.

| Task | Arm | End | Turns | Primary | Secondary | Pass-to-pass broken (after set-aside) | Fail-to-pass | Edits applied / attempted | PRISM calls ok / invalid name |
|---|---|---|---|---|---|---|---|---|---|
| delete-01 zod | PRISM | done (gate accepted) | 13 | no | **yes** | 1 (0) | – | 3/4 | 0/3 |
| delete-01 zod | BASELINE | turn cap | 28 | no | no | 49 (48) | – | 5/7 | – |
| delete-02 instructor | PRISM | done (gate accepted) | 28 | no | no | 60 (3) | – | 7/10 | 1/0 |
| delete-02 instructor | BASELINE | turn cap | 28 | no | no | 58 (1) | – | 7/12 | – |
| delete-03 pydantic-ai | PRISM | token budget | 25 | no | no | 69 (69, all missing) | – | 3/3 | 3/0 |
| delete-03 pydantic-ai | BASELINE | turn cap | 28 | no | no | 1 (1) | – | 4/4 | – |
| feature-01 hono | PRISM | turn cap | 28 | no | no | 0 | 0/18 | 3/3 | 2/0 |
| feature-01 hono | BASELINE | token budget | 23 | no | no | 0 | 0/18 | 0/0 | – |
| **feature-02 litestar** | **PRISM** | done | 22 | **yes** | yes | 0 | 1/1 | 4/4 | 1/0 |
| **feature-02 litestar** | **BASELINE** | done | 25 | **yes** | yes | 0 | 1/1 | 4/5 | – |

**The pass bar so far** (bar for all 40 runs in brackets):

| Measure | So far | Bar |
|---|---|---|
| Infrastructure deaths | 0 of 10 | ≤ 2 of 40 |
| Parse errors | 0 of 248 turns | < 2% of turns |
| Non-churn edit rate | 38 of 52 attempted (73%); churn 2 | ≥ 60% |
| Tasks solved, primary | **1 of 5** (feature-02, both arms) | ≥ 4 of 20 |
| Tasks solved, secondary | 2 of 5 (plus delete-01 PRISM) | – |

**The first solves of R4:**
- **feature-02 (litestar) was resolved in both arms:** the fail-to-pass test passes, no pass-to-pass test broke, and
  the hidden tests applied.
- **Both arms changed the same four files**, with function F1 = 1.0 against the developer's change.
- **A caveat on the task:** its fail-to-pass check is a single test (the HTML contains the new JavaScript functions),
  so it is the least demanding feature check in the batch.

**Near-misses, each from the agent's own edits:**
- **delete-02 BASELINE** broke 1 test once the pre-existing 57 are set aside; **PRISM** broke 3, all
  `test_legacy_prepare_parse_still_infers_stream`. Both also edited a test file (`tests/coverage/...`).
- **delete-02 PRISM's done was gate-accepted but unresolved:** the 3 broken tests are hidden-patch tests the gate
  cannot see.
- **delete-03 BASELINE** broke only `test_import_errors`.

**delete-03's earlier `missing` results were agent-caused:**
- **The evidence:** this BASELINE run got results for all 69 tests, 68 of them passing. So the environment produces
  them, and the earlier all-`missing` runs had broken an import.
- **Not a pre-existing failure:** its secondary score stays equal to the primary. No unchanged run exists to set
  anything aside, and nothing needs to be.

**Endings:**
- **4 dones,** all gate-accepted, with 0 gate rejections. Each ran the tests after its last edit.
- **4 turn caps.**
- **2 token-budget endings** (the 300,000-token run budget, unchanged). These are new with devstral: its prompts run
  at 12–17K tokens late in a run, so 300K is spent in 18–25 turns. feature-01 BASELINE spent its budget on 23 turns
  of grep and read without an edit.

**The context gap (amendment: "the first 10 runs will reveal any degradation")** — no quality degradation is seen up
to the 16K window. Time per turn rises with prompt size, but tool errors and failed edits do not:

| Prompt size | Turns | Edits (failed) | Tool errors | Mean model seconds |
|---|---|---|---|---|
| 0–4K | 63 | 9 (5) | 8% | 4.7 |
| 4–8K | 63 | 16 (5) | 13% | 7.2 |
| 8–12K | 72 | 14 (1) | 1% | 8.4 |
| 12–16K | 43 | 11 (1) | 2% | 17.0 |
| 16K+ | 7 | 2 (0) | 0% | 24.3 |

- **Truncation:** delete-03 PRISM had 2 cut outputs and 8 stubbed turns (the window rule). That is its only run.

**PRISM invalid symbol names:** 3 of 11 PRISM calls in the 10 runs, all in delete-01 (the pilot). The 8 calls since
then used valid names.

**Projection:** 5.6 min per run, so about 3.8 h for 40 runs plus per-repository setup. One Kaggle session.

**Set-aside lists for the other 15 tasks:** fixed before the `all` stage (AMENDMENTS.md).
- **Clean:** 11 tasks have nothing to set aside.
- **Not established:** delete-04, delete-05, delete-07 and delete-08 have no unchanged run.
- **Flagged:** three of those four have tests broken in every earlier run (4, 13 and 10 tests).

## Final: all 40 runs (2026-11-15)

**The run:**
- 20 tasks × 2 arms, seed 42, code `ac0d77ac`, devstral-small-2.
- 4.05 run-hours (6.1 min per run), in one Kaggle session.
- Results in `reports/r4/phase0/`.
- Every run exited normally (`rc` 0 for all 40).

### The pass bar (pre-registered; all four must hold): **PASS**

| Criterion | Result | Bar | |
|---|---|---|---|
| Infrastructure deaths | **0 of 40** | ≤ 2 | pass |
| Parse errors | **18 of 999 turns = 1.80%** | < 2% | pass, narrowly |
| Tasks solved in at least one arm | **7 of 20** | ≥ 4 | pass |
| Non-churn edit rate | **123 of 183 attempted = 67.2%** (126 applied, 3 churn) | ≥ 60% | pass |

**Claim (amendment wording):** "With devstral-small-2, the fixed harness solves ≥ 4 of 20": **7 of 20.**
- **What it covers:** a capability result for this model on this harness.
- **What it does not cover:** PRISM against BASELINE.

### Solves

| Task | PRISM | BASELINE | Notes |
|---|---|---|---|
| delete-06 (pydantic) | – | **yes** | BASELINE resolved at the turn cap, without calling done |
| delete-08 (werkzeug) | **yes** | **yes** | |
| delete-09 (litestar) | – | **yes** | BASELINE resolved when its token budget ran out, without calling done |
| delete-10 (prettier) | **yes** | **yes** | BASELINE in 5 turns |
| feature-02 (litestar) | **yes** | **yes** | a single fail-to-pass test |
| feature-05 (h3) | – | **yes** | a single fail-to-pass test |
| feature-09 (scrapy) | **yes** | – | 3/3 fail-to-pass |
| **Tasks per arm** | **4** | **6** | |

- **By family:** delete 4 of 10 tasks; feature 3 of 10.
- **Secondary (diagnostic) score:** 8 of 20. delete-01 PRISM is added once its pre-existing failure is set aside.
- **PRISM against BASELINE (4 tasks against 6):** n = 1 seed per task, and Phase 0 is not a PRISM comparison. No
  inference is drawn.

### Endings and false dones

| End | PRISM | BASELINE | All |
|---|---|---|---|
| done (gate accepted) | 6 | 5 | 11 |
| turn cap (28) | 9 | 11 | 20 |
| token budget (300,000) | 5 | 4 | **9** |

- **Gate rejections:** 9 across the 40 runs.
- **Done accepted by the gate, unresolved by the scorer:** 3 runs.
  - delete-01 PRISM: the pre-existing failure.
  - delete-02 PRISM: 3 hidden tests broken by its own edit.
  - delete-04 BASELINE: exactly the 4 tests flagged before the stage (see below).
- **Turns after the last edit:** every `done` ran the tests after its last edit.

### Token-budget endings: a known limitation of this model on this harness

- **How many:** 9 of 40 runs (22.5%) ended on the 300K run budget, at turns 23–27.
- **Why:** devstral's prompts run at 12–17K tokens late in a run.
- **What they cost:**
  - one budget ending still resolved (delete-09 BASELINE);
  - two were cut off while making progress (feature-04 PRISM, 4/6 fail-to-pass; feature-06 PRISM, 15/41);
  - two spent the whole budget without an applied edit (feature-01 BASELINE, feature-03 BASELINE).
- **The turn cap ended more runs (20 of 40) than the budget did.**
- **Unchanged during the run, as decided.** Recorded for the final report.

### Parse errors (18)

- **Where:** concentrated in 2 runs. feature-08 PRISM had 11, the same broken `edit_file` reply repeated. delete-05
  PRISM had 6, feature-04 BASELINE had 1.
- **What went wrong:**
  - **17 degenerate replies:** a well-formed start, then runs of whitespace or `\n` until the reply became invalid
    JSON. Ollama returned no token counts for these (`prompt_tokens` 0, no `done_reason`), so they cannot be placed
    by prompt size.
  - **1 reply hit the 4,096-token answer limit** (feature-04 BASELINE, turn 27).
- **Effect on the result:** without the two worst runs, the rate is 1 of 960 turns.

### Context by prompt size (all 40 runs)

| Prompt size | Turns | Edits (failed) | Tool errors | Mean model s |
|---|---|---|---|---|
| 0–4K | 257 | 27 (7) | 4% | 5.0 |
| 4–8K | 208 | 40 (16) | 10% | 7.2 |
| 8–12K | 230 | 52 (12) | 6% | 8.0 |
| 12–16K | 225 | 49 (16) | 8% | 15.7 |
| 16K+ | 62 | 15 (6) | 10% | 27.6 |
| unknown (the 17 degenerate replies) | 17 | – | – | 25.0 |

- **Error rates:** no clear trend with prompt size (4–10%).
- **Time:** grows about 5× from the smallest prompts to the largest.

### PRISM symbol names

- **Totals:** 38 PRISM calls; 35 returned results; 3 were rejected as invalid names. All 3 rejections were in the
  pilot (delete-01), none after it.

### The flagged tasks (delete-04, -05, -07): exploratory, not a pre-registered reading

- **Scored strictly, as decided.** Under both readings they count as unresolved.
- **What Phase 0 adds:**
  - **delete-05 and delete-07:** the broken pass-to-pass set equals the flagged set exactly in both arms, whatever the
    agent did.
  - **delete-04 BASELINE:** its broken set equals the flagged set exactly. That run passed the visible tests and the
    gate.
- **If the flagged tests were set aside** (exploratory only; the rule requires a start-state run and none was made),
  5 more runs would resolve: delete-04 BASELINE, delete-05 both arms, delete-07 both arms. Tasks solved would be
  **11 of 20**.
- **The start-state check** (deferred) is what would turn this into a measured secondary reading.
