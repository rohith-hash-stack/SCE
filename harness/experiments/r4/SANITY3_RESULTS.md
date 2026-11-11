# R4 sanity run 3: R4-SANITY-3 results (2026-11-12)

**The run:** 40/40 runs, code `27b065d1`, tasks `tasks_v3.json` (`a187478c…`). Results in `reports/r4/sanity3/`.
The same 20 tasks, both arms, seed 42, turn cap 28, with the gate and fixes 1–4.

## Against the expectations recorded before the run (`AMENDMENTS.md`)

| Expectation | Sanity 1 | Sanity 2 | **Sanity 3** | Met? |
|---|---|---|---|---|
| Applied edits ≥ 60% (applied / attempted `edit_file`) | 20% (56/279) | 27% (83/303) | **38% (147/392)** | no (it rose) |
| Loop rate < 30% (runs) | 30% | 72% | **58%** | no |
| Solves above sanity 2 (delete 2/2, feature 0/0, P/B) | 1/2, 0/1 | 2/2, 0/0 | **1/1, 0/0** | no (one fewer) |
| `stuck_done` | — | 14 | **6** (delete 2/2, feature 1/1) | — |
| Gate accepted, scorer unresolved | — | 1 | **4**: 1 genuine, **3 a gate bug** (finding 3) | — |

**The one solved task** is werkzeug delete-08, in both arms. **Prettier delete-10**, solved in both earlier runs,
now fails in both arms (finding 1).

## Zod and pydantic: did the prompt fix or the gate find the target?

**The prompt fix did it.**
- **Zod:** both arms read the file, then edited the target at turns 2–4 (`rdrE…`), before any rejection.
- **Pydantic:**
  - the BASELINE edited at turn 6 (`rxrxgE`), the PRISM arm at turn 4 (`rPgE`);
  - also before any rejection.
- **The gate's status message did none of the finding,** as the expectation wanted.
- **Neither solved:**
  - zod's edit left a syntax error, which the gate accepted (finding 3);
  - pydantic broke 10 tests.

## Findings

1. **A gap in fix 2 caused prettier's regression (my error).**
   - **What happened:** the model's `old` matched once apart from indentation. That goes down an older branch,
     whose message ("matches once apart from whitespace, at line 7. Copy the indentation exactly") does not show
     the lines.
   - **The result:** the model cannot see the indentation it is told to copy. Both arms repeated the same edit 27
     times (`rxxxx…`).
   - **The fix:** show the matching lines, numbered as `read_file` shows them, as the "not found" branch now does.
2. **The duplicate-match message is not actionable for this model.**
   - **What happened:** "occurs 3 times … at lines 557, 651, 786. Include a line before or after it" is answered
     only by changing indentation or a trailing newline. Instructor's BASELINE did this 26 times.
   - **The fix:** show each match with its neighbouring line, ready to copy, so that a unique span can be chosen by
     copying rather than composing.
3. **The gate is lenient on vanished tests (my error).**
   - **The rule:** the regression check counts a start-passing test that is absent from the run as "deleted by the
     model".
   - **Zod:** zod's suite runs every test in two projects, one of which type-checks. The agent's edit left a syntax
     error at `schemas.ts:1226`, which breaks the type-checking project. Its copies of the tests vanished; the plain
     copies passed. The gate accepted, and the scorer found 84 tests missing.
   - **Hono's BASELINE:** the same, with 278 missing.
   - **Reproduced locally** by replaying the run's recorded calls through the real runner (no model).
   - **The fix:** count an absent test as deleted only if its name no longer appears in its test file; otherwise it
     is a regression.
4. **`run_tests` hides vanished tests.** The model saw "49 passed, 0 failed" while half of that file's tests did not
   run. It had no signal that anything was wrong.
5. **Unchanged:**
   - feature runs re-read the same files as earlier outputs drop out of the window (feature-02 cycles through three
     template files; R8);
   - PRISM tool loops on functions that do not exist yet (feature-05, -07; R7, held).

## Reading, by the recorded decision rules

- **The rules:**
  - "applied edits rise but solves don't" means task difficulty;
  - "applied edits don't rise" means diagnosis.
- **The result is between them.** Applied edits rose from 27% to 38%, short of 60%, and solves did not rise.
- **The failures are still mechanical, and three are harness defects found here** (findings 1, 3, 4), so this is
  not yet a task-difficulty result.
- **The thinking analysis' decision tree** (`THINKING_R4_ANALYSIS.md` §4) places "edits < 50%, solves flat" in the
  row that says: do not run R4 on the current model; run a 40-run thinking sanity.

## Options, for decision

| Option | What | Cost | What it tells us |
|---|---|---|---|
| **A. Fix and re-check, current model** | findings 1, 3 and 4 (harness defects), and 2 (copyable duplicates); sanity 4 on the current model | ~1 day to build; ~3.5 h on Kaggle (sanity 2 took 3.4 h for 40 runs) | whether the remaining mechanical failures were the binding constraint |
| **B. A, plus Qwen3-14B with thinking on the same harness** | A, and the same 40 runs on Qwen3-14B with thinking | A + ~13–28 GPU-hours (2–3 sessions) | the model question, on a corrected harness, with both models measured on identical tasks and protocol |
| **C. Thinking only** | skip the current-model re-check | ~13–28 GPU-hours | the model question, but the harness defects would still confound it unless fixed first |

**Recommendation:**
- **B.** Fix the defects first (they are needed under every option), then run both models on the same corrected
  harness.
- **The current-model run is cheap** (~3.5 h) and tells us whether the fixes alone move edits and solves.
- **The thinking run** is the decision tree's prescribed next step for this outcome. Running both on identical tasks
  gives the model decision real evidence instead of the 3-task planning runs.
