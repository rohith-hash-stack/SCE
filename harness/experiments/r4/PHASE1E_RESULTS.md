# Phase 1 step 1e: results (2026-11-15)

**The run:**
- Kaggle, devstral-small-2, code `9e46c23c`.
- Part A: 2 of 2 degeneration checks (340 model calls).
- Part B: 24 of 24 runs, 2.43 run-hours.
- Results in `reports/r4/phase1e/`.
- Pre-registration in `AMENDMENTS.md`, "Phase 1 step 1e".

## Part A: the trailing-off parse errors: the fix does not meet its criterion

### The prompts

| Run | Target turns | Faithful to Phase 0 |
|---|---|---|
| delete-05 PRISM | 18, 19, 20, 21, 24, 27 | **6 of 6** (only a `run_tests` output with a matching head differs) |
| feature-08 PRISM | 8, 15, 19–27 | **1 of 11** (turn 8). Two earlier `run_tests` outputs (turns 12, 16) differ from Phase 0's even in their heads: the tests ran differently on this Kaggle machine |

- **Exclusion, as pre-registered:** turns 15–27 of feature-08 are excluded.
- **The comparison:** 7 turns, 70 calls per condition.

### The result on the 7 faithful turns (the pre-registered comparison)

| | Parse errors | Degenerate tails over 400 chars | Cut | Repaired |
|---|---|---|---|---|
| before | **39 of 70 (56%)** | 0 | – | – |
| after | **34 of 70 (49%)** | 0 | 0 | 0 |

**Against the expectations recorded before the run:**

| Expectation | Result | Holds? |
|---|---|---|
| 1. The problem reproduces (≥ 30%) | 56% | **yes** |
| 2. after ≤ a quarter of before (≤ 14%) | 49% | **no** |
| 2. Every degenerate tail cut | no tail ever exceeded 400 characters, so the cut never fired | (vacuous) |
| 3. The feature-08 shape is repaired | the shape (`args` complete, only the final brace missing) never occurred | not tested |
| 4. after is faster | delete-05 is faster on 4 of its 6 turns | partly |

**Per turn, before → after (10 calls each):**

| Turn | Parse errors |
|---|---|
| delete-05, turn 18 | 1 → 3 |
| delete-05, turn 19 | 10 → 9 |
| delete-05, turn 20 | 9 → 10 |
| delete-05, turn 21 | 1 → 6 |
| delete-05, turn 24 | 8 → 4 |
| delete-05, turn 27 | 9 → 0 |
| feature-08, turn 8 | 1 → 2 |

### What the replies show: my Phase 1 diagnosis of the shape was wrong

**Every invalid reply, in both conditions, has the same shape:**
- **It is short:** 217–485 characters, apart from three long ones of about 4.5K in "after".
- **It stops inside a JSON string:** usually the `old` or `new` of an `edit_file`, after a run of 16–32 escaped `\n`
  sequences.
- **Ollama sends no stop reason and no token counts.** This happens in non-streamed calls ("before") as well as in
  streamed ones ("after").

**What that suggests:** the generation is being ended by the server early, not running on.
- **This fits Phase 0's 17 cases:** they also came back with no counts, and most of them were short as well.
- **My Phase 1 plan generalised from the longest one** (feature-08 turn 8, 5,404 characters of whitespace). So the
  fix targets a run-on tail that almost never happens.
- **The cause on Ollama's side is not established.** The server log was not captured.

**The repeated-reply effect is real, but not measured by the pre-registered comparison.**
- **Where it shows:** turns that come after earlier invalid replies improve sharply once those replies are cut in
  the history:
  - delete-05 turn 27: 9 → 0;
  - feature-08 turns 19–27: 56 → 2 of 90, on unfaithful prompts, so descriptive only.
- **All 17 turns** (not pre-registered): 99 of 170 (58%) → 37 of 170 (22%).
- **What it shows:** the history cut stops the copying (feature-08 repeated the same broken reply 11 times in
  Phase 0). The first broken reply is not prevented.

### In live runs (part B)

- **1 parse error in 643 turns (0.16%),** against Phase 0's 1.80%.
- **Not a like-for-like comparison:** Phase 0's errors were concentrated in delete-05 and feature-08, which are not
  part B's tasks.

## Part B: seed noise on the Phase 1 harness

**Per task and arm, across seeds 42, 43, 44:**

| Task | Arm | Solves | Continuous score (mean, SD) | Per seed | Endings |
|---|---|---|---|---|---|
| delete-06 | PRISM | 1/3 | 0.989, 0.008 | 0.98, 0.98, 1 | cap ×3 |
| delete-06 | BASELINE | 1/3 | 0.989, 0.008 | 0.98, 0.98, 1 | cap ×2, done |
| delete-09 | PRISM | 0/3 | 0.603, 0.426 | 0.90, 0.90, 0 | cap ×3 |
| delete-09 | BASELINE | 2/3 | 0.968, 0.045 | 0.90, 1, 1 | cap ×3 |
| feature-05 | PRISM | **3/3** | 1.000, 0 | 1, 1, 1 | done ×3 |
| feature-05 | BASELINE | 2/3 | 0.667, 0.471 | 1, 0, 1 | done ×2, cap |
| feature-09 | PRISM | 0/3 | 0, 0 | 0, 0, 0 | cap ×3 |
| feature-09 | BASELINE | 0/3 | 0, 0 | 0, 0, 0 | cap ×3 |

### H5 (pre-registered readings): supported

- **Condition 1, seed-to-seed variation:** an arm gets 1 or 2 solves out of 3 in **3 of 4 tasks** (delete-06 both
  arms, delete-09 BASELINE, feature-05 BASELINE). That meets the bar of 3.
- **Condition 2, Phase 0's winner:** Phase 0's winning arm does not win in **3 of 4 tasks**:
  - delete-06: BASELINE won Phase 0; now a 1–1 tie;
  - feature-05: BASELINE won Phase 0; now PRISM 3–2;
  - feature-09: PRISM won Phase 0; now 0–0.
  - Only delete-09 holds (BASELINE 2–0).
- **Both conditions hold:** the Phase 0 4-against-6 gap was mostly noise.
- **No PRISM-versus-BASELINE claim** is made from 4 tasks, as pre-registered.
- **On the new metric, Phase 0's 40 runs give PRISM 0.535 and BASELINE 0.528.**

### The Phase 1 harness in practice (all 24 runs)

- **Token-budget endings: 0 of 24.**
  - These four tasks had 3 budget endings in their 8 Phase 0 runs.
  - The largest prompt was 11,430 tokens, and the window rule stubbed nothing in any run.
  - The turn cap is now the binding limit: 19 of 24 runs end there.
- **Tokens per run:** 68K–181K, against 141K–286K in Phase 0 for the same tasks.
- **Compaction was active in every run** (superseded, stale, summarised).
- **Possible side-effect, not established:**
  - **Identical re-reads per turn:** 0.042 in Phase 0 against 0.073 here.
  - **Failed edits per turn:** 0.005 against 0.031.
  - **Repeated failed edits:** 0 against 3.
  - **Where the failures are:** 16 of the 20 failed edits are "text not found", and 12 of those are in delete-09.
    That is consistent with the model editing from text that compaction had removed.
  - **The caveat:** Phase 0's comparison is 8 runs, and delete-09 is the task with the most test-file edits.
  - **The lever, if it holds:** keep more recent outputs (`KEEP_RECENT`), or never summarise the latest read of a file
    the agent is editing.

### Noise, for sizing the re-baseline

**The spread:**
- **The pooled within-task, within-arm SD of the continuous score is 0.276** per run (ddof = 1, 8 task-arm cells).
- **The score is nearly two-valued on these tasks:**
  - a delete run scores 0 (target left) or 0.9–1;
  - a feature run with 1–3 target tests scores 0 or 1.
- **Partial credit appears** only in tasks with many target tests (Phase 0: feature-06 0.37, feature-03 0.14) or
  heavy breakage (Phase 0 delete scores 0.5–0.6).
- **The estimate is rough:** it comes from 4 tasks chosen because they were unstable.

**Sizing, using that SD:**
- **The test:** paired by task. Each task's arm difference is averaged over k seeds. The calculation ignores how much
  the true effect itself varies between tasks, so it is a lower bound.
- **The baseline:** Phase 0's mean score is about 0.53. A 10–15% relative effect is d = 0.053–0.08.

| Tasks × seeds × 2 arms | Runs | Detectable difference (α 0.05, power 0.8) | Relative to 0.53 |
|---|---|---|---|
| 20 × 3 | 120 | 0.14 | ~27% |
| 20 × 5 | 200 | 0.11 | ~21% |
| 20 × 10 | 400 | 0.077 | ~15% |
| 62 × 3 | 372 | 0.08 | 15% (needs ~40 more tasks) |
| 142 × 3 | 852 | 0.053 | 10% |

- **At 6 minutes a run, 400 runs is about 40 GPU-hours,** against 1,700+ runs per arm for solve rate.
- **The cheapest design that reaches 15% is 20 tasks × 10 seeds.** Its conclusion is about these 20 tasks, which
  include the known broken ones.
- **Reaching 10% needs task mining.**
