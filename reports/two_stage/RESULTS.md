# PRISM two-stage validation

Code `f7c2d21d296c0eb71b1f52bfe1f4bd93ad5b6a88`, answer model `qwen2.5-coder:14b-instruct-q8_0`, SLM `qwen2.5-coder:7b-instruct-q8_0`, seeds 42,43,44,45,46,47,48,49,50,51.

## Stage 1 - retrieval tournament

160 frozen questions (sets 1-3). Every engine ranks the same candidates. `normal_oracle` is 1.00 by construction (sanity row); `prism_shipped_ask` ranks exactly as `prism_rule_intent` (it adds the payload).

| engine | top-1 | recall@5 | MRR | separation | set1 top-1 | set2 top-1 | set3 top-1 |
|---|---|---|---|---|---|---|---|
| `plain_bm25` | 0.400 | 0.762 | 0.562 | 0.700 | 0.440 | 0.400 | 0.360 |
| `stemmed_bm25` | 0.494 | 0.825 | 0.635 | 0.762 | 0.680 | 0.450 | 0.360 |
| `dense_jina_code` | 0.738 | 0.891 | 0.814 | 0.875 | 0.800 | 0.867 | 0.520 |
| `hybrid_rrf_rerank` | 0.556 | 0.869 | 0.698 | 0.819 | 0.660 | 0.533 | 0.480 |
| `prism_rule_intent` | 0.525 | 0.831 | 0.660 | 0.756 | 0.680 | 0.550 | 0.340 |
| `prism_slm_live` | 0.613 | 0.887 | 0.730 | 0.794 | 0.760 | 0.633 | 0.440 |
| `prism_oracle` | 0.581 | 0.850 | 0.699 | 0.787 | 0.740 | 0.633 | 0.360 |
| `prism_shipped_ask` | 0.525 | 0.831 | 0.660 | 0.756 | 0.680 | 0.550 | 0.340 |
| `normal_oracle` | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

Paired contrasts (questions are the pairing unit; 95% CI: bootstrap over questions, stratified by repo):

| a | b | metric | Δ | 95% CI | wins-losses | sign-test p |
|---|---|---|---|---|---|---|
| `prism_slm_live` | `hybrid_rrf_rerank` | top1 | +0.056 | [-0.019, +0.131] | 27-18 | 0.233 |
| `prism_slm_live` | `hybrid_rrf_rerank` | rr | +0.032 | [-0.022, +0.086] | - | - |
| `prism_slm_live` | `hybrid_rrf_rerank` | separation | -0.025 | [-0.094, +0.044] | 15-19 | 0.608 |
| `prism_slm_live` | `stemmed_bm25` | top1 | +0.119 | [+0.069, +0.175] | 21-2 | 0.000 |
| `prism_slm_live` | `stemmed_bm25` | rr | +0.095 | [+0.062, +0.133] | - | - |
| `prism_slm_live` | `stemmed_bm25` | separation | +0.031 | [-0.013, +0.075] | 10-5 | 0.302 |
| `prism_rule_intent` | `stemmed_bm25` | top1 | +0.031 | [-0.006, +0.075] | 9-4 | 0.267 |
| `prism_rule_intent` | `stemmed_bm25` | rr | +0.025 | [+0.000, +0.051] | - | - |
| `prism_rule_intent` | `stemmed_bm25` | separation | -0.006 | [-0.031, +0.019] | 2-3 | 1.000 |
| `prism_rule_intent` | `hybrid_rrf_rerank` | top1 | -0.031 | [-0.113, +0.050] | 21-26 | 0.560 |
| `prism_rule_intent` | `hybrid_rrf_rerank` | rr | -0.038 | [-0.094, +0.016] | - | - |
| `prism_rule_intent` | `hybrid_rrf_rerank` | separation | -0.062 | [-0.138, +0.006] | 13-23 | 0.133 |
| `prism_oracle` | `hybrid_rrf_rerank` | top1 | +0.025 | [-0.056, +0.106] | 26-22 | 0.665 |
| `prism_oracle` | `hybrid_rrf_rerank` | rr | +0.001 | [-0.054, +0.056] | - | - |
| `prism_oracle` | `hybrid_rrf_rerank` | separation | -0.031 | [-0.100, +0.037] | 15-20 | 0.500 |
| `hybrid_rrf_rerank` | `stemmed_bm25` | top1 | +0.062 | [-0.019, +0.150] | 30-20 | 0.203 |
| `hybrid_rrf_rerank` | `stemmed_bm25` | rr | +0.063 | [+0.007, +0.122] | - | - |
| `hybrid_rrf_rerank` | `stemmed_bm25` | separation | +0.056 | [-0.013, +0.125] | 22-13 | 0.175 |
| `dense_jina_code` | `stemmed_bm25` | top1 | +0.244 | [+0.156, +0.331] | 52-13 | 0.000 |
| `dense_jina_code` | `stemmed_bm25` | rr | +0.179 | [+0.115, +0.245] | - | - |
| `dense_jina_code` | `stemmed_bm25` | separation | +0.113 | [+0.044, +0.181] | 25-7 | 0.002 |

SLM intents: 160 questions, 158 parsed, 124 fully valid; action on 135, capability on 129; agreement with the oracle - action 41/160, capability 66/160.

## Stage 2 - causal-trace success rate on the 90 debug tasks

Model `qwen2.5-coder:14b-instruct-q8_0`. TSR here is the **causal-trace success rate** (`score_debug_causal`: the answer names the root cause along the gold causal chain), not a test-suite pass rate. 95% CIs: bootstrap over tasks, stratified by corpus, seeds averaged within task. `normal_oracle` (the gold slice) is a sanity reference.

| arm | TSR | 95% CI | tasks | scored cells | mean context tokens | per corpus |
|---|---|---|---|---|---|---|
| `arm0_no_retrieval` | 0.201 | [0.120, 0.288] | 74 | 735/736 | 284 | django 0.03, express 0.16, fastapi 0.26, trpc 0.20 |
| `arm1_hybrid_rag` | 0.393 | [0.303, 0.488] | 74 | 732/742 | 7958 | django 0.47, express 0.22, fastapi 0.63, trpc 0.29 |
| `repomap_pagerank` | 0.385 | [0.280, 0.484] | 74 | 735/735 | 3942 | django 0.10, express 0.27, fastapi 0.36, trpc 0.55 |
| `prism_full` | 0.853 | [0.782, 0.918] | 74 | 735/735 | 3315 | django 0.75, express 0.93, fastapi 0.82, trpc 0.85 |
| `prism_ask` | 0.394 | [0.296, 0.481] | 74 | 733/735 | 7954 | django 0.00, express 0.25, fastapi 0.64, trpc 0.32 |
| `normal_oracle` | 0.736 | [0.655, 0.819] | 74 | 734/734 | 1830 | django 0.60, express 0.47, fastapi 0.85, trpc 0.86 |
| `prism_oracle` | 0.737 | [0.657, 0.819] | 74 | 734/734 | 1830 | django 0.57, express 0.47, fastapi 0.86, trpc 0.86 |

| a | b | contrast | tasks | Δ TSR | 95% CI | task wins-losses | sign-test p |
|---|---|---|---|---|---|---|---|
| `prism_full` | `arm1_hybrid_rag` | PRISM two-pass vs hybrid RAG | 74 | +0.458 | [+0.370, +0.545] | 49-2 | 0.000 |
| `prism_ask` | `arm1_hybrid_rag` | PRISM ask payload vs hybrid RAG | 74 | -0.005 | [-0.054, +0.043] | 12-9 | 0.664 |
| `prism_oracle` | `normal_oracle` | entry breadcrumbs over the gold slice | 74 | +0.001 | [-0.003, +0.005] | 2-1 | 1.000 |
| `arm1_hybrid_rag` | `arm0_no_retrieval` | hybrid RAG vs no retrieval | 74 | +0.196 | [+0.120, +0.273] | 26-5 | 0.000 |
| `repomap_pagerank` | `arm1_hybrid_rag` | PageRank repo-map vs hybrid RAG | 74 | -0.014 | [-0.112, +0.088] | 19-19 | 1.000 |
| `prism_full` | `repomap_pagerank` | PRISM two-pass vs PageRank repo-map | 74 | +0.472 | [+0.377, +0.565] | 45-1 | 0.000 |


**INCOMPLETE** - the session stopped before the time limit; re-run this cell to resume.
