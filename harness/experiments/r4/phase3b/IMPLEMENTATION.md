# Phase 3b: implementation and verification

**Status:**
- built and verified;
- the Kaggle cell is **not pinned**, nothing has been run on Kaggle, and the amendment is not yet copied into
  `AMENDMENTS.md`;
- awaiting the user's approval.

**The design:** `AMENDMENT_DRAFT.md`, as approved on 2026-11-26.

**The erratum to `FINAL_REPORT.md`** is a separate commit (`e4c1cb93`).

## 1. What was built

| File | Change | Purpose |
|---|---|---|
| `harness/experiments/r3/loop.py` | `PRISM_GW_C`, `PRISM_GW_E` added to the arms; `GW_VARIANTS`, `GW_ARMS`, `PHASE3B_ARMS`, `base_version` | C and E are their own run versions, with PRISM_GW's prompt and tools (`index_doc` and `shared_doc` resolve them to PRISM_GW) |
| `harness/experiments/r3/tools.py` | `ToolLayer.keep_indent` (default off), `ToolLayer.shown_line`; the three printed-line sites use it | arm E in `prism_find_references` (rows, possible references) and `prism_search` |
| `src/prism/gateway/gateway.py` | `Gateway(..., exact_fix=False, keep_indent=False)`; `_shown`; `_exact_fix`; `_entry_done`; the `exact_fix` outcome in `_tick` | arm E in the gateway's quoted lines; arm C's note and its record |
| `harness/experiments/r3/runner.py` | the gateway for `GW_ARMS`; `layer.keep_indent` set only for PRISM_GW_E; the variants in `HISTORY_ARMS`; CLI choices | the arm switches. Arm A's construction is POC 2's call, unchanged |
| `harness/experiments/r3/pilot_run.py`, `harness/experiments/r4/phase2_dryrun.py` | the new versions accepted; dry run `--empty-every-arm` | the cell's runner, and test 7 |
| `harness/experiments/r4/phase3b_tasks.py` → `phase3/phase3b.json` | the task file, the seeds, the 5 shards | one file for every arm |
| `harness/experiments/r4/phase3b_replay.py` | replays recorded transcripts turn by turn (parse errors included) through `run_task` | tests 1–3 and 6 |
| `harness/experiments/r4/phase3b_compare.py` | replay against Kaggle's hashes; replay against replay (`identical`, `indent`, `exact_fix`) | tests 1–3 and 6 |
| `harness/experiments/r4/phase3b_edit_replay.py` | the offline replay of the edit fix | section 4 |
| `harness/experiments/r4/phase3b_analysis.py` | the frozen outcome definitions, the ledger, the single test, dedup | the analysis, after the runs |
| `tests/test_phase3b.py` (15 tests) | arm parity, E's rendering and isolation, C's activation and safety, history, baseline, task file | — |
| `tests/test_prism_history.py` | one expectation updated: `HISTORY_ARMS` now also lists the two variants | the variants are PRISM_GW on deletion tasks; BASELINE_LSP still never builds a snapshot |

## 2. The arms

**A (`PRISM_GW`):** no switch.
- **The runner:** it builds the gateway with POC 2's exact call, `Gateway(work, task["prompt"],
  layer.reference_places)`.
- **The defaults:** `ToolLayer.keep_indent` is off, and the gateway's `exact_fix` and `keep_indent` default to off.
- **Proof that it is unchanged:** test 1.

**C (`PRISM_GW_C`):** `Gateway(exact_fix=True)`.
- **When:** after an `edit_file` that the tool refused (any `error`) or the loop breaker refused, the gateway may add
  one note.
- **The note:** it names the nearest qualifying pending place in that file. It gives the exact line, JSON-quoted, as
  `old`, and the same line with the old name renamed as `new`.
- **What qualifies:**
  - the line has no look-alike use;
  - its text occurs once in the file;
  - the new name is known.
- **Silence:** two equally near places, or no line to measure from with several places, give no note.
- **Order and effect:** the note is computed after every other decision and appended last. It changes no file and no
  decision.
- **Recorded:** the refusal, the refused text, the place, the `old` and `new` given, and the response (`exact`,
  `different` or `none`). Its `warning_type` is `actionable_correction`.

**E (`PRISM_GW_E`):** `ToolLayer.keep_indent` and `Gateway(keep_indent=True)`.
- **The change:** every printed source line gets its own leading spaces and tabs back, before the same stripped text.
- **Caps:** they apply after the indentation.
- **A blank line** prints nothing, as before.
- **The layout** is unchanged.

**BASELINE_LSP:** no code path changed.
- **The runner:** it sets `keep_indent` only for PRISM_GW_E and builds no gateway for it.
- **Its prompt** is POC 2's.

**Historical answers (deletion tasks):** `_historical_references` is not touched. It still prints stripped lines
with either switch (test 4).

## 3. The seven tests

All the replays send the recorded calls of POC 2's Kaggle transcripts, turn by turn, through the real runner with
POC 2's flags, keeping every answer whole.

**The test environment's own outputs** are set apart in every comparison: `run_tests`, and a `done` the done gate
checked.
- They differ run to run, even between two local replays of the same code:
  - random temporary paths (`/tmp/r3run_…`);
  - test durations (`0.78s` against `0.82s`).
- They are never PRISM's answers.

| # | Test | Evidence | Result |
|---|---|---|---|
| 1 | A byte-identical to `d769d27c` | the 15 POC 2 PRISM_GW transcripts, replayed as A (new code), against the `output_sha` Kaggle recorded at `d769d27c` | **614 / 614** PRISM, edit, read and gateway answers identical. The environment's outputs: 20 / 28 identical; the 8 others differ only by temp paths and durations, as two local replays of `d769d27c` itself also do. |
| 1 | (gold replays) | the dry run's gold replay through A on both Phase 3b tasks | resolved; gateway: same places and interventions as POC 2's own gold replay |
| 2 | E = A apart from leading whitespace | the same 15 transcripts replayed as E, against A, all answers whole | **0 mismatches**: 276 answers identical, 338 differ only by leading whitespace inserted after a listing separator. The gateway's interventions are identical on every turn. `prism_search` (not called in those transcripts) is checked on a fixture (`test_prism_search_and_find_references_differ_only_by_indentation`). |
| 3 | C only after a refusal; no decision changed | the same 15 replayed as C, against A | **0 mismatches**: 509 identical, 105 equal to A plus exactly one note, each after a refused `edit_file` (32 tool errors, 73 loop refusals). On 57 refused edits no place qualified, so there was no note. The other interventions are identical on every turn. No file differs (the edit outputs equal A's). |
| 4 | Historical answers unchanged | `test_historical_answers_are_unchanged_with_either_switch`; source check | identical with E on and off; `_historical_references` does not use `shown_line` |
| 5 | Task consistency | `test_task_file_one_for_every_arm_with_only_the_turn_sentence_changed` | one file (`phase3/phase3b.json`, tasks sha256 `506e4047266c…`) for all four arms. p3-hidden-15 equals POC 2's task. p3-hidden-03 differs from `poc.json`'s only in `prompt`, only at "28" → "60". |
| 6 | BASELINE_LSP unaffected | the 15 POC 2 BASELINE_LSP transcripts replayed (new code) against Kaggle's hashes, plus the old code locally; source tests | **839 / 843** identical to Kaggle; the 4 others are jedi's environment, and **201 / 201** identical between old and new code in this container (section 3a) |
| 7 | Replay sanity, every arm | `phase2_dryrun --guards --empty-every-arm` on `phase3b.json`, arms A, C, E, BASELINE_LSP | gold **resolved in all 8** (4 arms × 2 tasks). Empty **unresolved in all 8**: BASELINE_LSP `stuck_done` (the gate rejects); A, C and E `stuck` (the gateway blocks `done` 15 times until the turn cap, identically in the three) |

**Test 7's exit code:** the tool's run returned 1 only because its criterion accepted `stuck_done` and not `stuck`.
It predates gateway arms. The criterion now also accepts `stuck`, only under `--empty-every-arm`; the default is
unchanged.

### 3a. Test 6 in detail

**BASELINE_LSP, new code, against Kaggle's hashes** (15 transcripts):
- **839 of 843 answers are identical.**
- **The 4 others are all `find_references` answers** (jedi 0.19.2, the language server), one per run:
  - hidden-15 seeds 201 and 202, and hidden-16 seed 201: on Kaggle jedi answered "no definition named …", here it
    resolves;
  - hidden-06 seed 203: a different reference list.
- **Environment:** the test environment's outputs give 7 of 24 identical, for the reasons above.

**Old code (`d769d27c`) against new code, both in this container, on those 4 runs:** **201 of 201 answers
identical.**

**Conclusion:** the 4 differences are jedi's behaviour in this container against Kaggle's, the same with or without
the Phase 3b change. Nothing in BASELINE_LSP's path changed. Its prompt is POC 2's, and no switch reaches it (source
tests).

**A note on the replay:** one BASELINE_LSP transcript (hidden-06 seed 203) has a parse error at turn 27. The replay
model now reproduces a recorded parse error in its turn, so later calls keep their turns. The first pass shifted
them; that pass was discarded.

**All the outputs:** `phase3b/verification/`, as `test*.json`.

**The full regression suite** (`pytest tests`, on `f5b379ec`): **3562 passed, 62 skipped, 16 xfailed, 1 xpassed, 0
failed**, in 1 h 46 min. The xpass is the Django feature-mask parity test, a known intermittent, non-strict xfail
that predates Phase 3b.

## 4. The offline edit-fix replay

**Inputs:**
- 586 recorded refusals: 512 under the current message, 74 under the old one;
- across 33 tasks and every stage.

**Every rebuilt file state was verified:** it shows the line the recorded error showed, and the tool still refuses
the call there. 0 were unverified.

**v1, the rule as first pinned,** had two defects that the first pass exposed:
- **Deletion:** an empty `new` left the line's indentation behind instead of deleting the line (4 events).
- **Trailing newlines:** "one line" stripped every trailing newline, so an `old` followed by 30 blank lines counted
  as one line (2 events).
- **v2 fixes those two cases only.** Both passes are kept (`verification/edit_replay_v1.json`, `verification/edit_replay_v2.json`).

**v2, under the current message (512 events):**

| | Count |
|---|---|
| single-line / multi-line `old` | 200 / 312 |
| **applied** | **170** |
| ambiguous (refused) | 0 |
| out of scope: part of a line, or more than leading whitespace (refused) | 29 |
| multi-line (refused) | 313 |
| applied: **intended** (whole resulting file equals the run's own later edit, or the answer key's line) | **169** |
| applied: unintended | **0** |
| applied: undetermined (no later edit, no answer-key line) | 1 |
| applied: breaks the file's parse | **0** |
| the variant writing `new` as given (no re-basing): unintended / breaks the parse | 169 / 138 |

**How the 169 were judged:** 153 against the run's own later edit of that line (the whole file compared), and the
rest against the answer key.

**The undetermined one:** `r4s-feature-01` (TypeScript). The model sent `\t    const caught = false` → `\t    let
caught = false`; the fix gives `    let caught = false` at the file's indentation. Neither the run nor the answer key
edits that line.

**Old message (74 events):** 4 single-line, 4 applied, 4 intended, 0 break the parse.

**What this means:**
- **Coverage:** the fix reaches a third of the current-message refusals (170 / 512). The rest are multi-line edits
  and are out of its scope.
- **Safety:** where it applies, it gives the file the run itself later made. It never applied an ambiguous match.
- **Re-basing is essential:** writing the model's own indentation would have broken the parse in 138 of the 169.
- **Status:** it stays out of every Kaggle arm, as decided.

## 5. The analysis, validated on POC 2's own records

**The check:** `phase3b_analysis.py` was run on POC 2's nine p3-hidden-15 Kaggle runs. It finds exactly the known
stuck runs:
- **Failed:** PRISM_GW seeds 201 and 203, and PRISM_LSP seeds 201 and 203. Each has `tests/conftest.py:181`
  triggered and not recovered within 5 turns.
- **Not failed:** every BASELINE_LSP run, whose triggers were all recovered.

**Each place is recorded with:**
- its outcome (`no_mismatch`, `recovered` or `not_recovered`);
- its trigger and recovery turns;
- the mechanism.

**Each run is recorded with:**
- edits, failed edits, whitespace mismatches and loop refusals;
- unlocated refusals;
- the warning types (problem description or actionable correction), and C's responses.

**The test:** deduplication by (tool, arguments) sequence comes first, then the two-sided Fisher test on A against E.
The undeduplicated test is reported beside it.

## 6. Allocation, seeds, shards

**The allocation:**
- p3-hidden-15: seeds 301–315 × 4 arms = 60 runs;
- p3-hidden-03: seeds 301–305 × 4 arms = 20 runs;
- **80 runs in total.**

**Shards:** 5 of 16 runs each.
- Shard k holds p3-hidden-15's seeds 3k−2 to 3k and p3-hidden-03's seed k.
- The arms rotate by one per task-and-seed block.
- **Checked:** 80 distinct runs, and each arm holds each position in its block exactly 5 times.

**Time:** about 12.7 GPU hours at POC 2's measured 9.5 minutes per run.

## 7. Remaining risks

1. **Power:**
   - **The estimate:** 0.85 for a large effect (failure 0.67 → 0.10), 0.30 for 0.67 → 0.33.
   - **Uncertain inputs:** A's rate rests on 6 POC 2 runs, and collapsed identical transcripts reduce n.
   - **Reading the result:** a non-significant result says nothing about no effect.
2. **E reaches the gateway's every quoted line,** worklists included. That is what was approved: it is the place
   where the model saw `) -> SessionMiddleware[...]:` stripped.
3. **C is silent on 57 of 162 refused edits in the replay:**
   - no qualifying place, two equally near, or the file has no pending place;
   - by design (no ambiguous target), so C's reach is partial.
4. **The read paths stay as they are in every arm:** `read_file`, `prism_read` and the edit tool's own error still
   print `number<TAB>text`. Seed 203 copied that tab.
5. **The jedi answers differ between Kaggle and this container** for BASELINE_LSP's `find_references` (section 3a).
   It is an environment difference, and the same in old and new code; on Kaggle every arm runs in one environment.
6. **The edit-fix rule changed once (v1 → v2)** after its first replay, to fix two semantic defects. It is disclosed
   above and was not tuned on the outcomes. It is not in any Kaggle arm.
