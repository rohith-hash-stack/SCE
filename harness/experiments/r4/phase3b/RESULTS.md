# Phase 3b: results (analysed 2026-11-28)

**Source:**
- the pre-registered analysis (`AMENDMENTS.md`, "Phase 3b", sections 4–5), run once on all 80 runs;
- the code `phase3b_analysis.py`, unchanged; full output in `reports/r4/phase3b_analysis.json`.
- **Independent verification:** `EVIDENCE_AUDIT.md`. It re-derived all 80 runs and the primary test, and found three
  passages here that needed correcting. They are corrected below, and listed in section 5.

**The runs:**
- 80 of 80, all at code `f77411d4`, in 6 Kaggle sessions;
- every environment check passed (one config sha256, one set of model layers, Ollama 0.34.0, 2 × T4);
- every health check passed: 0 infrastructure deaths, parse errors 0–0.4%;
- no run exited with an error, and no run was recorded twice.

**How the analysis was run:**
- The first invocation was given a glob of the shard folders, not of their `.jsonl` files, and matched no runs.
- It was re-run with `'reports/r4/phase3b_shard*/*.jsonl'`, the form its docstring and validation use. Nothing else
  changed.

## 1. The primary test (pre-registered)

p3-hidden-15, arm A (PRISM_GW) against arm E (PRISM_GW_E), run-level failure, two-sided Fisher exact test, α = 0.05.

| | Failed runs (deduplicated) | Failed runs (all) |
|---|---|---|
| A (PRISM_GW) | 14 / 15 | 14 / 15 |
| E (PRISM_GW_E) | 9 / 15 | 9 / 15 |
| **p (two-sided Fisher)** | **0.080** | 0.080 |

- No transcripts are identical within A or within E, so deduplication changes nothing.
- **Result: not significant at α = 0.05.** The pre-registered test does not show that keeping PRISM's indentation
  reduces failed, unrecovered edits.
- As pre-registered, this is not evidence that E has no effect. The difference is 5 runs of 15, in E's favour, with
  low power (section 5 of the amendment).
- The p-value was checked independently: 0.0801.

## 2. Descriptive results (no test; p-values below are labelled descriptive)

**p3-hidden-15:**

| Arm | Failed runs | Resolved | Triggers / recovered | Triggers per 100 edits | Failed edits per run | Loop refusals per run | Mean turns | Mean tokens |
|---|---|---|---|---|---|---|---|---|
| A: PRISM_GW | 14/15 | 2/15 | 50 / 34 | 6.8 | 36.3 | 30.1 | 57.5 | 621k |
| C: PRISM_GW_C | 5/15 | 11/15 | 48 / 43 | 11.1 | 14.1 | 9.1 | 34.4 | 321k |
| E: PRISM_GW_E | 9/15 | 6/15 | 12 / 3 | 2.1 | 25.3 | 22.5 | 44.6 | 463k |
| BASELINE_LSP | 0/15 | 12/15 | 7 / 7 | 1.4 | 12.4 | 5.7 | 50.5 | 242k |

- **C against A** (descriptive): 5/15 against 14/15 failed, Fisher p = 0.0017 (descriptive only, by the amendment).
  - 42 of C's 43 recoveries were gateway corrections: the next edit sent the note's exact text.
  - Of C's 211 notes, 45 were followed exactly and 4 were answered with different text at that place. **162 notes
    were ignored; the model submitted different text, mostly repeating its own previously refused edit.** These are
    the gateway's own `response` counts.
    - Receiving a note is not the same as following it. Most of the 162 were repeats of one note during a loop the
      model did not leave.
- **E against C** (descriptive): 9/15 against 5/15, p = 0.27.
- **C's transcripts:** 13 of 15 are distinct; two pairs of C runs are identical.
- **BASELINE_LSP:** 0 failed runs, and it resolved more p3-hidden-15 runs (12/15) than any gateway arm, though C
  resolved 11.
- **The gateway arms against the baseline** (descriptive only; no pre-registered test compares them):

  | Arm | p3-hidden-15 | p3-hidden-03 | Combined |
  |---|---|---|---|
  | A | 2/15 | 5/5 | 7/20 |
  | C | 11/15 | 5/5 | 16/20 |
  | E | 6/15 | 5/5 | 11/20 |
  | BASELINE_LSP | 12/15 | 2/5 | 14/20 |

  - **Combined:** A and E were below the baseline; C was above it.
  - **On p3-hidden-15:** the baseline resolved 12 and C 11.
  - **On p3-hidden-03:** every gateway arm resolved 5/5, against the baseline's 2/5.
  - These figures neither confirm nor reverse POC 2's verdict or its other tasks. Phase 3b tested only these two
    tasks.

**p3-hidden-03 (recovery task):**

| Arm | Failed runs | Resolved | Triggers / recovered | Mean turns |
|---|---|---|---|---|
| A | 0/5 | 5/5 | 3 / 3 | 22.2 |
| C | 0/5 | 5/5 | 10 / 10 (all gateway corrections) | 21.2 |
| E | 0/5 | 5/5 | 0 / 0 | 21.0 |
| BASELINE_LSP | 0/5 | 2/5 | 1 / 1 | 48.6 |

On this task, E had no trigger at all: every one of its 85 places was edited without a mismatch.

## 3. Where the failures are (descriptive)

**Every failed run, in every arm, fails at one place: `tests/conftest.py:181`.**
- A: 14 runs, plus 182 in 2 of them;
- C: 5 runs;
- E: 9 runs.
- This is the place where POC 2's PRISM runs got stuck.

| Arm | Runs with a trigger at line 181 | Recovered there |
|---|---|---|
| A | 14 | 0 |
| C | 12 | 7 |
| E | 10 | 1 |
| BASELINE_LSP | 0 | – |

**What happens at line 181** (counted over all attempts; details in `WHITESPACE_FAILURE_INVESTIGATION.md`):
- **The line:** it is `) -> SessionMiddleware[ClientSideSessionBackend]:`, the closing line of a multi-line
  signature. It has **no leading whitespace**: no spaces and no tabs. The file contains no tab at all.
- **The first attempt** carries four leading spaces in 40 of the 45 PRISM-arm runs. Lines 175, 180 and 182 next to it
  have four spaces. In the other 5 runs (all in E), the first attempt had none.
- **Later attempts often switch to a leading tab:**
  - A: 64 of 507 attempts;
  - C: 105 of 183;
  - **E: 304 of 383**.
  - Every attempt that first introduced a tab came after line 181 had been shown in the numbered `N<TAB>text` layout
    of the edit tool's error or of `read_file`/`prism_read`.
- **Not every attempt used four spaces.** Across arms, the leading whitespace of the attempts is:

  | Arm | 4 spaces | Tab | 8 spaces | None |
  |---|---|---|---|---|
  | A | 425 | 64 | 16 | 2 |
  | C | 67 | 105 | 0 | 11 |
  | E | 68 | 304 | 0 | 11 |

- **The refusal:** the edit tool refuses a whitespace-only mismatch, and shows the line numbered, as `181<TAB>) -> …`.
- **The loop:** the model repeats a refused call. The loop breaker refuses the repeats, and the run reaches the turn
  cap.

**What this means for E** (an interpretation, not a pre-registered result):
- E puts back indentation that PRISM's output used to strip.
- Line 181 has no indentation to strip, so PRISM showed it correctly in A and in E alike.
- So the failure at line 181 lies outside E's intended scope, whether the submitted whitespace is four spaces or a
  tab. Neither comes from PRISM's output: the file has no tab, and PRISM showed the line unindented.
- **Elsewhere, E works as intended:**
  - its triggers fall from 50 (A) to 12 on p3-hidden-15, and to 0 on p3-hidden-03;
  - its whitespace mismatches per 100 edits fall from 12.4 to 6.4.
- C gives the exact `old` text, so it reaches line 181, and 7 of its 12 runs recovered there.

## 4. Caveats

- **The primary test is not significant.** Every comparison except A against E is descriptive, by the amendment.
- **One place decides the primary outcome:** every run-level failure on p3-hidden-15 is at line 181. The result says
  more about that one place than about whitespace errors in general.
- **A's failure rate is higher than POC 2's:** 14/15 here, against 2/3 on POC 2's three p3-hidden-15 runs. The seeds
  and the pinned model files differ (`AMENDMENTS`, "Phase 3b note 1").
- **The section 3 interpretation** comes from inspecting transcripts after the outcome was known. It is not a
  pre-registered result.

## 5. Corrections (2026-11-28, after the independent audit)

`EVIDENCE_AUDIT.md` (section 3.3, D1–D3) found three passages that needed correcting. **No number and no test result
changed.** The primary result stands: A failed 14/15, E failed 9/15, two-sided Fisher p = 0.0801, not significant.

1. **The line-181 mechanism (§3):**
   - **Was:** the model sends `old` with four leading spaces.
   - **Now:** the full observed behaviour. Four spaces on the first attempt; later attempts often a tab (E: 304 of
     383); the source line has no leading whitespace.
2. **C's notes (§2):**
   - **Was:** "162 got no response".
   - **Now:** 162 notes were ignored; the model submitted different text, mostly repeating its own previously refused
     edit.
3. **The gateway arms against the baseline (§2):**
   - **Was:** "the gateway arms do worse than the baseline".
   - **Now:** the per-task and combined resolution counts. A and E were below the baseline combined; C was above it;
     on p3-hidden-15 the baseline resolved 12 and C 11; on p3-hidden-03 every gateway arm resolved 5/5, against 2/5.
   - All of these are descriptive.
