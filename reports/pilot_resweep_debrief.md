# Pilot Targeted Re-Sweep Debrief

Source run: `pilot-resweep-20260921T072845Z` (branch `origin/pilot-resweep-20260921T072845Z`,
built on `cd329f7` - the MVP v1 code freeze commit). Executed on a dual Tesla T4
Kaggle environment against `qwen2.5-coder:14b-instruct-q8_0` via a local Ollama
instance (`LLM_BASE_URL=http://localhost:11434/v1`), covering the 13 Django T02
tasks that made up all 31 failing cells in the stale `reports/pilot/failure_analysis.md`
baseline. Budgets 2000/4000/8000, seeds 42/43 (`n=2` per cell), `--pragmatic-oracle`.
Raw artifacts: `reports/pilot-resweep-20260921T072845Z/{eval_results_v11.json,
eval_results_v11.md, failure_analysis.md, checkpoint.json}` on that branch -
not merged into `reports/pilot/` or `reports/pilot-2/`, by design (see the
`--checkpoint` isolation guard confirmed before this run).

This debrief was compiled by reading `eval_results_v11.json`'s 195 records
directly (not just the summary tables), cross-referencing `cpi_strict`
(strict ground-truth retrieval coverage), `selected_symbols`,
`ground_truth_symbols`, and `tsr_scores` per (task, engine, budget) cell.

## 1. Headline TSR

| Engine | Budget 2000 | Budget 4000 | Budget 8000 | Aggregate (n=78) |
|---|---|---|---|---|
| oracle | 0.615 | 0.615 | 0.538 | 0.590 |
| **prism_v11** | 0.231 | 0.308 | 0.385 | **0.308** |
| baseline_bfs_forward | 0.154 | 0.308 | 0.231 | 0.231 |
| baseline_rag | 0.231 | 0.154 | 0.269 | 0.218 |
| baseline_bfs_bidirectional | 0.231 | 0.154 | 0.154 | 0.179 |

Prism beats every baseline in aggregate and is the only engine that improves
monotonically with budget. Oracle itself drops at budget=8000 (0.615 -> 0.538)
- more budget is not strictly better for this local model, for any engine.

Flagged (relative-underperformance) failure-cell count: 31 (stale baseline) ->
21 (fresh sweep, same 13-task x 3-budget grid). **This count measures Prism
scoring meaningfully below oracle/baseline, not raw TSR=0** - four tasks
(003, 005, 014, 016) score TSR=0.000 at every budget without appearing in
this count, because oracle scores 0.000 on them too (no comparative gap to
flag). Absolute TSR=0 counts are used throughout the rest of this document
instead, since that is what "did this task actually turn green" requires.

## 2. Retrieval recall: validated

Fix #1 (builtin-receiver exclusion) and Fix #2 (rendered-metadata metering /
L0->L2 downgrade) were built to close missing-pipeline-symbol gaps. Under
live LLM evaluation, `cpi_strict = 1.0` (100% strict ground-truth coverage)
on **20 of 21** residual failing cells, and on every cell of every one of
the 13 focus tasks except one (`django_t02_017` at budget=2000 only).

`django_t02_005_model_save_signals` specifically: `cpi_strict = 1.0` at
all three budgets - `Model.save`, `Model.save_base`, the `Model` class, and
both `pre_save`/`post_save` signal references are present in every context
package, confirming the earlier offline structural check was not a false
positive. TSR is still 0.000 at every budget, but **oracle also scores
0.000 at every budget** on this task, with zero context noise
(`fpr_gt = 0.0`). The model's own reasoning text is semantically correct
(it describes the real save -> save_base -> pre_save/post_save pipeline
accurately) - it simply doesn't produce a `symbols` array that survives the
exact-match scorer, even given a perfect, noise-free context. Retrieval is
not the bottleneck here; a downstream scoring/prompt-following ceiling is,
and it affects every engine identically, oracle included. Fix #1/Fix #2's
job on this task is complete.

`django_t02_009_queryset_filter_clone`: `cpi_strict = 1.0` at all three
budgets too, but TSR is 0.0 at 2000/4000 and only 1.0 at 8000 - budget-gated
success, not the clean pass hoped for across all budgets.

## 3. Failure mode categorization

**Retrieval/Context Failure** - exactly one cell:
`django_t02_017_redirect_url_safety_check` at budget=2000. `cpi_strict = 0.0`;
two of four ground-truth symbols (`django.utils.http._urlparse`,
`escape_leading_slashes`) were crowded out of the 2000-token budget. The
budget instead admitted `tests.utils_tests.test_http.
URLHasAllowedHostAndSchemeTests.test_allowed_hosts_str` - a **test file**
displacing a real production pipeline symbol. This is a real, narrow,
actionable retrieval-quality bug (see Phase B backlog item below), not a
general recall problem - it recovers to `cpi_strict = 1.0` at budget>=4000
on the same task.

**Scoring/Prompt-Following Failure** - the dominant category. All of
003/005/014/016 (task-inherent: oracle also fails, at every budget, with
zero noise - not fixable from the retrieval side) and the majority of
004/006/009/012/013/017@4000-8000 (Prism-specific: oracle succeeds, Prism
has full `cpi_strict = 1.0` coverage, and still fails). The consistent
correlate across the Prism-specific-but-not-retrieval cells is elevated
`fpr_gt` (context noise: 38%-91% of Prism's selected symbols are
non-ground-truth, vs. oracle's flat 0%). Circumstantial, not proven causal,
but a strong and consistent pattern across six different tasks.

**Dynamic/Edge Disconnection** - zero instances found in this sweep. No
missing symbol traced to an implicit-dispatch or decorator barrier past
static AST traversal; every retrieval gap found was budget-crowding
(017 only). Worth recording as a clean negative result for this task set.

## 4. Task-by-task status (absolute TSR, averaged over budgets/seeds)

| Task | Prism | Oracle | Status |
|---|---|---|---|
| 007_locmem_cache_get_eviction | 1.00 | 1.00 | Green, clean |
| 015_admin_each_context | 1.00 | 0.00 | Green - Prism beats oracle |
| 018_response_init_headers_cookies | 1.00 | 0.67 | Green - Prism beats oracle |
| 012_wsgi_entrypoint_dispatch | 0.67 | 1.00 | Partial - fails only at budget=2000 |
| 009_queryset_filter_clone | 0.33 | 1.00 | Partial - succeeds only at budget=8000 |
| 004_url_resolve_traversal | 0.00 | 1.00 | Prism-specific gap, full retrieval |
| 006_auth_get_user_resolution | 0.00 | 1.00 | Prism-specific gap, full retrieval |
| 013_common_middleware_slash_redirect | 0.00 | 1.00 | Prism-specific gap, full retrieval |
| 017_redirect_url_safety_check | 0.00 | 1.00 | Mixed: retrieval miss @2000, scoring @4000/8000 |
| 003_form_clean_validation | 0.00 | 0.00 | Task-inherent, oracle fails too |
| 014_send_mail_pipeline | 0.00 | 0.00 | Task-inherent, oracle fails too |
| 016_db_cursor_connection | 0.00 | 0.00 | Task-inherent, oracle fails too |
| 005_model_save_signals | 0.00 | 0.00 | Task-inherent, oracle fails too (retrieval fully solved) |

Two counter-intuitive results worth preserving: on 015 and 018, Prism's
noisier package (73-90% and 50-55% non-ground-truth respectively) actually
*outperforms* Oracle's pure, minimal context - a reminder that "less noise
is always better" is the dominant pattern here, not a universal law.

## 5. Follow-on work

See `docs/design_formalism.md` Sec 10 for the three Phase B backlog items
this sweep produced: test-symbol cost demotion, context-noise pruning (the
Subgraph Processor design), and evaluation-contract hardening.
