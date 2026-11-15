# Phase 1 step 1a: the PRISM / BASELINE gap in Phase 0 (analysis only, 2026-11-15)

**Source:** the 40 Phase 0 turn logs (`reports/r4/phase0/`).
- **No model calls.**
- **The plan's hypotheses:** H1–H5 (`PHASE1_PLAN.md` §1.2).

## The four discordant tasks

**Both arms solved delete-08, delete-10 and feature-02.** Four tasks differ:

### delete-06 (pydantic, remove `GenerateJsonSchema._generate_definitions`): BASELINE solved, PRISM did not

- **Turns 1–6 are identical in both arms:** the same reads and the same grep.
- **BASELINE:**
  - **Turn 7:** it reads the public method `generate_definitions`.
  - **Turn 8:** it folds the private method's body into it.
  - **Turns 9–18:** it deletes the private method, fixes `type_adapter.py`, and has the tests passing by turn 19.
  - **Then it loops:** six identical reads of lines 350–410. Its `done` at turn 28 is rejected for 1 test.
  - **The scorer counts it resolved** (budget 250K).
- **PRISM:**
  - **Turn 7:** `prism_change_context` (4,000 tokens, at the cap).
  - **Turn 9:** it deletes the private method *before* moving its body (the wrong order).
  - **Turn 16:** `prism_blast_radius` (4,000).
  - **Turn 18:** `prism_new_function_context` (3,999).
  - **By turn 18 the prompt is 16.6K.** From turn 19 the window rule stubs 7–14 older outputs.
  - **Turn 20:** it rewrites `generate_definitions`, but 11 tests fail.
  - **Turn 27:** the budget ends the run.
  - **PRISM outputs carry 57% of this run's budget** (an upper bound, ignoring stubbing).
- **Reading: H1 (context cost) is supported.** About 12K tokens of PRISM output in 11 turns pushed the run into
  stubbing and onto the budget. Whether the turn-9 ordering came from the PRISM output cannot be told from the log.

### delete-09 (litestar, remove `params.Dependency`): BASELINE solved, PRISM did not

- **Both arms take the same route:** remove the export and the function, then fix the test imports one file at a
  time.
- **PRISM:**
  - **Turns 7–8:** `change_context` and `blast_radius`.
  - **`blast_radius` reported "0 transitive callers",** while grep (turns 3–4) had shown many uses: in tests, and as
    default values (`= Dependency()`), which are not call edges.
  - **At the turn cap** it was still replacing `Dependency()` calls inside test bodies, with 18 tests broken.
- **BASELINE:**
  - **The same import fixes.**
  - **It also deleted the deprecated-marker test.** A deleted test is not counted, by the task's rule.
  - **It ran out of budget at turn 25,** already resolved.
- **Reading:**
  - **H3 (misleading context) is weakly supported:** the "0 callers" answer was wrong for this use, but the agent
    went on from grep's results anyway.
  - **The two PRISM calls cost 2 turns.**
  - **Mostly a trajectory difference** (which test to delete instead of fix).

### feature-05 (h3, add `composeMiddleware`): BASELINE solved, PRISM did not

- **Both arms produced the same two edits** (a function in `middleware.ts`, an export in `index.ts`).
- **PRISM named the function `precomposeMiddleware`.**
  - **Where the name came from:** its own earlier guess ("precompose", grepped at turn 4), passed into
    `prism_new_function_context` at turn 10 and kept.
  - **The snapshot test expects `composeMiddleware`.** PRISM ran the tests 3 times, failed, and never renamed.
- **BASELINE grepped the same pattern 14 times in a row** (turns 10–23, a loop), then wrote `composeMiddleware` and
  passed.
- **Reading:**
  - **PRISM did not supply the wrong name;** the model did. The PRISM call echoed it back and may have anchored it.
  - **Otherwise this is trajectory variance (H5).**

### feature-09 (scrapy, add `_asyncio_reactor_path`): PRISM solved, BASELINE did not

- **The PRISM arm made no PRISM calls at all** in this run. It found the constant's name in the test's import, added
  it, and passed.
- **BASELINE:**
  - **It passed pytest node IDs as file names to `run_tests`** (twice: "no such test file").
  - **It read `crawler.py` for 10 turns** and never edited.
- **Reading: pure trajectory variance (H5).** PRISM's tools played no part.

## The hypotheses

| # | Hypothesis | Evidence | Verdict |
|---|---|---|---|
| H1 | Context cost | PRISM outputs average 2,207 tokens; `new_function_context` averages 3,665 and 11 of 38 calls hit the 4,000 cap. Carried forward in the history, PRISM outputs account for ~22% of the PRISM arm's budget on average, and for 16–57% in its five budget-ended runs. delete-06 is the clear case | **supported** |
| H2 | Symbol format | 3 of 38 calls rejected, all in delete-01 (not a discordant task) | **refuted** as a cause of the gap |
| H3 | Misleading context | one clear wrong answer (delete-09 `blast_radius`: 0 callers for a function used as a default value); no run where an edit followed wrong PRISM guidance | **weak** |
| H4 | Fixed cost of the PRISM tool menu | the PRISM system prompt is 399 tokens against BASELINE's 252: +147 per turn, about 3.7K per 25-turn run (~1.2% of the budget) | **refuted** as a main cause |
| H5 | Seed noise | feature-09 (PRISM arm made no PRISM calls) and feature-05 (same edits, a different name the model chose) are trajectory differences unrelated to PRISM's tools: 2 of the 4 discordances | **supported** for half the gap; tested in step 1e (4 tasks × 3 seeds × 2 arms) |

## What this means for Phase 1

- **The gap is mostly noise plus one real mechanism.** That mechanism is the size of PRISM's outputs, carried turn
  after turn.
- **Step 1d's history compaction acts on it directly:** PRISM outputs older than the 6 newest tool outputs shrink to
  12 lines, under the same rule as BASELINE's `related_code`.
- **A PRISM-side compact output mode** (`PHASE1_PLAN.md` §1.3: a per-call cap near 800 tokens instead of 4,000) is
  the candidate intervention. Whether it is still needed after compaction should be decided from the re-baseline.
- **Nothing in Phase 0 shows PRISM's content leading the agent into a wrong edit.**
