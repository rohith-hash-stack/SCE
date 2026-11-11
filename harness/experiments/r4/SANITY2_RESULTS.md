# R4 sanity run 2: the verification gate (R4-SANITY-2), results (2026-11-11)

**The run:** 40/40 runs, code `d27f79bd`, tasks `tasks_v2.json` (sha `065cfad9…`). Results in `reports/r4/sanity2/`.
Same 20 tasks, both arms, seed 42, turn cap 28. The only change from run 1 is the gate.

## Against the expectations recorded before the run (`AMENDMENTS.md`)

| Expected | Result | Met? |
|---|---|---|
| Early done near zero | accepted early dones: 0 in all four family × arm cells (run 1: 10–20%). Attempted early dones, all rejected: 2 + 2 runs, the zod and pydantic deletes in both arms | yes |
| `stuck_done` appears | 14 runs: delete 5 + 5, feature 2 + 2 | yes |
| Edit-loop runs about unchanged | the first run's 12 edit-loop runs still loop or end at the cap or budget. **Overall loop rates rose** (delete 10% → 40% PRISM / 60% BASELINE; feature 60/40% → 90/100%): runs that used to stop at a false done now continue, and loop | yes, and loops spread |
| Solve rate uncertain | **flat**: delete 2 / 2 (run 1: 1 / 2), feature 0 / 0 (run 1: 0 / 1) | flat |
| (gate leniency) accepted, scorer unresolved | 1 run: delete-04 PRISM, where the hidden tests are stricter (4 pass-to-pass broken) | low |

**Outcomes, run 1 → run 2:**

| | PRISM run 1 | PRISM run 2 | BASELINE run 1 | BASELINE run 2 |
|---|---|---|---|---|
| delete | 1 resolved, 8 unresolved, 1 stuck | 2 resolved, 1 unresolved, 5 stuck_done, 1 stuck, 1 budget | 2 resolved, 7 unresolved, 1 budget | 2 resolved, 5 stuck_done, 2 stuck, 1 budget |
| feature | 4 unresolved, 4 stuck, 2 budget | 2 stuck_done, 3 stuck, 5 budget | 1 resolved, 5 unresolved, 3 stuck, 1 budget | 2 stuck_done, 5 stuck, 3 budget |

**Applied edits:** 83 of 303 attempts (27%), against 56 of 279 (20%) in run 1.

## Reading

- **The gate works as designed.** No run ends on a false done, and every rejection names what is unmet.
- **It converts false dones into `stuck_done`, loops and cap or budget endings, not into solves.** After a rejection,
  the model retries the edit that already failed (`x`, `d`), or rewrites a whole file with an empty `old` (`c`). These
  are the parked edit-tool problems (R2, R3), now in more runs.
- **By the recorded rule** ("if the solve rate holds flat, the edit-error fixes are next"), the edit-error fixes are
  next.

## Findings

1. **A gate wording bug (mine).** The "no file has been changed yet" check runs first and returns alone. In the zod and
   pydantic deletes, both arms (4 runs), the model believed the target did not exist: the prompt's index name is not
   greppable (R1). So the gate never told it "`_generate_definitions` is still defined in `pydantic/json_schema.py` at
   line 375": the one fact that would have corrected it. The pattern was read, grep, done, rejected, ×3, then
   `stuck_done`.
   - **Fix:** with no change, still state the target's status. That is within the gate's approved content: the
     target and its line, never call sites.
2. **Edit-tool loops dominate feature**, in both arms:
   - `crcrcr…`: an empty `old` on an existing file ("exists: give the exact text"), 8 runs;
   - `xxxx`: text not found.
3. **PRISM-only tool loops on feature** (R7): feature-05, -06 and -07 PRISM spent 20+ turns on `prism_change_context`
   or `prism_blast_radius`, for a function that does not exist yet.
4. **Read loops:** feature-10, both arms, ended at the budget after 20+ consecutive `read_file` calls. Long files are
   cut and stubbed, so the model re-reads them (R2, R8).
5. **delete-03 BASELINE** (`RETRET…`, 8 rejections): it edits, runs the tests, calls done and is rejected, over and
   over. Each round is real progress, so it is not `stuck_done`, and it ends at the budget. It never repairs the import
   it broke (69 tests).

## Next, for decision

| # | Fix | Arms | For |
|---|---|---|---|
| 1 | The gate states the delete target's status even when no file has changed | both | finding 1 (a gate bug; small) |
| 2 | Edit errors that help: closest matching lines for "not found", line numbers for "occurs N times", and for an empty `old` on an existing file, "give the exact lines to replace; read them first" | both | findings 2, 5 |
| 3 | A cut `read_file` says what it showed and how to continue ("lines 1-212 of 3265 shown; read on with start=213") | both | findings 2, 4 |
| 4 | Delete prompt: the bare name, its kind and its line | both | finding 1 (R1) |
| 5 | PRISM "not found": the nearest symbols, or a pointer to `prism_new_function_context` for a new function | PRISM | finding 3 (R7) |

**Recommendation:**
- **Take 1–3 together** as the next sanity iteration: the gate fix plus the parked edit-error fixes, per the recorded
  rule.
- **Add 4,** because finding 1 shows it blocks the zod and pydantic tasks in both arms.
- **Hold 5** for the PRISM tool work.
