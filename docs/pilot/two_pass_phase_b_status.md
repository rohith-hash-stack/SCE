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

| Engine | Cells (n) | Mean TSR | Mean CPI_answer | FPR_GT (vs annotated set) | FPR_Oracle (vs Oracle's own pack) | Token Footprint | Source |
|---|---|---|---|---|---|---|---|
| baseline_bfs_bidirectional | 300 | 0.880 | 0.833 | 0.679 | 0.681 | 6,943.5 | [JSON-verified] |
| baseline_bfs_forward | 300 | 0.913 | 0.967 | 0.526 | 0.527 | 5,561.7 | [JSON-verified] |
| baseline_rag | 300 | 0.571 | 0.017 | 0.962 | 0.964 | 16,287.7 | [JSON-verified] |
| oracle (reference ceiling, not a competitor) | 300 | 0.961 | 0.983 | 0.000 | 0.000 | 4,449.6 | [JSON-verified] |
| prism_v11 (single-pass) | 300 | 0.948 | 0.933 | 0.616 | 0.619 | 7,910.1 | [JSON-verified] |
| prism_two_pass, unpatched (pre-fix, 4 seeds, historical) | 240 | 0.888 | 0.888 | — | — | — | [JSON-verified] (`pilot-4-progress`) |
| **prism_two_pass, patched (5 seeds, final)** | 300 | **0.957** | **0.957** | **0.193** | **0.247** | **5,776.8** | **[JSON-verified]** (`pilot-4-patched-progress` @ `594eec9`) |

`fpr_gt` for the single-pass engines is **not** in `checkpoint_single_pass.
json` (that file only carries `prompt_tokens`, `completion_tokens`,
`cpi_strict`, `cpi_fractional`, `score`, `model`, `raw_response`,
`selected_symbols`) - an earlier version of this document wrongly read that
checkpoint's absence of the field as "never recorded" and reported it as a
structural gap. It's real, wrong, and now corrected: `runner.py`'s
`compute_diagnostics` (line 328) computes `fpr_gt` **and** `fpr_oracle` for
every engine, every (task, budget) cell, and both live in the separate
report file `reports/pilot-4/eval_results_v11.json` (300 records: 20 tasks
x 5 engines x 3 budgets, `tsr_scores` a 5-element list per record - one per
seed; `diagnostics.fpr_gt`/`fpr_oracle` a single retrieval-only value per
record, since retrieval doesn't depend on seed). Single-pass values above
are the mean of those fields across all 60 (task, budget) points per
engine, pulled and verified directly from that file. Oracle's `0.000` is
real, not a placeholder - its selection is built from the annotated ground
truth, so it can't diverge from it by construction.

`fpr_oracle` (divergence from Oracle's own per-budget package, rather than
the raw annotated set) is the metric `runner.py`'s own docstring calls "the"
FPR, precisely because pulling in real, relevant context beyond the narrow
annotated set isn't a false positive against a domain expert's own answer.
`run_two_pass_benchmark.py` never computed it for two-pass at all (only
`fpr_gt`) - closed by an independent, full 300-cell recompute against the
real pinned Django checkout (`PrismEngine.retrieve_requested`, fed each
cell's actual parsed Turn-1 request), validated against the checkpoint
before trusting it: **0/300 cells' recomputed `fpr_gt` differed from the
recorded value.** Two-pass's `fpr_oracle` (0.247) is a bit higher than its
own `fpr_gt` (0.193) - Oracle's per-budget package is often a proper subset
of the full annotated set (budget-capped, and itself has `fpr_gt=0.000`
without covering every annotated symbol), so comparing against it counts a
few more things as "extra" than comparing against the full set does. This
doesn't change the comparison to other engines: two-pass's `fpr_oracle` is
still roughly **2.5x lower than single-pass Prism's (0.619)** and 2.2-3.9x
lower than every baseline, both statistically confirmed via the same
paired-bootstrap methodology the formal gate uses (ΔFPR_oracle vs baseline
-43.43pp, 95% CI [-46.04, -40.85]; vs single-pass -37.29pp, 95% CI
[-40.34, -34.25], both excluding zero).

**This closes what was previously listed as gap 5 below**: context noise
*can* be compared side by side after all, by both definitions. See "Why
two-pass's context noise isn't zero" below for the root cause of the 0.193/
0.247 figures.

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

## Why two-pass's context noise isn't zero

Investigated directly, not assumed. First pass at this question used the
wrong JSON field (`symbols` instead of the real `requested_symbols`) and
wrongly concluded the model's own Turn-1 selection had zero noise - caught
by checking the raw `turn1_response` text, corrected below.

**Aggregate (all 300 cells)**: comparing Turn 1's own raw requested symbols
(before any hydration) against ground truth gives a mean proxy noise of
0.190 - almost identical to the final recorded `fpr_gt` of 0.193. The
deterministic post-hydration fixups (direct-callee auto-inclusion, class-
promotion) add only ~0.003, about 1.5% of the total. **The noise is
overwhelmingly the model's own Turn-1 choice, not the hydration
machinery.**

**Confirmed with real, named symbols** via the same full-corpus recompute
used for `fpr_oracle` above, tracing every "extra" (non-ground-truth)
symbol in 4 real cells to its exact source:

| Task | Extra symbols | From the model's own request | From direct-callee auto-inclusion | Residual (class-promotion, itself traced to a model pick) |
|---|---|---|---|---|
| t02_003_form_clean_validation | 9 | 6 | 0 | 3 |
| t02_012_wsgi_entrypoint_dispatch | 4 | 3 | 0 | 1 |
| t02_013_common_middleware_slash_redirect | 4 | 2 | 1 | 1 |
| t02_015_admin_each_context | 4 | 3 | 0 | 1 |

21 total extra symbols across these 4 cells; ~90% trace directly to the
model's own pick (either requested outright, or its containing class got
auto-promoted after the model requested one of its methods). Only 1/21
came from the Phase B direct-callee auto-inclusion fix.

**What's actually being over-included** (most frequent "extra" symbols,
300-cell aggregate) is real, causally-adjacent Django code, not
hallucination - **0/300 cells had a hallucinated symbol survive into the
final selection**:
- Form-validation siblings (`ComboField.clean`, `FileField.clean`,
  `Field.clean`, `BaseForm.has_changed`, `BaseForm.add_error`,
  `ErrorDict`) - genuinely related validation methods, just not the
  specific 3-6 symbols annotators picked for that narrow task.
- URL-resolution helpers (`django.urls.base.resolve`/`reverse`/
  `is_valid_path`/`set_urlconf`, `_get_cached_resolver`) - used pervasively
  across Django's request pipeline, reasonably included whenever a seed's
  chain touches URL resolution even if that specific task's ground truth
  didn't name them.
- `django.utils.module_loading.import_string` (29 occurrences, the single
  most over-requested symbol) - Django's generic dynamic-import helper for
  pluggable backends, shows up whenever a seed's chain loads a configurable
  class.

**Reading on the 0.193/0.247 figures**: `fpr_gt`/`fpr_oracle` measure
divergence from a narrow, hand-annotated 3-6-symbol set (or Oracle's own
budget-capped subset of it) - neither distinguishes "irrelevant" from "real
Django code the model judged worth including that annotators didn't happen
to list." This is a materially less alarming story than "19-25% garbage":
it's the model exercising real judgment about causally-adjacent code, most
of the time reasonably, occasionally beyond what a narrow annotation
happened to cover.

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
5. ~~`fpr_gt` is structurally absent from the single-pass checkpoint
   schema~~ — **corrected, not a real gap**: it's absent from the
   checkpoint file specifically, but present and verified in
   `reports/pilot-4/eval_results_v11.json`. Context noise is compared
   side by side in the results table above.

Gap 3 needs a second corpus/model run, cost and time permitting. Gap 4 is
inherent to this evaluation's current scope, not something a rerun alone
fixes - more seeds on the same corpus/model was never going to move a
result that's already stable at 5/5 seeds.

## Adoption status

Two-pass is the approach being carried forward for future pilot evaluation
work on this project, with single-pass kept as the comparison baseline in
the harness - this is an evaluation-protocol statement, not a code change.
Nothing in `prism.cli`, the MCP server, or `benchmarks/runner.py` has been
modified. Single-pass is not deprecated and has no removal planned.
