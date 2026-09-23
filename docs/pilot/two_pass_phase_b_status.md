# Two-Pass Phase B Patches — Evaluation Status

**Status: strongly promising, not yet final.**

This tracks the evaluation of the Phase B two-pass patches
(`feature/two-pass-phase-b-patches`, commits `7512ab0` fix-turn1-resilience
and `b75d1c5` feat-callee-autoinclusion-and-upstream-admission) against the
pilot-4 grid. It exists to keep one honest answer to "is two-pass ready"
instead of that answer living only in chat history.

## Scope (do not read anything below as broader than this)

- **All 5 seeds: 42, 43, 44, 45, 46. Complete for both engines.**
- **One corpus**: Django.
- **One model**: `qwen2.5-coder:14b-instruct-q8_0`, served locally via Ollama.
- **One evaluation type**: T02 debug tasks only (20 tasks, 3 budgets).

Nothing here has been checked against a second corpus or a second model. Any
claim of generalization beyond this scope is not supported by this
evaluation.

## Artifact status — read this before trusting any two-pass number below

The 5-seed run completed on Kaggle and pushed real data to
**`pilot-4-patched-progress`** (final commit `594eec9`, "5-seed run
complete, merged + gated"). Getting here took two rounds of real bugs in
`kaggle/pilot_4_patched_rerun_and_preserve_cell.py`, both fixed on
`feature/two-pass-phase-b-patches` before this run:

- `d52fac8`: the Kaggle container had no git identity configured, so every
  commit silently failed, and the push itself used an ambiguous refspec
  that fails while `HEAD` is detached - together these meant an earlier
  4h11m run (single-pass seed 46 complete, two-pass never even started)
  produced nothing durable at all. Both fixed.
- `e42bc48`: the restore-on-restart logic only restored two-pass progress
  from `pilot-4-patched-progress`, not single-pass - so a recovered
  single-pass seed-46 checkpoint (rescued from that earlier failed run's
  Kaggle output, independently verified, and pushed by hand) would have
  been silently discarded and recomputed. Fixed, and confirmed live: the
  final run's own `checkpoint_single_pass.json` is **byte-for-byte
  identical** to the hand-recovered one (0/1500 cells differ) - `--resume`
  reused it instead of recomputing, saving the ~2h that already went into
  it.

All three files independently re-verified after the run (structure, cell
counts, a from-scratch merge regeneration, a from-scratch gate re-run - see
Verification report below), not trusted as pushed.

sha256sum (of the files on `pilot-4-patched-progress` @ `594eec9`):
```
checkpoint_single_pass.json  03fe1b6e2ba27c214806641782cff00a1ba2bd5a596d521b0bf6908eb91ec967
checkpoint_two_pass.json     84652610fecb9f17cd0e005e79d4db222626e01d21509af10d5f188f6efbb14b
checkpoint_merged.json       d447402c87e2c574f398631d8275a476dd65998996d34989ad0afd7ab2247e29
```

## Results table (5 seeds, 300 cells per engine, 1800 total)

| Engine | Cells (n) | Mean TSR | Mean CPI_answer | Context Noise (FPR_GT) | Token Footprint | Source |
|---|---|---|---|---|---|---|
| baseline_bfs_bidirectional | 300 | 0.880 | 0.833 | not recorded* | 6,943.5 | [JSON-verified] |
| baseline_bfs_forward | 300 | 0.913 | 0.967 | not recorded* | 5,561.7 | [JSON-verified] |
| baseline_rag | 300 | 0.571 | 0.017 | not recorded* | 16,287.7 | [JSON-verified] |
| oracle (reference ceiling, not a competitor) | 300 | 0.961 | 0.983 | not recorded* | 4,449.6 | [JSON-verified] |
| prism_v11 (single-pass) | 300 | 0.948 | 0.933 | not recorded* | 7,910.1 | [JSON-verified] |
| prism_two_pass, unpatched (pre-fix, 4 seeds, historical) | 240 | 0.888 | 0.888 | — | — | [JSON-verified] (`pilot-4-progress`) |
| **prism_two_pass, patched (5 seeds, final)** | 300 | **0.957** | **0.957** | **0.193** | **5,776.8** | **[JSON-verified]** (`pilot-4-patched-progress` @ `594eec9`) |

\* `fpr_gt` was never written to the single-pass checkpoint schema at all
(confirmed by inspecting the actual field list on a real cell:
`prompt_tokens`, `completion_tokens`, `cpi_strict`, `cpi_fractional`,
`score`, `model`, `raw_response`, `selected_symbols` - no `fpr_gt`). This is
a pre-existing gap in the single-pass harness, not something lost with this
run.

**Seed 46 breakdown**: mean TSR 0.9567, 49/60 perfect - statistically
indistinguishable from seeds 42-45 (each also 0.9567, 49/60 perfect). Not
an outlier.

**The known repetition-loop failure mode** (`django_t02_002`, previously
140x token repetition and a truncated, unparseable Turn-1 response) does
not reappear anywhere in this 5-seed run: `turn1_parsed_ok == True` on all
15 (seed × budget) cells for that task, Turn-1 token usage a stable 2,684
(well under any truncation risk), `tsr == 0.75` on every one. The
`repeat_penalty` fix holds on real, full-scale data, not just the earlier
4-seed sample.

## Verification report

**Single-pass**: independently recomputed, fresh, directly from
`checkpoint_single_pass.json` on `pilot-4-patched-progress`: n=1500 total
(5 seeds x 5 engines x 3 budgets x 20 tasks, i.e. 300 cells per engine),
0 null scores. Confirmed byte-for-byte identical to the hand-recovered
seed-46 checkpoint pushed earlier in this evaluation - proof `--resume`
reused it rather than recomputing.

**Two-pass (patched, 5 seeds)**: independently recomputed, fresh, directly
from the pushed `checkpoint_two_pass.json` - not from the Kaggle log, not
trusted as pushed. Checks performed, and results:

- JSON structurally valid, all three files.
- 300 cells; seeds 42-46 at 60 cells each; budgets 2000/4000/8000 at 100
  cells each; 20 distinct tasks; 0 cells with a null `tsr`; **0 cells with
  `turn1_parsed_ok == False`**; 0 cells with an empty Turn-1 or Turn-2
  response; 0 cells with a missing/zero prompt-token count.
- Hand-counted headline numbers, computed fresh from the raw cells:
  245/300 perfect (`tsr == 1.0`), mean `tsr` = 0.9567, mean
  `cpi_end_to_end` = 0.9567, mean `fpr_gt` = 0.1933, mean token footprint
  (`turn1_prompt_tokens + turn2_prompt_tokens + completion_tokens`) =
  5776.8.
- `checkpoint_merged.json` independently regenerated (via
  `scripts/merge_pilot_checkpoints.py`, from the two raw checkpoints above)
  and diffed value-by-value against the pushed merged file: **0
  differences across all 1,800 cells.**
- Both gate comparisons re-run independently via `scripts/apply_gate.py`:
  same point estimates and same decisions (EXPAND vs baseline, STOP vs
  single-pass) as the pushed `gate_vs_*.md` reports. Bootstrap CI bounds
  differ by roughly 0.01-0.1 percentage points between the two runs
  despite the script's fixed default seed - the same small, already-
  observed, non-blocking non-determinism in the CI computation noted in
  earlier verification passes; it does not change any point estimate or
  decision.

**Conclusion: fully verified, not just pushed.** The numbers in this
document were independently recomputed from the raw checkpoints, not taken
from the Kaggle log or the gate reports as given.

## Formal gate status

The project's own pre-registered threshold (ΔTSR and ΔCPI_answer both
≥15 percentage points over `baseline_bfs_bidirectional`, with a bootstrapped
95% CI excluding zero) has **not been cleared** by the patched two-pass
result, now on the complete 5-seed grid. The gate output (independently
re-run against the verified JSON) shows ΔTSR ≈7.67pp (95% CI excludes zero)
and ΔCPI_answer ≈12.33pp (95% CI excludes zero) - materially unchanged from
the earlier 4-seed read, still short of 15pp. This threshold has not been
changed by this evaluation and should not be treated as relaxed.

Against single-pass (`prism_v11`) specifically: ΔTSR ≈0.89pp and
ΔCPI_answer ≈2.33pp, both with CIs crossing zero - also unchanged from the
4-seed read. Two-pass no longer trails single-pass (it did, clearly, before
the patch), but it hasn't established a statistically confirmed lead over
it either, even with the full 5-seed grid now in. "At least as good as
single-pass, plausibly slightly ahead" is the honest read, not "beats
single-pass."

## Pre-registered generalization criteria

Before this can be called a general result rather than a Django-and-one-model
result, on a second corpus and/or model:

- Two-pass's mean TSR and mean CPI_answer should both remain at or above
  single-pass's on the same cells (not necessarily better - not regressing
  is the bar for "still promising," beating single-pass again is the bar
  for "confirmed").
- The `t02_002`-style repetition-loop failure (0 parse failures on the
  patched Django run, now confirmed across the full 5-seed grid: 0/300)
  should not reappear at a materially higher rate than observed here.
- Token footprint should remain lower than single-pass's on the same cells,
  not just on Django.

None of these have been checked yet - all require a second corpus/model.

## Remaining gaps before this is a real, generalizable win

1. ~~Patched two-pass artifacts do not exist anywhere durable~~ — **fixed**:
   the 5-seed run is now genuinely pushed and durable on
   `pilot-4-patched-progress` (`594eec9`), independently re-verified.
2. ~~Seed 46 has never been run for either engine~~ — **fixed**: all 5
   seeds now complete for both engines, seed 46 not an outlier.
3. Zero corpora other than Django, zero models other than
   `qwen2.5-coder:14b-instruct-q8_0`, have been tested.
4. The formal 15pp gate threshold has not been cleared vs. baseline, and
   two-pass's edge over single-pass is not yet statistically confirmed
   (CI crosses zero on both ΔTSR and ΔCPI_answer) - unchanged by adding
   the 5th seed.
5. `fpr_gt` is structurally absent from the single-pass checkpoint schema,
   so context-noise can currently only be compared for two-pass in
   isolation, not against single-pass side by side.

Gap 3 needs a second corpus/model run, cost and time permitting. Gaps 4-5
are inherent to this evaluation's current scope, not things a rerun alone
fixes - more seeds on the same corpus/model was never going to move a
result that's already stable at 5/5 seeds.

## Adoption status

Two-pass is the approach being carried forward for future pilot evaluation
work on this project, with single-pass kept as the comparison baseline in
the harness - this is an evaluation-protocol statement, not a code change.
Nothing in `prism.cli`, the MCP server, or `benchmarks/runner.py` has been
modified. Single-pass is not deprecated and has no removal planned.
