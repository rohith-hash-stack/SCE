## Stage 2 - causal-trace success rate on the 90 debug tasks

Model `qwen2.5-coder:14b-instruct-q8_0`. TSR here is the **causal-trace success rate** (`score_debug_causal`: the answer names the root cause along the gold causal chain), not a test-suite pass rate. 95% CIs: bootstrap over tasks, stratified by corpus, seeds averaged within task. `normal_oracle` (the gold slice) is a sanity reference.

| arm | TSR | 95% CI | tasks | scored cells | mean context tokens | per corpus |
|---|---|---|---|---|---|---|
| `arm0_no_retrieval` | 0.201 | [0.126, 0.281] | 90 | 900/901 | 284 | django 0.17, express 0.16, fastapi 0.26, trpc 0.20 |
| `arm1_hybrid_rag` | 0.369 | [0.283, 0.452] | 90 | 897/910 | 8053 | django 0.29, express 0.22, fastapi 0.63, trpc 0.29 |
| `repomap_pagerank` | 0.344 | [0.260, 0.436] | 90 | 900/900 | 3937 | django 0.15, express 0.27, fastapi 0.36, trpc 0.55 |
| `prism_full` | 0.842 | [0.774, 0.902] | 90 | 900/900 | 3584 | django 0.78, express 0.93, fastapi 0.82, trpc 0.85 |
| `prism_ask` | 0.374 | [0.289, 0.460] | 90 | 899/902 | 8004 | django 0.24, express 0.25, fastapi 0.64, trpc 0.32 |
| `normal_oracle` | 0.779 | [0.709, 0.850] | 90 | 900/900 | 1807 | django 0.89, express 0.47, fastapi 0.85, trpc 0.86 |
| `prism_oracle` | 0.773 | [0.701, 0.843] | 90 | 900/900 | 1807 | django 0.86, express 0.47, fastapi 0.86, trpc 0.86 |

| a | b | contrast | tasks | Δ TSR | 95% CI | task wins-losses | sign-test p |
|---|---|---|---|---|---|---|---|
| `prism_full` | `arm1_hybrid_rag` | PRISM two-pass vs hybrid RAG | 90 | +0.474 | [+0.393, +0.556] | 61-2 | 0.000 |
| `prism_ask` | `arm1_hybrid_rag` | PRISM ask payload vs hybrid RAG | 90 | +0.006 | [-0.050, +0.061] | 17-11 | 0.345 |
| `prism_oracle` | `normal_oracle` | entry breadcrumbs over the gold slice | 90 | -0.006 | [-0.020, +0.004] | 2-2 | 1.000 |
| `arm1_hybrid_rag` | `arm0_no_retrieval` | hybrid RAG vs no retrieval | 90 | +0.167 | [+0.093, +0.240] | 29-9 | 0.002 |
| `repomap_pagerank` | `arm1_hybrid_rag` | PageRank repo-map vs hybrid RAG | 90 | -0.023 | [-0.116, +0.069] | 22-23 | 1.000 |
| `prism_full` | `repomap_pagerank` | PRISM two-pass vs PageRank repo-map | 90 | +0.498 | [+0.412, +0.583] | 57-1 | 0.000 |
