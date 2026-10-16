# PRISM two-stage validation

Code `d48800528dbf13a769689ae06c65e2bbbd88c392`, answer model `qwen2.5-coder:14b-instruct-q8_0`, SLM `qwen2.5-coder:7b-instruct-q8_0`, seeds 42,43,44,45,46,47,48,49,50,51.

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
| `arm0_no_retrieval` | 0.181 | [0.083, 0.291] | 46 | 452/452 | 282 | express 0.16, fastapi 0.00, trpc 0.20 |
| `arm1_hybrid_rag` | 0.260 | [0.151, 0.376] | 45 | 443/450 | 8441 | express 0.21, trpc 0.29 |
| `repomap_pagerank` | 0.422 | [0.291, 0.551] | 45 | 450/450 | 3936 | express 0.27, trpc 0.55 |
| `prism_full` | 0.884 | [0.809, 0.949] | 45 | 450/450 | 2832 | express 0.93, trpc 0.85 |
| `prism_ask` | 0.285 | [0.171, 0.409] | 45 | 449/450 | 8514 | express 0.25, trpc 0.32 |
| `normal_oracle` | 0.687 | [0.573, 0.798] | 45 | 450/450 | 1577 | express 0.47, trpc 0.86 |
| `prism_oracle` | 0.689 | [0.576, 0.798] | 45 | 450/450 | 1577 | express 0.47, trpc 0.86 |

| a | b | contrast | tasks | Δ TSR | 95% CI | task wins-losses | sign-test p |
|---|---|---|---|---|---|---|---|
| `prism_full` | `arm1_hybrid_rag` | PRISM two-pass vs hybrid RAG | 45 | +0.629 | [+0.507, +0.747] | 37-1 | 0.000 |
| `prism_ask` | `arm1_hybrid_rag` | PRISM ask payload vs hybrid RAG | 45 | +0.029 | [-0.013, +0.080] | 8-6 | 0.791 |
| `prism_oracle` | `normal_oracle` | entry breadcrumbs over the gold slice | 45 | +0.002 | [+0.000, +0.007] | 1-0 | 1.000 |
| `arm1_hybrid_rag` | `arm0_no_retrieval` | hybrid RAG vs no retrieval | 45 | +0.073 | [+0.007, +0.153] | 11-5 | 0.210 |
| `repomap_pagerank` | `arm1_hybrid_rag` | PageRank repo-map vs hybrid RAG | 45 | +0.167 | [+0.036, +0.307] | 17-8 | 0.108 |
| `prism_full` | `repomap_pagerank` | PRISM two-pass vs PageRank repo-map | 45 | +0.462 | [+0.349, +0.580] | 28-1 | 0.000 |


**INCOMPLETE** - the session stopped before the time limit; re-run this cell to resume.
