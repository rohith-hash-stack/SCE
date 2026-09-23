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
| `checkpoint_single_pass.json` (seeds 42-45, 5 engines) | **Real, durable.** Lives on `pilot-4-progress`. Independently re-verified directly from that JSON (see below) - not log-parsed. |
| `checkpoint_two_pass.json` (patched, seeds 42-45) | **Lost.** The Kaggle cell that produced it never included a push/save step. It is not present in this repository on any branch (every remote branch was checked). The only record of this run is a pasted Kaggle log transcript. |
| `checkpoint_merged.json` (patched) | **Lost**, same reason - it was derived from the file above. |

Because of this, every two-pass number in this document is labeled either
**[JSON-verified]** (computed directly from real, checked-in data) or
**[log-parsed, unverified]** (read out of the pasted log text, never
independently confirmed against the actual checkpoint). Do not treat a
log-parsed number as equivalent evidence to a JSON-verified one - it isn't.

`kaggle/pilot_4_patched_rerun_and_preserve_cell.py` reruns the full
two-pass evaluation (all 5 seeds - the lost 42-45 data has to be redone, not
just seed 46) with the push step this time, and will make the two-pass row
below JSON-verified once it's run.

## Results table

| Engine | Cells (n) | Mean TSR | Mean CPI_answer | Context Noise (FPR_GT) | Token Footprint | Source |
|---|---|---|---|---|---|---|
| baseline_bfs_bidirectional | 240 | 0.880 | 0.833 | not recorded* | 6,944 | [JSON-verified] |
| baseline_bfs_forward | 240 | 0.913 | 0.967 | not recorded* | 5,562 | [JSON-verified] |
| baseline_rag | 240 | 0.571 | 0.017 | not recorded* | 16,288 | [JSON-verified] |
| oracle (reference ceiling, not a competitor) | 240 | 0.961 | 0.983 | not recorded* | 4,450 | [JSON-verified] |
| prism_v11 (single-pass) | 240 | 0.948 | 0.933 | not recorded* | 7,910 | [JSON-verified] |
| prism_two_pass, unpatched (pre-fix) | 240 | 0.888 | 0.888 | — | — | [JSON-verified] (`pilot-4-progress`) |
| **prism_two_pass, patched** | 240 | **0.957** | **0.957** | **0.179** | **~5,733** | **[log-parsed, unverified]** |

\* `fpr_gt` was never written to the single-pass checkpoint schema at all
(confirmed by inspecting the actual field list on a real cell:
`prompt_tokens`, `completion_tokens`, `cpi_strict`, `cpi_fractional`,
`score`, `model`, `raw_response`, `selected_symbols` - no `fpr_gt`). This is
a pre-existing gap in the single-pass harness, not something lost with this
run.

**Seed 5 (46): not run for either engine.** Not included above.

## Verification report

The single-pass row was independently recomputed, fresh, directly from
`checkpoint_single_pass.json` on `pilot-4-progress` (not from any earlier
summary), and matches exactly: n=240, 200/240 perfect, mean TSR 0.9478,
mean token footprint 7,910.1 across `prism_v11`.

The patched two-pass row (0/240 parse failures, 196/240 perfect, mean score
0.957, ~5,733 tokens/cell) could not be put through the same process - there
is no real JSON to recompute from. It is repeated here as-is from the
earlier log parse, explicitly flagged unverified. **No discrepancy has been
found, because no independent check has been possible yet.** That is a gap,
not a confirmation.

## Formal gate status

The project's own pre-registered threshold (ΔTSR and ΔCPI_answer both
≥15 percentage points over `baseline_bfs_bidirectional`, with a bootstrapped
95% CI excluding zero) has **not been cleared** by either the unpatched or
patched two-pass result. The patched run's own gate output (log-parsed,
same caveat as above) showed ΔTSR ≈7.67pp and ΔCPI_answer ≈12.33pp -
real, CI-excludes-zero improvements over the unpatched run's ≈0.83pp /
≈5.50pp, but still short of 15pp. This threshold has not been changed by
this evaluation and should not be treated as relaxed.

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

1. Patched two-pass `checkpoint_two_pass.json` / `checkpoint_merged.json` do
   not exist anywhere durable - rerun and push required
   (`kaggle/pilot_4_patched_rerun_and_preserve_cell.py`).
2. Seed 46 has never been run for either engine.
3. Zero corpora other than Django, zero models other than
   `qwen2.5-coder:14b-instruct-q8_0`, have been tested.
4. The formal 15pp gate threshold has not been cleared.
5. `fpr_gt` is structurally absent from the single-pass checkpoint schema,
   so context-noise can currently only be compared for two-pass in
   isolation, not against single-pass side by side.

## Adoption status

Two-pass is the approach being carried forward for future pilot evaluation
work on this project, with single-pass kept as the comparison baseline in
the harness - this is an evaluation-protocol statement, not a code change.
Nothing in `prism.cli`, the MCP server, or `benchmarks/runner.py` has been
modified. Single-pass is not deprecated and has no removal planned.
