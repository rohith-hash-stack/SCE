# Investigation: historical standalone PRISM runs vs the 8-arm final sweep

**Question.** Earlier standalone benchmark runs reported higher PRISM
performance than the 8-arm final sweep. What changed?

**Answer.** PRISM's measured performance did not drop. The TSR metric
changed between the two reports.

- **Earlier two-pass TSR** was the *mean partial-credit score* of
  `score_debug_causal` (ordered-subsequence coverage, 0-1 per cell).
- **The sweep's headline `tsr`** is *binary*: 1 only when that same score
  is exactly 1.0.
- **Same metric, same number.** Recomputing the sweep's `prism_full` cells
  with the historical metric gives the historical figures: tRPC/gpt-4o-mini
  **0.923 vs 0.923**, FastAPI/qwen-7B **0.915 vs 0.921**.
- **The baselines were mis-scored.** The historical tables scored the
  single-pass baselines with a different, stricter scorer than PRISM (exact
  ordered-list match, `score_debug`). That is why historical PRISM-vs-baseline
  gaps were much larger than the sweep's.

Investigated on branch `claude/prism-final-empirical-8arms-kp3tpt`
(verified with `git branch --show-current`). `develop` was not checked
out, pulled, or modified.

---

## 1. Historical runs located

| Run | Commit / location | Model, T | Budget(s) | Seeds | Raw cells in git? |
|---|---|---|---|---|---|
| tRPC 3-engine sweep | `d3364c6`, `reports/trpc_benchmark_debrief.md` | gpt-4o-mini, T=0.0 | 4000 | 42-46 | **No** (debrief only; `reports/` is gitignored) |
| Express pilot closure | `a05189f`/`a2b955a`, `reports/express_pilot_audit_gap_closure.md` | gpt-4o-mini, T=0.0 | 2000, 4000 | 5 seeds | **No** (debrief only) |
| FastAPI qwen-7B full | `9d4333f`, `reports/pilot-fastapi-qwen7b-full/` (branch history) | qwen2.5-coder:7b-instruct-q8_0, T=0.0 | 2000/4000/8000 | 42 | Yes |
| FastAPI qwen-7B holdout | `0588972` on `origin/pilot-fastapi-qwen7b-holdout-progress` | qwen2.5-coder:7b-instruct-q8_0, T=0.0 | 2000/4000/8000 | 101-103 | Yes |
| Django qwen-7B pilot / holdout / smoke | `origin/pilot-qwen7b-progress` (`765ee1f`), `origin/pilot-qwen7b-holdout` (`0ee0c2e`), `origin/smoke-qwen7b-progress` | qwen2.5-coder:7b-instruct-q8_0, T=0.0 | 2000/4000/8000 | 43-46, 101-102 | Yes. Re-scored in `auditor_revision_plan.md` §2.4 |
| FastAPI qwen-7B reruns v2 / v3 / smoke | `origin/pilot-fastapi-qwen7b-full-v2-progress`, `-full-v3-progress`, `origin/smoke-fastapi-qwen7b-progress` | qwen2.5-coder:7b-instruct-q8_0, T=0.0 | 2000/4000/8000 | 42 | Yes. Re-scored in `auditor_revision_plan.md` §2.4 |
| Django deepseek / qwen-14B pilots | `origin/pilot-deepseek-progress`, `origin/pilot-4-patched-progress` | deepseek-coder:6.7b / qwen2.5-coder:14b | 2000/4000/8000 | 42-46 | Yes |

All 55 remote branches were fetched and scanned. Qwen-7B runs outside the
final sweep exist only for Django and FastAPI.

T=0.0 comes from `benchmarks/tsr/client.py`: `DEFAULT_TEMPERATURE = 0.0`.
The historical harnesses never overrode it.

---

## 2. Configuration and pipeline diff

| Dimension | Historical standalone runs | 8-arm final sweep | Effect on reported PRISM TSR |
|---|---|---|---|
| **TSR definition (two-pass)** | mean of `score_debug_causal` (partial credit) | `tsr` = 1 iff `score_debug_causal == 1.0`; partial kept as `tsr_partial` | **Primary cause.** Same cells: 0.923 partial vs 0.724 binary (tRPC, gpt) |
| **TSR definition (single-pass baselines/oracle)** | `runner.py --scorer` default **`strict`** = `score_debug` exact list match (Express re-scored post hoc to causal; tRPC not) | same `score_debug_causal` as every other arm | Historical ΔTSR inflated: baselines scored harshly, PRISM leniently |
| Turn-1 system prompt | `TURN1_SYSTEM_PROMPT` | identical text for `ablation_signature_only`; `prism_full` appends an `axes=` row-format sentence | Small (see §4) |
| Turn-1 user prompt, answer system prompt, response contract | `_turn1_user_prompt`, `SYSTEM_PROMPT`, `DEBUG_TASK_RESPONSE_CONTRACT` | **byte-identical** (checked `0588972` vs HEAD) | none |
| Manifest rows | `qualified_name\|role\|kind\|signature\|calls=[...]` | same, plus `\|axes=S:..;F:..;O:..;R:..` in `prism_full` | Small, not significant (§4) |
| Candidate universe (k-hop=3, scope filter, upstream cap) | same code path | same (`manifest_candidate_count` identical per task, e.g. 41 / 7 / 1 for FastAPI 004 / 011 / 020) | none observed |
| Turn-2 rendering | `build_context_package_requested` | same, plus lexical constant bundling (`_append_bundled_constants`, landed after `0588972`), plus harness token ceiling 8,800 | not measurable here; ceiling dropped nodes in ≤4% of prism cells |
| Budget | tRPC 4000; Express 2000/4000; FastAPI 2000-8000 | **8000** | Historically PRISM scored lower at 8000 than at 2000-4000 (FastAPI holdout: 0.921 → 0.873 partial). The BFS baseline gains from more budget (Express gpt: 0.530 → 0.779 partial). |
| Temperature | **0.0** | **0.4** | Converts deterministic passes into per-seed pass rates of 7-9/10 on some tasks (§3) |
| Seeds | 5 (tRPC 42-46) | 10 | none by itself |
| Model strings | gpt-4o-mini (floating alias) / qwen2.5-coder:7b-instruct-q8_0 | gpt-4o-mini-2024-07-18 (pinned) / same qwen tag | not separable |
| Context window (Ollama) | not set; historical prompts reached 16.7K tokens and show no truncation cap | `num_ctx` 24,576; 0 calls at the limit | none |
| System fingerprint | not recorded | recorded (42 distinct values for gpt-4o-mini on tRPC) | not assessable historically |

---

## 3. Evidence

### 3.1 The historical tRPC table is reproduced by the current cells under the historical scorers

Current gpt-4o-mini tRPC cells (2,000; budget 8000; T=0.4), re-scored:

| arm | historical debrief | current, strict `score_debug` | current, `score_debug_causal` mean | current, binary |
|---|---|---|---|---|
| baseline_bfs_bidirectional | 0.584 | **0.600** | 0.887 | 0.644 |
| oracle / pragmatic_oracle | 0.760 | **0.764** | 0.935 | 0.764 |
| prism_two_pass / prism_full | 0.923 | 0.572 | **0.923** | 0.724 |

The historical debrief's single-pass figures match the strict scorer, and
its two-pass figure matches the partial-credit scorer. Its reported
**ΔTSR +33.87pp** mixed the two.

The same comparison with one scorer applied to every arm:

| scorer used for all arms | ΔTSR (prism − baseline) |
|---|---|
| partial credit | +0.036 |
| binary | +0.080 |
| strict | −0.028 |

The Express debrief had already found and corrected this confound for
Express (§5.4: +67.46pp retracted to +43.00pp). The tRPC debrief was
written afterwards without the same re-scoring.

### 3.2 PRISM under the historical metric is unchanged across corpora and models

| model | corpus | prism_full partial (historical metric) | prism_full binary | historical figure |
|---|---|---|---|---|
| gpt-4o-mini | tRPC | **0.923** | 0.724 | 0.923 (budget 4000) |
| gpt-4o-mini | Express (933 cells, 11/20 tasks complete) | 0.905 | 0.686 | 0.955-0.964 (budget 2000/4000) |
| qwen-7B | FastAPI | **0.915** | 0.824 | 0.921 partial / 0.800 binary (holdout, budget 4000); 0.873 / 0.720 (budget 8000) |
| qwen-7B | Express | 0.918 | 0.780 | - |
| qwen-7B | tRPC | 0.911 | 0.692 | - |
| qwen-7B | Django | 0.920 | 0.740 | - |

At the same budget (8000), FastAPI `prism_full` binary TSR is higher now
(0.824) than historically (0.720). Per task, 3/25 tasks are lower now and
5/25 are higher.

Two caveats on the Express gpt row:
- **Incomplete data.** The sweep's Express gpt data stops partway (OpenAI
  credits ran out), so it covers a non-random subset of tasks.
- **Budget.** It ran at budget 8000; the historical run used 2000 and 4000.

### 3.3 Where binary failures come from (gpt-4o-mini `prism_full`)

- **tRPC:**
  - 26 of the binary failures are *all pipeline stages named, but out of
    order*, with full partial credit on coverage.
  - The rest omit one stage. Most of those stages were present in the
    context: pipeline-in-context is 0.948.
- **Express:** failures concentrate in stages that Turn 1 does not request.
  - `lib.response.get` (task 010) was requested in 0/10 seeds, in both
    manifest variants.
  - `enable` (task 001) was requested in 1/10 seeds with axes and 5/10
    without.
  - The historical Express report records the same pattern: "tasks 001,
    002, 003, 009, 010 all root-cause to the model's own Turn 1 symbol
    selection … task 010 never requested `lib.response.get`".

---

## 4. Secondary factors (measured, small)

**4-axis manifest annotations** (`prism_full` vs `ablation_signature_only`,
whose manifest and Turn-1 prompt equal the historical ones):

| model | corpus | full pipeline in context: prism_full / signature_only | Turn-1 recall: prism_full / signature_only | binary TSR: prism_full / signature_only |
|---|---|---|---|---|
| gpt-4o-mini | tRPC | 0.948 / 0.980 | 0.936 / 0.911 | 0.724 / 0.796 |
| gpt-4o-mini | Express | 0.788 / 0.784 | 0.918 / 0.925 | 0.686 / 0.716 |
| qwen-7B | tRPC | 0.984 / 0.984 | 0.901 / 0.917 | 0.692 / 0.692 |
| qwen-7B | Express | 0.880 / 0.935 | 0.936 / 0.964 | 0.780 / 0.815 |

Pooled over all four Qwen corpora, ΔTSR = −0.018 [−0.040, +0.002]. The
direction is consistent but the effect is not significant. It accounts for
a few points on some corpora, not the gap between the historical 0.92 and
the sweep's 0.72.

**Budget 8000.** In the historical FastAPI holdout data, `prism_two_pass`
scores lower at 8000 than at 4000 (0.921 → 0.873 partial, 0.800 → 0.720
binary). The sweep runs every arm at 8000, a point where PRISM was
historically weaker and the BFS baseline historically stronger.

**Temperature 0.4.** At T=0.0 each (task, seed) outcome is close to
deterministic. At 0.4 some tasks move from always-pass to 7-9 passes out of
10 (§5), which lowers binary means without any retrieval change.

---

## 5. Discordant-cell side-by-sides

Historical per-cell data exists in git only for FastAPI. The tRPC and
Express historical checkpoints were never committed, so their historical
cells cannot be recovered (see §6). The three FastAPI tasks whose binary
TSR is lower now than historically, same model, both at budget 8000:

**`fastapi_t02_004_route_registration_pipeline`**, current `prism_full`
binary 7/10 seeds (historical 3/3):

| | historical `0588972`, seed 101, T=0.0 | current, seed 2, T=0.4 |
|---|---|---|
| candidates in manifest | 41 | 41 |
| Turn-1 requested | APIRoute.\_\_init\_\_, get_route_handler, get_dependant, get_flat_dependant, get_body_field, create_body_model, create_model_field | APIRoute.\_\_init\_\_, get_route_handler, Default, \_should_embed_body_fields, get_body_field, get_dependant, get_flat_dependant, get_parameterless_sub_dependant, **get_typed_return_annotation**, create_cloned_field, create_model_field, get_path_param_names, is_body_allowed_for_status_code |
| answer symbols | \_\_init\_\_, get_typed_return_annotation, get_dependant, get_flat_dependant, get_body_field | \_\_init\_\_, get_route_handler, get_dependant, get_flat_dependant, get_body_field |
| scorer | 1.0 | 0.8. Stage `get_typed_return_annotation` is in context but not named in the answer. `_ordered_subsequence_coverage` = 4/5, so the binary check fails. |

**`fastapi_t02_011_get_fields_from_routes_recursion`**, current binary
8/10 (historical 3/3):

| | historical, seed 101 | current, seed 1 |
|---|---|---|
| candidates in manifest | 7 | 7 |
| Turn-1 requested | get_fields_from_routes, get_flat_params, \_get_flat_fields_from_params, get_cached_model_fields | get_fields_from_routes, get_flat_params, get_fields_from_routes (duplicate) |
| answer symbols | get_fields_from_routes, get_flat_params, … | get_fields_from_routes, get_fields_from_routes |
| scorer | 1.0 | 0.5. Stage `get_flat_params` is in context but not named in the answer. |

**`fastapi_t02_020_ujson_response_render`**, current binary 9/10
(historical 3/3):

| | historical, seed 101 | current, seed 3 |
|---|---|---|
| candidates in manifest | 1 | 1 |
| context packed | UJSONResponse.render | UJSONResponse, UJSONResponse.render |
| answer symbols | UJSONResponse.render | UJSONResponse.render, **ujson.dumps** |
| scorer | 1.0 | 0.0. `score_debug_causal` hallucination gate (`illegitimate = [s for s in extracted if s not in pipeline_set and s not in candidates_n]` → `return 0.0`): `dumps` is neither a pipeline stage nor in context. |

In all three:
- **Retrieval is the same:** each task has an identical candidate count.
- **Most seeds still pass.**
- **Each failure is one sampled answer** that drops a stage or adds an
  out-of-context symbol.

This is consistent with T=0.4 sampling variance, not a pipeline change.

**Current tRPC / Express cells** that count as failures under the binary
metric but score partial credit under the historical metric (gpt-4o-mini
`prism_full`):

| task, seed | pipeline | answer | binary / partial / strict | cause |
|---|---|---|---|---|
| trpc_t02_001, 43 | createTRPCInner, getDataTransformer, createBuilder, createMiddlewareFactory, createRouterFactory, createCallerFactory | createTRPCInner, getDataTransformer, createRouterFactory, createCallerFactory, createMiddlewareFactory, createBuilder | 0 / 0.667 / 0 | all stages present, source-order differs (0/10 seeds pass binary) |
| trpc_t02_024, 42 | subscriptionPullFactory, observable, \_pull, getTRPCErrorFromUnknown | subscriptionPullFactory, \_pull, getTRPCErrorFromUnknown, observable | 0 / 0.750 / 0 | order (0/10 binary) |
| trpc_t02_019, 42 | query, createResolver, createNewBuilder, createProcedureCaller | query, createResolver, createNewBuilder | 0 / 0.750 / 0 | stage missing; not in context (pipeline-in-context 0.75) |
| express_t02_001, 48 | init, defaultConfiguration, enable, set | init, defaultConfiguration, set | 0 / 0.750 / 0 | `enable` not requested in Turn 1 → not in context |
| express_t02_010, 42 | redirect, location, get, format | redirect, location, format | 0 / 0.750 / 0 | `get` never requested (0/10), matching the historical report |

---

## 6. Limitations

- **No historical tRPC or Express cells.** Those checkpoints were written
  under the gitignored `reports/` and never force-added, so a true per-cell
  historical-vs-current diff is impossible there. The tRPC conclusion rests
  on the current cells re-scored with each historical scorer (§3.1), which
  reproduces the debrief's three figures to within 0.016.
- **Confounded changes.** Budget, temperature, pinned-vs-floating model
  alias, and constant bundling all changed at once. §4 bounds the effect of
  budget, temperature and axes with the available data. Constant bundling
  was not isolated.
- **Incomplete Express gpt data.** The sweep's gpt-4o-mini Express data
  covers 11 complete tasks plus partial others, so it is not a random
  sample.
- **What "TSR" means here.** "Passed" in all runs is the deterministic
  answer scorer, not an executed test suite.

---

## 7. Reproduction

```bash
git branch --show-current                     # claude/prism-final-empirical-8arms-kp3tpt
# historical FastAPI cells
git show 0588972:reports/pilot-fastapi-qwen7b-holdout/checkpoint_two_pass.json > hist_2p.json
git show 0588972:reports/pilot-fastapi-qwen7b-holdout/checkpoint_single_pass.json > hist_1p.json
# re-score current cells with each scorer (benchmarks.tsr.scorer_debug.score_debug / score_debug_causal)
# over reports/final_sweep/{full,slm_qwen7b}/<repo>/cells.jsonl using each cell's answer_response,
# the task's adjudicated.pipeline_symbols, and the cell's selected_symbols.
```
