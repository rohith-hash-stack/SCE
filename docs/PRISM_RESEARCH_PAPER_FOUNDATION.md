# Deterministic AST-Graph Context Packing vs. Semantic RAG for Causal Code Context and Blast Radius

*Research foundation for the PRISM paper. Every number below is taken from a committed report, and every table names
the commit of the file it comes from. Numbers marked **derived** were computed from committed cells by a committed
script and are not stated in the original report.*

## Abstract

An LLM working on a large repository needs the right code in its context: what a failing behavior depends on (to
debug it) and what depends on a symbol (to know what a change breaks). Similarity search finds code that looks like
the question. It does not follow the call graph that links a root cause to its symptom, or a function to its
callers.

PRISM builds a deterministic static graph from Tree-sitter parses: symbols, calls, instantiations, inheritance, and
closures. From an anchor symbol it assembles context in two passes. Turn 1 shows the model a manifest of graph
candidates and the model picks some. Turn 2 hydrates the picks under a token budget.

Across seven benchmark phases (Sep 16 to Oct 20, 2026) the evidence supports a division of labor:

- **Seed discovery: dense retrieval wins.** On 160 frozen questions, `dense_jina_code` reaches top-1 0.738 and MRR
  0.814. PRISM's shipped rule-intent ranking reaches 0.525 and 0.660 (`15baac1`).
- **Causal debugging from an anchor: PRISM wins clearly.** On 90 multi-file debug tasks with a 14B model,
  `prism_full` reaches a causal-trace success rate (TSR) of 0.842, against 0.369 for hybrid RAG: +0.474
  [+0.393, +0.556], 61 task wins to 2 (`15baac1`).
- **Blast radius: PRISM wins, with caveats.** On the held-out Layer 4 tournament, gold comes from runtime traces of
  each repository's own tests. `prism_full` reaches forward F1 0.496 against 0.296 for hybrid RAG (paired Δ +0.200,
  95% CI [+0.026, +0.371]; `5e6bf66`, `7ef8cf9`). This follows index fixes informed by those same traces. Before
  them, prism_full scored 0.321 (`2cb1e62`).
- **The main remaining loss is the model's Turn-1 choice, not the graph and not the budget.** Of 49 gold callers in
  the candidate list, 14.0 per cell are never requested at Turn 1 and 2.8 are cut by the budget (`72b6efd`). An
  earlier ablation found the same: on blast-radius tasks, replacing the model's pick with "take every caller" raised
  TSR in all four corpora (`58d107e`).

## 1. Chronological progression matrix

**Scores are comparable within a row only.** Phases differ in model, task set, metric and scorer, so no score in one
row can be set against a score in another.

| # | date | phase | commit (report) | model | scope / task type | metric | best non-oracle baseline | PRISM | key finding / pivot |
|---|---|---|---|---|---|---|---|---|---|
| 0 | Sep 16–18 | Pilot, v1.1 engines | `2aedc4c` (`reports/pilot/eval_results_v11.md`) | not recorded in the report | Django: 300 debug + 60 blast cells; budgets 2k / 4k / 8k | TSR | `baseline_bfs_bidirectional` 0.375 @ 2k | `prism_v11` 0.458 @ 8k | PRISM gains with budget; BFS does not |
| 1 | Sep 22 | Noise-reduction spike A/B/C | added `267ca2f`, current text `8111666` (`reports/spike_noise_reduction_debrief.md`) | gpt-4o-mini, T = 0 | 3 Django debug tasks × 3 budgets × 2 seeds | TSR, CPI strict, FPR vs. gold | single-zone knapsack: TSR 0.222 | two-pass A v3: TSR 0.500 | **Pivot to two-pass hydration (Approach A)** |
| 2 | Sep 30 | 8-arm final sweep | `c6d7bc4` (`reports/final_sweep/slm_qwen7b/analysis_full.md`); gpt-4o-mini partial runs `a6aa3fd`, `ae75dd0` | Qwen2.5-Coder 7B, T = 0.4 | 90 debug tasks, 4 corpora, 7,200 cells; internal arms only | strict binary TSR (`63d97fe`) | BFS floor 0.626 | `prism_full` 0.759 (+0.133 [+0.069, +0.200]) | Taxonomy anchors matter (+0.142); 4-axis annotations and the purity axis add nothing measurable |
| 3a | Oct 7 | Harness M4, T2 localization | `23dfa3f` (`reports/harness_m4/analysis/t2_matrix.md`) | Qwen2.5-Coder 14B | localization; arms A0–A5 + oracle; 3 seeds | TSR | A2 packing 0.833 (Express), A3 LSP 0.819 (FastAPI) | A5 PRISM first in all 4 corpora (0.650–0.950) | PRISM leads localization |
| 3b | Oct 7 | Harness M4, T5 blast radius | `23dfa3f` (`reports/harness_m4/analysis/t5_matrix.md`) | Qwen2.5-Coder 14B | blast radius; the same arms | fraction of gold recalled | A1 RAG 0.495 (FastAPI); A2 0.262–0.806 | A5 third in all 4 corpora (0.151–0.281) | **Weakness exposed → Design C** (`6180d5b`) |
| 4 | Oct 8 | R0 vs. R1 Turn-1 ablation | `58d107e` (`reports/harness_r0r1/R0_vs_R1_T5.md`) | Qwen2.5-Coder 14B | T5 only, Arm 5 only, 192 cells | as 3b | R1 (model picks): 0.333–0.407 | R0 (rule picks): 0.500–0.666 | **The model's Turn-1 selection adds no value over the rule** |
| 5a | Oct 16 | Two-stage validation, Stage 1 | `15baac1` (`reports/two_stage/RESULTS.md`) | SLM Qwen2.5-Coder 7B (intent) | 160 seed-retrieval questions, 6 repositories | top-1, recall@5, MRR, separation | `dense_jina_code` 0.738 / 0.891 / 0.814 | `prism_rule_intent` 0.525 / 0.831 / 0.660 | **Dense retrieval owns seed discovery** |
| 5b | Oct 16 | Two-stage validation, Stage 2 | `15baac1` (`reports/two_stage/RESULTS.md`) | Qwen2.5-Coder 14B | 90 debug tasks, 4 corpora, 7 arms, 10 seeds | causal-trace TSR | `arm1_hybrid_rag` 0.369 | `prism_full` 0.842 (+0.474, 61–2) | **Largest PRISM win** |
| 6a | Oct 19–20 | Layer 4 forward | `5e6bf66` (`TOURNAMENT.md`), `72b6efd` (funnel), `7ef8cf9` (derived statistics) | Qwen2.5-Coder 14B | 8 blast-radius tasks, runtime-trace gold, 80 cells per arm | P / R / F1 | `arm1_hybrid_rag` F1 0.296 | `prism_full` 0.321 → 0.496 | Bottleneck moves to the Turn-1 pick (14.0 vs. 2.8 lost) |
| 6b | Oct 19–20 | Layer 4 backward | `5e6bf66` | Qwen2.5-Coder 14B | 8 re-implementation tasks; gating tests must pass | pass rate | `arm1_hybrid_rag` 0.013 | `prism_full` 0.033 | Ceiling: the oracle passes 0/80; the family does not separate arms |

**Not in the matrix:** `reports/query_to_evidence/report.md` (`ee211ec`, Oct 9) is a diagnostic only. It traces where a
query's evidence is lost and compares no engines.

**Branch locations:** phases 0–1 are on `develop`. Phases 2–6 are on `eval/held-out-benchmark` and are not on
`develop`.

**Naming collision.** Approach "C" of the Sep 22 spike (spine pruning inside the knapsack, dropped) is unrelated to
**Design C** of Oct 7 (the two-way, budget-aware caller walk, `6180d5b`).

## 2. Stage 1: the seed-retrieval boundary (`15baac1`)

**Setup** (`reports/two_stage/RESULTS.md`, `reports/two_stage/stage1/stage1_results.json`, `15baac1`):

- 160 frozen questions in three sets:

  | set | repositories | questions |
  |---|---|---|
  | set 1 | microblog, realworld | 25 + 25 |
  | set 2 | fastapi_template, styleguide | 30 + 30 |
  | set 3 | express, trpc | 25 + 25 |

- Every engine ranks the same candidate pool.
- `normal_oracle` scores 1.00 by construction and is a sanity row only.
- `prism_shipped_ask` ranks exactly as `prism_rule_intent`; it only adds the payload.
- Latency is not reported.

| engine | top-1 | recall@5 | MRR | separation |
|---|---|---|---|---|
| `plain_bm25` | 0.400 | 0.762 | 0.562 | 0.700 |
| `stemmed_bm25` | 0.494 | 0.825 | 0.635 | 0.762 |
| **`dense_jina_code`** | **0.738** | **0.891** | **0.814** | **0.875** |
| `hybrid_rrf_rerank` | 0.556 | 0.869 | 0.698 | 0.819 |
| `prism_rule_intent` (= `prism_shipped_ask`) | 0.525 | 0.831 | 0.660 | 0.756 |
| `prism_slm_live` | 0.613 | 0.887 | 0.730 | 0.794 |
| `prism_oracle` | 0.581 | 0.850 | 0.699 | 0.787 |
| `normal_oracle` (sanity) | 1.000 | 1.000 | 1.000 | 1.000 |

**Paired contrasts** (questions are the pairing unit; bootstrap stratified by repository; `15baac1`):

| a | b | Δ top-1 | 95% CI | wins–losses | sign-test p |
|---|---|---|---|---|---|
| `dense_jina_code` | `stemmed_bm25` | +0.244 | [+0.156, +0.331] | 52–13 | < 0.001 |
| `prism_rule_intent` | `stemmed_bm25` | +0.031 | [−0.006, +0.075] | 9–4 | 0.267 |
| `prism_slm_live` | `stemmed_bm25` | +0.119 | [+0.069, +0.175] | 21–2 | < 0.001 |
| `prism_slm_live` | `hybrid_rrf_rerank` | +0.056 | [−0.019, +0.131] | 27–18 | 0.233 |

**Intent reading by the SLM** (`15baac1`): 158 of 160 parsed and 124 fully valid. Agreement with the oracle intent
was 41/160 on action and 66/160 on capability.

**Architectural conclusion.** PRISM's natural-language entry point (`prism.ask`) is a lexical ranking:
BM25 plus a rule-based re-rank of intent. It is not significantly better than stemmed BM25, and it is below dense
code embeddings by every metric. PRISM's design contribution is graph-directed expansion **from** an anchor, not
discovery **of** the anchor. A production pipeline should take the seed from dense retrieval, or from the user or
agent, and hand it to PRISM. Stage 2 and Layer 4 both evaluate PRISM from a given anchor.

## 3. Stage 2: multi-file causal debugging

### 3.1 The 90-task debug sweep (`15baac1`)

**Setup** (`reports/two_stage/RESULTS.md`, `15baac1`):

- 90 debug tasks over Django, Express, FastAPI and tRPC.
- Answer model Qwen2.5-Coder 14B (q8_0, context window 24,576 tokens); 10 seeds.
- Metric: causal-trace success rate (`score_debug_causal`): the answer names the root cause along the gold causal
  chain. This is not a test pass rate.
- 95% CIs: bootstrap over tasks, stratified by corpus, seeds averaged within a task.

| arm | TSR | 95% CI | mean context tokens | django | express | fastapi | trpc |
|---|---|---|---|---|---|---|---|
| `arm0_no_retrieval` | 0.201 | [0.126, 0.281] | 284 | 0.17 | 0.16 | 0.26 | 0.20 |
| `repomap_pagerank` | 0.344 | [0.260, 0.436] | 3,937 | 0.15 | 0.27 | 0.36 | 0.55 |
| `arm1_hybrid_rag` | 0.369 | [0.283, 0.452] | 8,053 | 0.29 | 0.22 | 0.63 | 0.29 |
| `prism_ask` | 0.374 | [0.289, 0.460] | 8,004 | 0.24 | 0.25 | 0.64 | 0.32 |
| **`prism_full`** | **0.842** | **[0.774, 0.902]** | **3,584** | 0.78 | 0.93 | 0.82 | 0.85 |
| `normal_oracle` | 0.779 | [0.709, 0.850] | 1,807 | 0.89 | 0.47 | 0.85 | 0.86 |
| `prism_oracle` | 0.773 | [0.701, 0.843] | 1,807 | 0.86 | 0.47 | 0.86 | 0.86 |

| a | b | Δ TSR | 95% CI | task wins–losses | sign-test p |
|---|---|---|---|---|---|
| `prism_full` | `arm1_hybrid_rag` | +0.474 | [+0.393, +0.556] | 61–2 | < 0.001 |
| `prism_full` | `repomap_pagerank` | +0.498 | [+0.412, +0.583] | 57–1 | < 0.001 |
| `prism_ask` | `arm1_hybrid_rag` | +0.006 | [−0.050, +0.061] | 17–11 | 0.345 |
| `arm1_hybrid_rag` | `arm0_no_retrieval` | +0.167 | [+0.093, +0.240] | 29–9 | 0.002 |

**Reading the result:**
- `prism_full` uses less than half of hybrid RAG's context (3,584 vs. 8,053 tokens) and more than doubles its TSR.
- It scores above both oracle arms overall. The oracles' Express score is 0.47, and that column accounts for the
  difference.
- `prism_ask` uses PRISM's lexical entry point without graph expansion, and is level with hybrid RAG. The gain
  comes from graph expansion from the anchor, not from the PRISM name.

**Earlier debug evidence on the same task family** (Sep 30, `c6d7bc4`, Qwen 7B, 7,200 cells, internal arms only):

| contrast | Δ TSR | 95% CI |
|---|---|---|
| `prism_full` (0.759) vs. bidirectional-BFS floor (0.626) | +0.133 | [+0.069, +0.200] |
| taxonomy anchors vs. lexical anchors | +0.142 | [+0.078, +0.213] |
| 4-axis annotations vs. signatures only | −0.018 | [−0.040, +0.002] |
| purity axis | −0.000 | – |

The axis annotations in the manifest therefore have no measurable effect on this task family.

### 3.2 Why two-pass hydration (spike, added `267ca2f`, current text `8111666`)

**Setup:** gpt-4o-mini at T = 0; 3 Django debug tasks × 3 budgets (2k / 4k / 8k) × 2 seeds = 18 cells per approach;
about $0.54 in total. The spike branch was never merged into production.

**Goal:** reduce false positives against the gold set (FPR) without lowering strict coverage of the causal pipeline
(CPI strict).

| approach | TSR | CPI strict | FPR (gold) | mean tokens |
|---|---|---|---|---|
| baseline (single-zone knapsack, production) | 0.222 | 0.889 | 0.778 | 7,597 |
| B: two-zone rendering (frontier index) | 0.222 | 0.889 | 0.778 | 8,275 |
| C: spine pruning inside the knapsack | 0.111 | 0.778 | 0.739 | – |
| A v1: two-pass, name-only manifest | 0.333 | 0.333 | 0.000 | 8,379 |
| A v2: two-pass, signatures + resolved calls | 0.389 | 0.833 | 0.133 | 47,060 |
| A v3: two-pass, hop 3 + scope-filtered manifest | **0.500** | 0.667 | **0.000** | **4,167** |

**Why each approach was kept or dropped** (from the debrief):

- **B: no effect, by construction.** It renders more of the graph without changing which nodes are admitted, so it
  cannot move selection metrics. CPI and FPR are byte-identical to the baseline in all 18 paired cells, and prompt
  overhead rises by about 9%.
- **C: dropped.** Ordering admission purely by topological distance has no way to judge relevance. At
  `django_t02_017`, an unrelated i18n branch used up the budget before `_urlparse` was reached. Fixing that would
  mean re-deriving the knapsack's own density defenses inside a new tier.
- **A: adopted.**
  - The model sees a structural manifest and requests the nodes it needs.
  - v2 showed that signatures and resolved call targets close most of v1's coverage gap.
  - v3 cut Turn 1 from about 44K to about 4.2K tokens, with the best TSR (0.500) and FPR 0.000.
  - No variant wins on every metric: v2 has the best CPI strict (0.833).
- This is the origin of `prism_full`'s Turn 1 / Turn 2 protocol.

## 4. Layer 4 tournament: forward blast radius and backward ceilings

**Design** (`harness/experiments/layer4/LAYER4.md`; results `5e6bf66`):

- 16 held-out tasks: 2 per corpus per family over Django, FastAPI, Express and tRPC.
- 7 arms × 10 seeds = 1,120 cells. Model Qwen2.5-Coder 14B (q8_0), context window 24,576 tokens.
- **Gold:** traced at runtime from each repository's own tests, not authored by hand. Forward gold is the focal
  function's callers within the task's maximum depth (at most 15).
- **Backward:** the focal body is masked and the answer is spliced back in. It passes when the gating tests are green.
- **Leak gating:** 20 cells were held back as leaks and never sent to the model. All are prism_full backward cells,
  10 each on `l4_bwd_django_01` and `l4_bwd_fastapi_02`.
- **Contamination check:** no task was flagged; `arm0_no_retrieval` stayed under the 0.5 threshold on every task.
- **Declared amendment:** the run resumed across four declared code changes, recorded in the manifest. The last is
  "forward context v2 (prism_full)": only prism_full's 80 forward cells were rerun, and the other 1,040 were kept.

### Table 1: forward blast radius (n = 80 cells per arm)

Metrics are means of per-cell scores. F1 is the mean of each cell's F1, not the harmonic mean of mean precision and
mean recall.

| arm | precision | recall | F1 | context tokens | source |
|---|---|---|---|---|---|
| `arm0_no_retrieval` | 0.052 | 0.133 | 0.071 | 17 | `5e6bf66` |
| `repomap_pagerank` | 0.099 | 0.135 | 0.096 | 3,580 | `5e6bf66` |
| `arm1_hybrid_rag` | 0.359 | 0.296 | 0.296 | 6,752 | `5e6bf66` |
| `prism_ask` | 0.432 | 0.317 | 0.329 | 7,746 | `5e6bf66` |
| `prism_full`, before the fixes | 0.540 | 0.259 | 0.321 | 4,973 | `2cb1e62` |
| **`prism_full`, after the fixes** | **0.595** | **0.462** | **0.496** | 7,149 | `5e6bf66` |
| `normal_oracle` | 0.918 | 0.724 | 0.783 | 5,479 | `5e6bf66` |
| `prism_oracle` | 0.918 | 0.724 | 0.783 | 5,479 | `5e6bf66` |

**Paired comparison, `prism_full` (after) − `arm1_hybrid_rag`** (**derived**, `scripts/layer4_paper_stats.py`, output
`reports/layer4/diagnostics/paper_stats.json`, `7ef8cf9`):
- The pairing unit is the 80 matched (task, seed) cells.
- CI: a bootstrap that resamples tasks, then seeds within a task (10,000 resamples, seed 0).
- Wilcoxon signed-rank over the 8 task means.

| metric | Δ | 95% CI | tasks won | Wilcoxon p |
|---|---|---|---|---|
| precision | +0.236 | [+0.050, +0.400] | 6/8 | 0.078 |
| recall | +0.166 | [−0.003, +0.348] | 5/8 | 0.195 |
| F1 | +0.200 | [+0.026, +0.371] | 6/8 | 0.078 |

The bootstrap intervals for F1 and precision exclude zero; the recall interval does not. With 8 tasks, no Wilcoxon
test reaches p < 0.05. **The advantage is consistent but not statistically established at the task level.**

**Per-task F1 for `prism_full`** (`2cb1e62` → `5e6bf66`):

| task | before | after |
|---|---|---|
| express_02 (`res.header`) | 0.000 | 0.711 |
| fastapi_02 | 0.244 | 0.569 |
| express_01 | 0.372 | 0.517 |
| trpc_01 | 0.436 | 0.526 |
| trpc_02 | 0.359 | 0.525 |
| django_02 | 0.581 | 0.654 |
| django_01 | 0.250 | 0.225 |
| fastapi_01 | 0.326 | 0.241 |

Two tasks got worse after the fixes: django_01 and fastapi_01.

### Table 2: backward adaptation (540 cells)

The 540 cells are 7 arms × 80, minus prism_full's 20 leak cells.

**Reported pass rates** (`5e6bf66`):

| arm | n | pass rate | parses | context tokens |
|---|---|---|---|---|
| `arm0_no_retrieval` | 80 | 0.000 | 0.988 | 17 |
| `repomap_pagerank` | 80 | 0.000 | 1.000 | 3,422 |
| `arm1_hybrid_rag` | 80 | 0.013 | 1.000 | 7,041 |
| `prism_ask` | 80 | 0.000 | 1.000 | 8,514 |
| `prism_full` | 60 | 0.033 | 1.000 | 2,495 |
| `normal_oracle` | 80 | 0.000 | 1.000 | 3,154 |
| `prism_oracle` | 80 | 0.000 | 1.000 | 3,154 |

**Failure classes: a derived classification** (`7ef8cf9`), applied to each cell's test failure text by a fixed rule:
- **Pass:** the gating tests passed.
- **Syntax error:** the answer did not parse, or failed with `SyntaxError`, `IndentationError`, or esbuild
  `Transform failed`.
- **Timeout:** a test-runner timeout message. The cells carry no timeout field, so this is read from log text only.
- **Undefined API:** `NameError`, `ReferenceError … is not defined`, `has no attribute`, `is not a function`,
  `ImportError` / `ModuleNotFoundError` / `Cannot find module`, or an unexpected keyword argument.
- **Wrong behavior:** everything else.

| arm | n | pass | wrong behavior | undefined API | syntax error | timeout |
|---|---|---|---|---|---|---|
| `arm0_no_retrieval` | 80 | 0 | 38 | 22 | 18 | 2 |
| `repomap_pagerank` | 80 | 0 | 43 | 34 | 3 | 0 |
| `arm1_hybrid_rag` | 80 | 1 | 52 | 25 | 1 | 1 |
| `prism_ask` | 80 | 0 | 47 | 31 | 2 | 0 |
| `prism_full` | 60 | 2 | 41 | 16 | 1 | 0 |
| `normal_oracle` | 80 | 0 | 40 | 39 | 1 | 0 |
| `prism_oracle` | 80 | 0 | 40 | 39 | 1 | 0 |
| **total** | 540 | 3 | 301 | 206 | 27 | 3 |

**The oracle's 39 undefined-API failures: an empirical generation ceiling.**
- The backward oracle's context holds the masked focal function, its callers, its callees and types (`LAYER4.md`).
- Even so, 39 of 80 oracle cells fail on a name that the answer uses but never binds, or that does not exist. That is
  the highest count of any arm.
- By task (cell-level inspection of `5e6bf66`):

  | task | failure | cells |
  |---|---|---|
  | `l4_bwd_django_01` | `UpdateQuery` not defined / `add_update_values` | 10 |
  | `l4_bwd_fastapi_02` | imports `CacheKey` from the wrong module | 9 |
  | `l4_bwd_trpc_01` | `emptyRouter` / `createCallerFactory` not defined | 10 |
  | `l4_bwd_trpc_02` | `Observable` not defined | 8 |

- More relevant context does not remove this failure. The data is consistent with a limit in the 14B model's code
  generation (tracking imports and bindings across a re-implemented body), not with missing retrieval.
- **Scope of the claim:** it rests on one model, 8 tasks and a regex classifier. The paper should present it as an
  observed ceiling, not as proof of a capacity limit.
- **The citable ceiling comes straight from the report:** the oracle passes 0/80, so this family cannot separate
  retrieval arms with this model.

### Table 3: the recall funnel, and the Turn-1 bottleneck

**Forward funnel for `prism_full` after the fixes** (`reports/layer4/diagnostics/RECALL_FUNNEL_forward_context_v2.md`,
`72b6efd`). The last three stages are means over 10 seeds per task, summed over the 8 tasks.

| stage | gold callers | share of gold |
|---|---|---|
| traced gold (within max_depth) | 63 | 100% |
| in the index | 63 | 100% |
| in the static graph | 48 | 76.2% |
| in the candidate list (V_cand) | 49 | 77.8% |
| packed into context | 32.2 | 51.1% |
| emitted by the model | 28.4 | 45.1% |
| — of which named in context | 28.3 | 44.9% |

**Where gold is lost:**
- **Between the candidate list and the context: 16.8 per cell.**
  - **Turn 1: 14.0.** The model never requested them.
  - **Render budget: 2.8.** fastapi_01 2.1, trpc_01 0.4, trpc_02 0.2, django_02 0.1.
  - The split comes from replaying the model's requests.
- **After packing:** a further 3.9 gold were in context but not named in the answer.
- **Before the candidate list:** 13 of the 14 missing gold callers have no static call path to the focal function.
  - Express callback chains: `format`, `sendFile`, `sendfile`, `Layer.handle`, `handle_error`, `restore`.
  - tRPC: `callProcedure`, `createCallerFactory`, `observable.error`.
  - Django's config-driven `process_response`, FastAPI's `add_api_route` and `FastAPI.setup`.
  - The 14th is a hop-2 caller the list builder does not admit (`fastapi.routing.get_request_handler`).
- The candidate list counts one more gold than the static graph: django_01's `MiddlewareMixin.__call__`, which the
  caller walk reaches but the graph-only count does not.

**Independent corroboration from M4 R0 vs. R1** (`58d107e`; Qwen 14B; T5 blast radius; 192 cells). R1 is the shipped
pipeline, where the model picks from the Design C manifest. R0 is the same pipeline with "take every `caller` row"
instead of a model call.

| config | fastapi (n=8) | django (n=8) | express (n=2, anecdotal) | trpc (n=14) |
|---|---|---|---|---|
| R1 (model picks) | 0.375 | 0.407 | 0.333 | 0.372 |
| R0 (rule picks) | 0.518 | 0.558 | 0.500 | 0.666 |

| config | gold coverage | selection precision | selection recall |
|---|---|---|---|
| R1 | 0.461 | 0.507 | 0.460 |
| R0 | 0.878 | 0.681 | 0.874 |

- R0 leads in every corpus. The gain is significant after Holm correction on tRPC (p_holm < 0.001) and not on the
  others.
- On an independent semantic gold (4 Django seeds) the gap narrows: answer recall 0.355 vs. 0.335.
- **The two phases agree:** the model's Turn-1 selection over a correct candidate list is the binding constraint on
  blast-radius recall.

**The caveat:** rule selection moves pressure onto the budget. In a Layer 4 replay that requests every candidate
(`72b6efd`, section "Budget"), django_02 keeps 0 of 5 gold and fastapi_01 keeps 4 of 8. Rule selection would need a
packer that ranks callers ahead of downstream rows.

## 5. System architecture and production integration

### 5.1 Static index

- **Parsing:** Tree-sitter (`src/prism/parser/tree_sitter_loader.py`, `lang_config.py`, `queries.py`). A
  content-addressed AST cache keyed by path and SHA-256 of the content (`src/prism/parser/cache.py`, `2aedc4c`).
- **Symbol table and graph:** `ConcreteGraphBuilder` (`src/prism/graph/concrete_builder.py`, `symbol_table.py`).
  - Edge relations: `CALLS`, `INSTANTIATES`, `EXTENDS`, `OVERRIDES`, `READS_STATE`, and `ENCLOSES` (added in
    `ef3bf12`).
  - Calls that cannot be resolved with certainty become a `TENTATIVE_CALL` or an explicit ambiguity sentinel
    (`<ambiguous:name@file:line>`), never a guess.
- **Edge-resolution extensions that fed the Layer 4 rerun:**
  - `ce57a69`: JS chained-assignment method aliases, and same-object `this` calls.
  - `ef3bf12`, Fix C: a closure gets an `ENCLOSES` edge from the function that defines it, which the caller walk
    crosses at no extra hop.
  - `ef3bf12`, Fix D: a constructor inherits its class's callers. Calling a value that holds a class (a `Type[C]`
    annotation, a class default, `a or b`, `self.attr`) creates an `INSTANTIATES` edge; never on two possible classes.
  - `ef3bf12`, Fix E: `self.m()` with no `m` anywhere up the class hierarchy gets tentative edges to subclass
    definitions (dynamic hooks).
  - `ef3bf12`, Fix F: a receiver named after a class that only inherits the method, while other class families
    define their own, is marked ambiguous instead of guessed.
- **Static coverage over the 16 Layer 4 tasks** (`reports/layer4/diagnostics/static_coverage_{before,after}_cf.json`,
  `ef3bf12`):

  | measure | before Fixes C–F | after |
  |---|---|---|
  | direct-caller edge recall | 41/44 | 41/44 |
  | callers reached within depth | 55/77 | 58/77 |
  | callees reached | 55/120 | 55/120 |

### 5.2 Two-pass engine (`prism_full`)

- **Turn 1: the candidate manifest** (`src/prism/packer/candidate_index.py::build_candidate_manifest`).
  - One compact row per graph candidate: qualified name, role, signature, size, and the axis annotations.
  - Default mode is downstream.
  - Blast-radius mode (`direction="both"`, Design C, `6180d5b`) interleaves transitive callers (up to
    `UPSTREAM_WALK_MAX_HOPS = 6`) with downstream rows under the token budget.
  - The model returns `requested_symbols`; names outside the manifest are discarded.
- **Turn 2: hydration** (`PrismEngine.retrieve_requested` → `pack_symbol_context_requested`,
  `src/prism/packer/submodular_knapsack.py`).
  - Turn 2 does **not** run the knapsack. Its docstring says "no knapsack, no density competition": it admits the
    seed plus every request that resolves to the candidate list.
  - The token budget is applied at render time (`_enforce_render_budget`). That is where the 2.8 budget losses in
    Table 3 happen.
- **The submodular knapsack** (`pack_symbol_context`, `submodular_knapsack.py`) is PRISM's single-pass packer. It
  scores density and novelty and assigns variable resolution (full / skeleton / contract / stub). It backs
  `prism.slice` and `get_symbol_context`, and it was the production baseline that the spike's two-pass approach
  replaced for `prism_full`.

### 5.3 MCP server (`src/prism/mcp/server.py`)

| tool | purpose |
|---|---|
| `get_symbol_context` | token-budgeted, variable-resolution Markdown context for a symbol (knapsack) |
| `get_architectural_invariants` | a symbol's semantic tags, its call edges with runtime confidence (`CONFIRMED_RUNTIME` vs. `STATIC`), and any unmet `REQUIRES_BEFORE` relation (e.g. a `#db_write` with no `#auth_guard` upstream) |
| `find_symbols_by_tag` | every symbol mapped to a tag (`#route_handler`, `#db_write`, …) with location and runtime invocation count |
| `get_graph_status` | index statistics: symbols, call edges, runtime-confirmed vs. runtime-discovered edges, per-tag counts |
| `reindex_repo` | drop the cached graph and rebuild it, re-merging recorded runtime traces |
| `prism.slice` / `prism.explain` | `<prism_context>` envelope from one seed (knapsack, both directions; at most 3 callers per step, 1.5 hops). `task_type` only controls whether a `<causal_path>` block appears |
| `prism.blast_radius` | the Design C manifest (`direction="both"`, default budget 13,000 tokens). Takes every `caller` row by rule, with no model call, then hydrates; tests included by default |
| `prism.ask` | lexical ranking for a natural-language question (BM25 + rule-based intent re-rank) with entry-point breadcrumbs. The Stage 1 `prism_shipped_ask` row |

**Tool selection in production vs. in the benchmark:**
- **Production:** no agent passes `task_type="blast"`. The client LLM chooses a tool by matching the user's request
  to the tool names and docstrings, and PRISM does no routing. `prism.blast_radius` has no task-type parameter: blast
  mode is built into the tool.
- **Benchmark:** `benchmarks/final_sweep/runner.py::_two_pass_context` sends `task.task_type == "blast"` cells to
  `direction="both"` (`ce57a69`). That assumes the agent picked the right tool.
- **This can't affect the debug sweeps:** they load debug tasks only (`runner.py`, `task_type == "debug"` filter).
  `tests/test_final_sweep_8arms.py` pins that debug cells get the default manifest.
- **Production routing experiment** (`harness/experiments/production_routing/`; report `b58620b`): a query-intent
  classifier that never reads `task_type`. It scores 33/36 on held-out queries, with no query that clearly asks in
  one direction sent the other way. It is parked (off by default) and was not run in any benchmark.

### 5.4 Requested enhancements not implemented

The six tools `find_test_targets`, `mock_envelope`, `trace_path`, `contract_diff`, `find_implementations` and
`find_dead_code` **do not exist** in the repository. They appear in no source file and no commit on any branch, as of
`7ef8cf9`. The paper must not describe them as implemented or evaluated. If they are planned, list them as future
work.

## 6. Threats to validity and limitations

**(a) Fixes C–F were tuned on the Layer 4 forward traces.**
- The index fixes (closures, class-value construction, undefined-hook dispatch, the inherited-only receiver guard;
  `ef3bf12`) were designed from the runtime traces of the same forward tasks they are scored on. Examples: FastAPI
  routing closures and Django session middleware.
- Only `prism_full`'s 80 forward cells were rerun (the declared amendment "forward context v2 (prism_full)").
  `prism_ask` also depends on the static index but was scored on the earlier index (`2cb1e62`).
- The F1 rise from 0.321 to 0.496 is therefore partly fit to the test set, and the comparison with `prism_ask` is
  uneven.
- The gains are not uniform: fastapi_01 fell from 0.326 to 0.241 and django_01 from 0.250 to 0.225.
- **Required in the paper:**
  - report both `prism_full` rows and label the second as using trace-informed index fixes;
  - rerun `prism_ask` on the same code before comparing the two;
  - support any headline claim with tasks not used in the diagnosis.

**(b) The Layer 4 harness assumes the right tool is chosen.**
- `prism_full`'s forward cells get the blast-radius manifest because the task is labeled `blast`. That is an upper
  bound on routing, not a measurement of it.
- The label leaks nothing the prompt does not already state.
- `prism_full` is also not the production `prism.blast_radius` tool:
  - prism_full: the model picks at Turn 1, and tests are excluded;
  - `prism.blast_radius`: takes every caller by rule, and includes tests by default.
- A routed ablation, using the intent classifier instead of the label, would measure the realistic case.

**(c) Small task counts, and the limits of runtime-trace gold.**
- Layer 4 has 8 tasks per family across 4 repositories. No Wilcoxon test over the task means reaches p < 0.05 (Table
  1), and the per-task results vary widely.
- Forward gold contains only callers reached by each repository's own tests. Callers on paths the tests never run are
  missing, so recall is measured against a lower bound of the true blast radius, and some "false positives" may be
  real callers.
- Gold excludes test files and example code (`LAYER4.md`).
- Backward pass rates are near zero for every arm (the oracle passes 0/80), so that family cannot separate arms.
- The backward failure classes are a regex classification (`7ef8cf9`).

**Other limits:**
- **Models:** each phase uses one answer model (Qwen 7B or 14B, or gpt-4o-mini), with no cross-model replication
  inside a phase, apart from the partial gpt-4o-mini sweep of Sep 30 (`a6aa3fd`, `ae75dd0`).
- **Baseline fidelity:** the PageRank baseline is Aider-inspired, not a faithful replication of Aider's repo map
  (`167f9d3`).
- **Pilot:** the pilot report does not record its model (`2aedc4c`).
- **Stage 2 metric:** a causal-trace success rate, not test-suite resolution.
