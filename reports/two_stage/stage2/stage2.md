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
