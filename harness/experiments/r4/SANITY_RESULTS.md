# R4 sanity: results (2026-11-11)

**The run:** 40/40 runs (20 tasks × PRISM and BASELINE × seed 42), code `cfabae3f`, task sha `8d89c2d2…`. Results:
`reports/r4/sanity/` (`sanity_report.json`). Sanity runs are development iterations, not pre-registered tests.

| Family | L1 scope | L2 BASELINE resolved (1–4/10) | L3 edit ≥70% P/B | L3 loop <20% P/B | L3 early-done <20% P/B | PRISM arm clean | Resolved P/B (smoke only) |
|---|---|---|---|---|---|---|---|
| delete | 10/10 (pool 79%, a lower bound) | 2/10 pass, but 3 tasks are unscorable (below) | 70% / 70% | 10% / 10% | **20% / 20%** | pass (10/10 scored) | 1 / 2 |
| feature | 10/10 (pool 78%) | 1/10 pass (bottom edge) | **20% / 20%** | **60% / 40%** | 10% / 20% | pass (10/10 scored) | 0 / 1 |

**Layer 3 fails in both families and both arms.** The arms fail the same way, so the cause is the shared harness
and model, not PRISM.

## Findings

1. **A delete scoring bug (harness).** The hidden patch is the developer's test diff. A delete task invites the
   agent to edit the tests that use the target. The developer's diff then no longer applies, and the run is
   unscorable ("hidden tests do not apply").
   - **Affected:** 5 runs. delete-04 (hono) and delete-07 (scrapy) in both arms, and delete-05 (multidict) in PRISM.
   - **Fix:** before applying the hidden patch, restore the files it touches to the start state, so the scored tests
     are the developer's, as in R3. The agent's own test edits are then not scored, which is correct for this
     family.
   - **Re-scoring:** the runs can be re-scored without a model by replaying each run's applied edits.
2. **Failed-edit loops (shared tool layer).** Most `edit_file` calls fail: 81 of 129 in delete, 143 of 150 in
   feature. The model repeats the same failing call up to 27 times. The errors are:
   - "the text to replace was not found";
   - "occurs N times; give a longer, unique span";
   - "FILE exists: give the exact text to replace" (a create on an existing file).

   This drives almost all of the loops, the stuck and budget endings, and feature's 20% edit rate. The error text
   gives the model nothing to correct from: not the closest matching lines, and not where the duplicates are.
3. **Early done with no edit.** In 3 runs the model read the code and called done within 3–4 turns, with no edit:
   delete-01 (zod) and delete-06 (pydantic) in both arms, and feature-02 BASELINE.
4. **Ending done after a visible failure.** delete-03 (pydantic-ai): in both arms the agent removed the whole
   `Python` class, saw `run_tests` fail on an import, and called done anyway.
   - **A related display problem:** the test output shown to the agent ends in importlib frames, so the
     `ImportError` line itself is cut off.
5. **A PRISM-arm-only loop.** feature-04 (typer): `prism_change_context` was called 20 times. A feature task has no
   anchor, and repeated calls returned nothing new. The PRISM arm itself ran cleanly: no crashes, every run scored.
6. **Layer 2:**
   - **Delete:** the baseline solved 2 of 10 (werkzeug, prettier). Only 7–8 tasks were scorable in each arm, so the
     true rate is unknown until finding 1 is fixed.
   - **Feature:** 1 of 10 (h3-05). That is at the bottom edge; with findings 2 and 3 in the way, it says little yet
     about task validity.

## What this means for R4

- **Not ready for R4.** Layer 3 measures whether the loop gives a task a fair chance, and it does not yet: the edit
  tool's failures dominate both arms.
- **The sanity check did its job.** It found a scorer bug and a shared tool-layer problem before the 240+ run R4.
