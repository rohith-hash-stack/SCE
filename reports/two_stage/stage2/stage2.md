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
