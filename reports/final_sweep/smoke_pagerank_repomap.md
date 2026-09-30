# Smoke test: `baseline_pagerank_repomap` (Aider-style PageRank repo map)

Branch `test/pagerank-repomap-smoke`.

**Implementation**
- Retriever: `benchmarks/baselines/pagerank_repomap.py`.
- Arm: `baseline_pagerank_repomap` (`benchmarks/final_sweep/config.py`,
  `EXPERIMENTAL_ARMS`). It is resolvable with `--arms` but excluded from
  `--arms all`, so the 8-arm protocol is unchanged.

**Method**
1. **Graph.** The directed graph covers all 7,880 FastAPI symbols with
   2,103 AST relation edges.
2. **Rank.** `nx.pagerank(alpha=0.85)`, personalized (teleport weight 100)
   toward symbols whose bare name, at least 3 characters and not generic,
   appears in the task prompt.
3. **Pack.** Signature lines only, in rank order, greedily up to **4,000
   rendered tokens**.

The retriever is not told the seed symbol.

## Status: retrieval measured; TSR not yet run

The 5 LLM cells were **not executed**. This environment has no GPU, and
its network policy blocks every source of the model: `ollama.com`,
`registry.ollama.ai` and `huggingface.co` all return 403.

Everything below except TSR comes from a zero-LLM dry run (real
retrieval). To get TSR, run `scripts/kaggle_slm_sweep.py` with the
commented "PageRank repo-map smoke test" lines enabled: `REPO="fastapi"`,
`SEEDS="1"`, `ARMS="baseline_pagerank_repomap"`, and the 5 task ids. Output
goes to `reports/final_sweep/smoke_qwen7b/fastapi/`, separate from the
sweep's cells.

`prism_full` values are its real `qwen2.5-coder:7b-instruct-q8_0`, seed 1,
T=0.4 cells from the final sweep.

| task | PageRank TSR | PageRank ctx tokens | PageRank cleanliness | PageRank pipeline in ctx | seed in map | prism_full TSR | prism_full ctx tokens | prism_full cleanliness | prism_full pipeline in ctx |
|---|---|---|---|---|---|---|---|---|---|
| t02_001 dependant_tree_construction | pending | 3,917 | 0.200 | 0.40 | yes | 1 | 5,944 | 0.889 | 1.00 |
| t02_002 solve_dependencies_runtime | pending | 3,901 | 0.053 | 0.00 | no | 0 | 7,570 | 0.583 | 1.00 |
| t02_003 request_params_coercion | pending | 3,873 | 0.000 | 0.00 | no | 1 | 3,158 | 1.000 | 1.00 |
| t02_004 route_registration_pipeline | pending | 3,963 | 0.000 | 0.00 | no | 1 | 7,938 | 0.545 | 1.00 |
| t02_005 request_validation_error | pending | 3,938 | 0.048 | 0.33 | yes | 0 | 4,493 | 0.600 | 1.00 |
| **mean** | pending | **3,918** | **0.060** | **0.15** | 2/5 | **0.60** | **5,821** | **0.724** | **1.00** |

## Observations (retrieval only)

- **Budget is respected.** Every map fits in 3.9K rendered tokens, with
  19-21 signatures and no ceiling cuts.
- **Low coverage and cleanliness.** Only 15% of annotated pipeline stages
  reach the context, versus 100% for `prism_full`, and cleanliness is
  0.06.
- **The limiting factor is rank depth, not personalization.**
  Personalization works: query hits are found and the seed ranks 10-25 on
  4 of 5 tasks. But only about 20 signatures fit, and the downstream
  pipeline stages rank about 70-250.
- **Duplicate names dilute the query hits.** 6,732 of the 7,880 nodes are
  `docs_src/` tutorials and tests, which redefine names like `Body` and
  `Cookie`.
- **A shorter-identifier filter changed nothing.** It was the only tuning
  tried; tuning further against the answer key was deliberately avoided.
- **Expected TSR effect.** A signatures-only map carries no bodies, so
  even when a stage is present the model cannot see what it calls.
  Expect low TSR, but that must be measured, not assumed.

Raw dry-run cells: `reports/final_sweep/smoke_pagerank_dryrun/fastapi/`.
