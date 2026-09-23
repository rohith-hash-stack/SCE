# Two-Pass Phase B Patches — Evaluation Status

**Status: strongly promising, not yet final.**

This tracks the evaluation of the Phase B two-pass patches
(`feature/two-pass-phase-b-patches`, commits `7512ab0` fix-turn1-resilience
and `b75d1c5` feat-callee-autoinclusion-and-upstream-admission) against the
pilot-4 grid. It exists to keep one honest answer to "is two-pass ready"
instead of that answer living only in chat history.

## Scope (do not read anything below as broader than this)

- **4 of 5 seeds**: 42, 43, 44, 45. Seed 46 was never run for either engine.
- **One corpus**: Django.
- **One model**: `qwen2.5-coder:14b-instruct-q8_0`, served locally via Ollama.
- **One evaluation type**: T02 debug tasks only (20 tasks, 3 budgets).

Nothing here has been checked against a second corpus, a second model, or a
5th seed. Any claim of generalization beyond this scope is not supported by
this evaluation.

## Artifact status — read this before trusting any two-pass number below

| File | Status |
|---|---|
| `checkpoint_single_pass.json` (seeds 42-45, 5 engines) | **Real, durable.** Lives on `pilot-4-progress`. Independently re-verified directly from that JSON. |
| `checkpoint_two_pass.json` (patched, seeds 42-45) | **Rescued and preserved.** The Kaggle cell that produced it never pushed anything, but the user still had it locally after the session ended. Pulled from that local copy, independently re-verified (structure, cell counts, headline numbers, and a from-scratch merge/gate re-run - see below), then committed to `two-pass-artifact-preservation` (commit `a76f962`, `reports/pilot-4-patched/checkpoint_two_pass.json`). |
| `checkpoint_merged.json` (patched) | **Rescued and preserved**, same branch/commit. Independently regenerated from the file above plus the trusted single-pass checkpoint and confirmed to match the rescued copy with **zero value differences** across all 1,440 cells. |

Every two-pass number in this document is now **[JSON-verified]** - computed
directly from the real, checked-in, independently re-verified data on
`two-pass-artifact-preservation`, not read out of log text.

sha256sum (also recorded in the preservation commit message):
```
checkpoint_two_pass.json  7af08cc623effbaba6bbdda30bd94cdf354a071ef46116c269a0fb692439a4c2
checkpoint_merged.json    f0454b7f92b8396cd9a08af0c1425a1ca1a5a62fa4419bc6396a29196d7ed9af
eval_results.json         7da754a762abb450c3e9973b0ecc7237429ddc6730e339651f17a22a1501933e
```

`kaggle/pilot_4_patched_rerun_and_preserve_cell.py` still exists and is
still the right thing to run for seed 46 / widening the corpus - the
artifact-loss problem it was built to solve is fixed, but the "4 seeds,
one corpus, one model" scope limitation below is not.

## Results table

| Engine | Cells (n) | Mean TSR | Mean CPI_answer | Context Noise (FPR_GT) | Token Footprint | Source |
|---|---|---|---|---|---|---|
| baseline_bfs_bidirectional | 240 | 0.880 | 0.833 | not recorded* | 6,944 | [JSON-verified] |
| baseline_bfs_forward | 240 | 0.913 | 0.967 | not recorded* | 5,562 | [JSON-verified] |
| baseline_rag | 240 | 0.571 | 0.017 | not recorded* | 16,288 | [JSON-verified] |
| oracle (reference ceiling, not a competitor) | 240 | 0.961 | 0.983 | not recorded* | 4,450 | [JSON-verified] |
| prism_v11 (single-pass) | 240 | 0.948 | 0.933 | not recorded* | 7,910 | [JSON-verified] |
| prism_two_pass, unpatched (pre-fix) | 240 | 0.888 | 0.888 | — | — | [JSON-verified] (`pilot-4-progress`) |
| **prism_two_pass, patched** | 240 | **0.957** | **0.957** | **0.179** | **5,733** | **[JSON-verified]** (`two-pass-artifact-preservation`) |

\* `fpr_gt` was never written to the single-pass checkpoint schema at all
(confirmed by inspecting the actual field list on a real cell:
`prompt_tokens`, `completion_tokens`, `cpi_strict`, `cpi_fractional`,
`score`, `model`, `raw_response`, `selected_symbols` - no `fpr_gt`). This is
a pre-existing gap in the single-pass harness, not something lost with this
run.

**Seed 5 (46): not run for either engine.** Not included above.

## Verification report

**Single-pass**: independently recomputed, fresh, directly from
`checkpoint_single_pass.json` on `pilot-4-progress`, and matches exactly:
n=240, 200/240 perfect, mean TSR 0.9478, mean token footprint 7,910.1
across `prism_v11`.

**Two-pass (patched)**: independently recomputed, fresh, directly from the
rescued `checkpoint_two_pass.json` - not from the log, not from trusting
the uploaded file as-is. Checks performed, and results:

- JSON structurally valid, both files.
- 240 cells; seeds 42/43/44/45 at 60 cells each; budgets 2000/4000/8000 at
  80 cells each; 20 distinct tasks; 0 cells missing any required field;
  0 cells with a null `tsr`; **0 cells with `turn1_parsed_ok == False`**.
- Hand-counted headline numbers, computed fresh from the raw cells:
  196/240 perfect (`tsr == 1.0`), mean `tsr` = 0.9567, mean
  `cpi_end_to_end` = 0.9567, mean `fpr_gt` = 0.1794, mean token footprint
  (`turn1_prompt_tokens + turn2_prompt_tokens + completion_tokens`) = 5732.8.
  **Exact match** to the previously-reported log-parsed figures - no
  discrepancy found.
- `checkpoint_merged.json` independently regenerated (via
  `scripts/merge_pilot_checkpoints.py`, from this file plus the trusted
  single-pass checkpoint) and diffed value-by-value against the rescued
  merged file: **0 differences across all 1,440 cells.**
- Both gate comparisons re-run independently via `scripts/apply_gate.py`:
  same point estimates and same decisions (EXPAND vs baseline, STOP vs
  single-pass) as the rescued `gate_vs_*.md` reports. Bootstrap CI bounds
  differ by roughly 0.02-0.2 percentage points between the two runs
  despite the script's fixed default seed - a small, already-observed,
  non-blocking non-determinism in the CI computation; it does not change
  any point estimate or decision.

**Conclusion: fully verified, not just re-uploaded.** The numbers reported
throughout this evaluation, before and after this verification pass, agree.

## Formal gate status

The project's own pre-registered threshold (ΔTSR and ΔCPI_answer both
≥15 percentage points over `baseline_bfs_bidirectional`, with a bootstrapped
95% CI excluding zero) has **not been cleared** by either the unpatched or
patched two-pass result. The patched run's own gate output (independently
re-run against the verified JSON, not log-parsed) shows ΔTSR ≈7.67pp
(95% CI excludes zero) and ΔCPI_answer ≈12.33pp (95% CI excludes zero) -
real, confirmed improvements over the unpatched run's ≈0.83pp / ≈5.50pp,
but still short of 15pp. This threshold has not been changed by this
evaluation and should not be treated as relaxed.

Against single-pass (`prism_v11`) specifically: ΔTSR ≈0.89pp and
ΔCPI_answer ≈2.33pp, both with CIs crossing zero - two-pass no longer
trails single-pass (it did, clearly, before the patch), but it hasn't
established a statistically confirmed lead over it either. "At least as
good as single-pass, plausibly slightly ahead" is the honest read, not
"beats single-pass."

## Pre-registered generalization criteria

Before this can be called a general result rather than a Django-and-one-model
result, on a second corpus and/or model:

- Two-pass's mean TSR and mean CPI_answer should both remain at or above
  single-pass's on the same cells (not necessarily better - not regressing
  is the bar for "still promising," beating single-pass again is the bar
  for "confirmed").
- The `t02_002`-style repetition-loop failure (0 parse failures on the
  patched Django run) should not reappear at a materially higher rate than
  observed here (0/240).
- Token footprint should remain lower than single-pass's on the same cells,
  not just on Django.

None of these have been checked yet.

## Remaining gaps before this is a real, generalizable win

1. ~~Patched two-pass artifacts do not exist anywhere durable~~ — **fixed**:
   rescued, independently re-verified, and preserved on
   `two-pass-artifact-preservation` (commit `a76f962`).
2. Seed 46 has never been run for either engine.
3. Zero corpora other than Django, zero models other than
   `qwen2.5-coder:14b-instruct-q8_0`, have been tested.
4. The formal 15pp gate threshold has not been cleared vs. baseline, and
   two-pass's edge over single-pass is not yet statistically confirmed
   (CI crosses zero on both ΔTSR and ΔCPI_answer).
5. `fpr_gt` is structurally absent from the single-pass checkpoint schema,
   so context-noise can currently only be compared for two-pass in
   isolation, not against single-pass side by side.

`kaggle/pilot_4_patched_rerun_and_preserve_cell.py` is still the right tool
for gap 2 (it also already includes the push step, so a rerun won't
reproduce gap 1). Gap 3 needs a second corpus/model run, cost and time
permitting. Gaps 4-5 are inherent to this evaluation's current scope, not
things a rerun alone fixes.

## Adoption status

Two-pass is the approach being carried forward for future pilot evaluation
work on this project, with single-pass kept as the comparison baseline in
the harness - this is an evaluation-protocol statement, not a code change.
Nothing in `prism.cli`, the MCP server, or `benchmarks/runner.py` has been
modified. Single-pass is not deprecated and has no removal planned.
