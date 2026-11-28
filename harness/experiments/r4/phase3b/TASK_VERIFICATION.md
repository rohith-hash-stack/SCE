# Phase 3b, step 1: verifying whitespace-sensitive tasks

**Status:** verification only. No harness code changed; no fix built; nothing run. Scripts: `ws_scan.py` and
`ws_kind.py` here, run from the repository root against the stored transcripts in `reports/r4/`.

**What was measured:** every `edit_file` call that the edit tool answered with its whitespace-only mismatch, under
its current message (the one that shows the file's own lines).
- A refusal by the loop breaker of the same call counts as a further failed attempt.
- A **persistent** failure means 3 or more failed attempts at one place in one run.
- The single-line leading-whitespace classification compares the `old` text with the file line shown in the error.
  That text is cut in the stored logs, so the comparison is by prefix.

## Findings

**1. The failure is common, but rarely persistent.**
- **Coverage:** 157 stored runs contain at least one whitespace mismatch.
- **Usual outcome:** the model usually corrects the indentation within a few turns.
- **Persistent cases:** they cluster on a few places, and only one task shows them repeatably:

| Task | Family | Runs | Runs with a mismatch | Runs with a persistent one | Single-line / multi-line attempts | Place |
|---|---|---|---|---|---|---|
| **p3-hidden-15** | rename (hidden) | 9 | 6 | **4** (2 PRISM_LSP, 2 PRISM_GW) | 20 / 6 | `tests/conftest.py:181` |
| p3-hidden-03 | rename (hidden) | 9 | 7 | 0 | 9 / 0 | `src/werkzeug/sansio/request.py:575` |
| p2-rename-04 | rename (derived) | 40 | 10 | 0 | 31 / 0 | `tests/evals/test_otel.py` (several) |
| p2-rename-05 | rename (derived) | 40 | 2 | 2 | 8 / 0 (5 are part of a line, not whitespace) | `instructor/core/__init__.py:13, 33` |
| p2-signature-05 | signature (derived) | 40 | 20 | 1 | 93 / 2 | `tests/e2e/test_routing/test_route_reverse.py` |
| p3-hidden-01 | rename (hidden) | 9 | 1 | 1 | 22 / 0 (not whitespace: `"` against `"""`) | `litestar/openapi/config.py:77` |

**2. The planned edit fix covers fewer than half of the events.**
- **Split:** across all tasks, about 211 mismatches are single-line, leading-whitespace only; about 274 are
  multi-line.
- **What the fix cannot reach:**
  - the multi-line ones;
  - the "part of the line" ones, where the current matcher accepts a substring;
  - p3-hidden-01's case, a quoting difference rather than whitespace.
- **What it does cover:** p3-hidden-15's place (`conftest.py:181`).

**3. The gateway's fix can only matter on rename tasks.** The gateway acts on rename prompts only (other tasks get the
syntax check). So arm C is identical to arm A on any supporting task that is not a rename, such as p2-signature-05.

**4. A likely root cause in PRISM's own output (new; not in the Phase 3b plan).**
- **The difference between arms on p3-hidden-15:**
  - BASELINE_LSP made **0** whitespace mismatches in `tests/conftest.py` in all 3 runs, and solved the task 2 of 3.
  - **All 6** PRISM_LSP and PRISM_GW runs made some.
- **What PRISM shows:** its listings print each reference line with its indentation stripped
  (`harness/experiments/r3/tools.py`, `.strip()` at lines 239, 355, 368, 494, 601; `src/prism/gateway/gateway.py:265`).
  This covers `prism_find_references`, `prism_search`, the historical answers and the gateway worklist.
- **What grep shows:** it prints the raw line, indentation included.
- **What the model then does:** it edits straight from PRISM's listing and guesses the indentation:
  - four spaces before `) -> SessionMiddleware[...]:` (line 181 is at column 0);
  - four spaces before the top-level `def session_middleware(`.
- **Across all stored runs since the current edit message,** whitespace mismatches per 100 edits are:

| Arm | Mismatches per 100 edits |
|---|---|
| BASELINE | 1.2 |
| BASELINE_LSP | 3.9 |
| INJECTED | 3.1 |
| PRISM | 4.3 |
| PRISM_LSP | 2.6 |
| PRISM_GW | 3.1 |
| PRISM_ONLY (no grep) | 7.2 |

- **How strong this is:** the rates fit the explanation, but they are confounded: stages have different task sets,
  and PRISM_ONLY reads files differently. The p3-hidden-15 split is the clean evidence.

## What this means for Phase 3b (for the user's decision)

- **Primary task:** p3-hidden-15 is verified. It has the only repeatable persistent failure: 4 of 6 PRISM runs.
- **Supporting tasks** (rename, so the gateway is active):
  - p3-hidden-03 and p2-rename-04 have frequent single-line mismatches that usually recover. They suit the recovery
    metric, not a test of rescue.
  - p2-rename-05 is weak: 2 of 40 runs, and mixed kinds.
  - No second task with a repeatable persistent failure exists in the stored runs. Getting one means mining new
    renames, filtered for reference places on lines a stripped listing misleads about (dedented continuation lines,
    column-0 definitions inside test modules, tabs).
- **A candidate third fix, the root cause:** keep each line's indentation in PRISM's listings and in the worklist.
  It changes what the model sees rather than rescuing it after an error. If adopted, it is an arm of its own, or it
  replaces C.
- **The primary metric needs a definition that works in every arm.**
  - With the edit fix, a single-line whitespace mismatch no longer fails, so "after the first failed edit" never
    happens.
  - A definition that works in every arm: the trigger is the first mismatched edit at a target place, whether or not
    the tool rescued it; recovery is the intended change being in the file within 5 turns.
- **Power:** at the observed rate, 5 seeds give about 2–3 persistent failures per arm on p3-hidden-15. The arms
  can be described, not tested.
  - **For the edit-tool side, a replay is exact and costs no Kaggle time:** run every recorded mismatched call
    (about 485) through the fixed edit tool, and count which it applies and whether the result is the intended change.
  - **The Kaggle runs are then needed only for the model's side:** whether it copies the exact replacement, and how
    many turns it takes.
