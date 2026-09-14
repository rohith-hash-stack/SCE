# Pilot Pre-Registered STOP Condition

This document is the contract for the pilot. It is written and
committed *before* the pilot runs. After the pilot runs, the numbers
either clear it or they don't — this document does not get retuned,
re-thresholded, or reinterpreted after the fact to fit the result.

## 1. Primary metrics

- **TSR** (Task Success Rate)
- **CPI_strict** (Causal Pipeline Integrity, strict variant)

Both are reported with 95% bootstrap confidence intervals (10,000
resamples).

## 2. Decision thresholds

- **PASS**: ΔTSR ≥ 15 percentage points AND ΔCPI_strict ≥ 15
  percentage points, both CIs excluding zero.
  → Proceed to the full 4-repo sweep (Phase P2).

- **EXPAND**: one or both metrics land in the 5-15 percentage-point
  range.
  → Add 20 more tasks, re-run, re-evaluate against this same rule.

- **STOP**: both metrics < 5 percentage points.
  → Do not build Phase P2.

These three outcomes are exhaustive and mutually exclusive over the
two metrics; there is no fourth reading of the numbers.

## 3. Comparison baseline

- **Primary**: Prism v1.1 vs. BFS-bidirectional.
- **Secondary** (reported, not gating): BM25, BFS-forward,
  PragmaticOracle.

Only the primary comparison feeds the decision thresholds in
Section 2. The secondary engines are reported alongside for context
and are never used to justify a different PASS/EXPAND/STOP outcome
than the primary comparison produces.

## 4. Post-hoc exclusions prohibited

- No task may be dropped from the analysis after the pilot runs,
  except for the pre-declared validity reasons below. There are no
  other grounds for exclusion, regardless of how a task's result
  looks once seen.
  - Seed symbol missing from the pinned commit.
  - Parse failure on the task's seed file.
- No threshold may be adjusted after the pilot runs.
- No LLM may be swapped mid-run.

## 5. LLM pinning

- **Model**: `gpt-4o-2024-11-20` (already pinned in
  `benchmarks/tsr/client.py`).
- **Temperature**: 0.
- **Seeds**: 5 per cell.

## 6. Failure to meet any pre-condition

If the pilot cannot be run as specified — the pinned commit changed,
the LLM is unavailable, the corpus is corrupted, or any other
precondition in this document no longer holds — the pilot is
postponed. This STOP-condition document is not modified to
accommodate the failure. The condition as written is either met or
not; it is not edited to make a compromised run meet it.

## 7. Sign-off

The STOP condition above is pre-registered and will not be adjusted
after the pilot runs.

Signed: ______________________  Date: ______________
