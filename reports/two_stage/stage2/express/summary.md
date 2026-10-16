# Final sweep summary (final-sweep-8arms/1.1)

Cells: 1400 (ok=1398, error=2, dry_run=0); schema-invalid records: 0

| arm | ok/n | TSR (95% CI) | TSR partial | cleanliness | CPI ctx | CPI answer | CPI e2e | suff. ratio | sufficient | truncated | ctx tokens | $/cell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `pragmatic_oracle` | 200/200 | 0.470 [0.40, 0.54] | 0.531 | 1.000 | 1.000 | 0.976 | 0.976 | 0.550 | 0.550 | 0.000 | 1193 | 0.00000 |
| `prism_full` | 200/200 | 0.925 [0.88, 0.95] | 0.980 | 0.653 | 0.985 | 0.980 | 0.980 | 0.952 | 0.920 | 0.505 | 2459 | 0.00000 |
| `arm0_no_retrieval` | 200/200 | 0.160 [0.12, 0.22] | 0.286 | 1.000 | 0.000 | 0.844 | 0.000 | 0.550 | 0.550 | 0.000 | 278 | 0.00000 |
| `arm1_hybrid_rag` | 199/200 | 0.221 [0.17, 0.28] | 0.414 | 0.055 | 0.746 | 0.854 | 0.733 | 0.570 | 0.550 | 1.000 | 8673 | 0.00000 |
| `baseline_pagerank_repomap` | 200/200 | 0.265 [0.21, 0.33] | 0.488 | 0.034 | 0.425 | 0.838 | 0.413 | 0.717 | 0.650 | 1.000 | 3944 | 0.00000 |
| `prism_ask` | 199/200 | 0.246 [0.19, 0.31] | 0.378 | 0.060 | 0.718 | 0.867 | 0.705 | 0.570 | 0.550 | 1.000 | 8523 | 0.00000 |
| `prism_oracle` | 200/200 | 0.470 [0.40, 0.54] | 0.531 | 1.000 | 1.000 | 0.976 | 0.976 | 0.550 | 0.550 | 0.000 | 1193 | 0.00000 |

| arm | Turn-1 parse fail | answer parse fail | anchor = task seed | seed-varying tasks | Turn-1 reused | distractors in ctx / named | mean latency (s) | calls | cost ($) |
|---|---|---|---|---|---|---|---|---|---|
| `pragmatic_oracle` | n/a | 0.000 | 1.000 | 3/20 | 0 | - | 9.68 | 200 | 0.0000 |
| `prism_full` | 0.000 | 0.000 | 1.000 | 2/20 | 0 | - | 22.38 | 500 | 0.0000 |
| `arm0_no_retrieval` | n/a | 0.000 | n/a | 2/20 | 0 | - | 7.27 | 200 | 0.0000 |
| `arm1_hybrid_rag` | n/a | 0.000 | n/a | 3/20 | 0 | - | 12.95 | 199 | 0.0000 |
| `baseline_pagerank_repomap` | n/a | 0.000 | 0.350 | 1/20 | 0 | - | 10.52 | 200 | 0.0000 |
| `prism_ask` | n/a | 0.000 | n/a | 4/20 | 0 | - | 13.44 | 199 | 0.0000 |
| `prism_oracle` | n/a | 0.000 | 1.000 | 3/20 | 0 | - | 8.01 | 200 | 0.0000 |

Paired contrasts (same task and seed; A - B):

| A | B | what it isolates | pairs | ΔTSR (95% CI) | Δcleanliness |
|---|---|---|---|---|---|
| `prism_ask` | `arm1_hybrid_rag` | prism ask payload vs hybrid RAG | 198 | +0.025 [-0.015, +0.066] | +0.006 |
| `prism_full` | `arm1_hybrid_rag` | PRISM two-pass vs hybrid RAG | 199 | +0.704 [+0.638, +0.764] | +0.600 |
| `prism_oracle` | `pragmatic_oracle` | entry-point breadcrumbs over the gold slice | 200 | +0.000 [+0.000, +0.000] | +0.000 |
| `arm1_hybrid_rag` | `arm0_no_retrieval` | hybrid RAG vs no retrieval | 199 | +0.060 [+0.015, +0.106] | -0.945 |

Served models: {'qwen2.5-coder:14b-instruct-q8_0': 1698}
System fingerprints: {'fp_ollama': 1698}
Tasks whose Turn-1 manifest hash varied across seeds (should be none): none

Cost: $0.0000 over 1698 LLM calls (5,960,094 prompt + 158,088 completion tokens).
Full-sweep projection: 6,300 cells (7 arms x 900) ≈ $0.00 - this run's mean cost per ok cell, per arm (repo mix differs - Django/FastAPI contexts are larger).

Errors:
- express_t02_003_routing_dispatch_loop arm1_hybrid_rag seed=50: LLMCallError: request to 'qwen2.5-coder:14b-instruct-q8_0' timed out after 180s: Request timed out.
- express_t02_003_routing_dispatch_loop prism_ask seed=51: LLMCallError: request to 'qwen2.5-coder:14b-instruct-q8_0' timed out after 180s: Request timed out.
