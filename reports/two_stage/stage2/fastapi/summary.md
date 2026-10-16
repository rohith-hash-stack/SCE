# Final sweep summary (final-sweep-8arms/1.1)

Cells: 1750 (ok=1749, error=1, dry_run=0); schema-invalid records: 0

| arm | ok/n | TSR (95% CI) | TSR partial | cleanliness | CPI ctx | CPI answer | CPI e2e | suff. ratio | sufficient | truncated | ctx tokens | $/cell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `pragmatic_oracle` | 250/250 | 0.852 [0.80, 0.89] | 0.952 | 1.000 | 1.000 | 0.979 | 0.979 | 0.040 | 0.040 | 0.000 | 2214 | 0.00000 |
| `prism_full` | 250/250 | 0.816 [0.76, 0.86] | 0.932 | 0.560 | 0.984 | 0.968 | 0.960 | 0.295 | 0.200 | 0.332 | 3783 | 0.00000 |
| `arm0_no_retrieval` | 249/250 | 0.261 [0.21, 0.32] | 0.265 | 1.000 | 0.000 | 0.636 | 0.000 | 0.040 | 0.040 | 0.000 | 287 | 0.00000 |
| `arm1_hybrid_rag` | 250/250 | 0.632 [0.57, 0.69] | 0.770 | 0.205 | 0.611 | 0.769 | 0.609 | 0.152 | 0.080 | 1.000 | 7021 | 0.00000 |
| `baseline_pagerank_repomap` | 250/250 | 0.356 [0.30, 0.42] | 0.448 | 0.036 | 0.369 | 0.627 | 0.357 | 0.239 | 0.120 | 0.800 | 3960 | 0.00000 |
| `prism_ask` | 250/250 | 0.644 [0.58, 0.70] | 0.749 | 0.171 | 0.515 | 0.752 | 0.515 | 0.140 | 0.080 | 1.000 | 6919 | 0.00000 |
| `prism_oracle` | 250/250 | 0.856 [0.81, 0.89] | 0.956 | 1.000 | 1.000 | 0.979 | 0.979 | 0.040 | 0.040 | 0.000 | 2214 | 0.00000 |

| arm | Turn-1 parse fail | answer parse fail | anchor = task seed | seed-varying tasks | Turn-1 reused | distractors in ctx / named | mean latency (s) | calls | cost ($) |
|---|---|---|---|---|---|---|---|---|---|
| `pragmatic_oracle` | n/a | 0.000 | 1.000 | 3/25 | 0 | - | 10.57 | 250 | 0.0000 |
| `prism_full` | 0.000 | 0.000 | 1.000 | 4/25 | 0 | - | 25.57 | 500 | 0.0000 |
| `arm0_no_retrieval` | n/a | 0.012 | n/a | 8/25 | 0 | - | 11.25 | 249 | 0.0000 |
| `arm1_hybrid_rag` | n/a | 0.000 | n/a | 5/25 | 0 | - | 11.63 | 250 | 0.0000 |
| `baseline_pagerank_repomap` | n/a | 0.000 | 0.600 | 4/25 | 0 | - | 11.36 | 250 | 0.0000 |
| `prism_ask` | n/a | 0.000 | n/a | 2/25 | 0 | - | 12.37 | 250 | 0.0000 |
| `prism_oracle` | n/a | 0.000 | 1.000 | 3/25 | 0 | - | 9.31 | 250 | 0.0000 |

Paired contrasts (same task and seed; A - B):

| A | B | what it isolates | pairs | ΔTSR (95% CI) | Δcleanliness |
|---|---|---|---|---|---|
| `prism_ask` | `arm1_hybrid_rag` | prism ask payload vs hybrid RAG | 250 | +0.012 [-0.028, +0.052] | -0.034 |
| `prism_full` | `arm1_hybrid_rag` | PRISM two-pass vs hybrid RAG | 250 | +0.184 [+0.140, +0.232] | +0.355 |
| `prism_oracle` | `pragmatic_oracle` | entry-point breadcrumbs over the gold slice | 250 | +0.004 [-0.008, +0.020] | +0.000 |
| `arm1_hybrid_rag` | `arm0_no_retrieval` | hybrid RAG vs no retrieval | 249 | +0.373 [+0.309, +0.438] | -0.794 |

Served models: {'qwen2.5-coder:14b-instruct-q8_0': 1999}
System fingerprints: {'fp_ollama': 1999}
Tasks whose Turn-1 manifest hash varied across seeds (should be none): none

Cost: $0.0000 over 1999 LLM calls (7,586,215 prompt + 221,819 completion tokens).
Full-sweep projection: 6,300 cells (7 arms x 900) ≈ $0.00 - this run's mean cost per ok cell, per arm (repo mix differs - Django/FastAPI contexts are larger).

Errors:
- fastapi_t02_009_get_openapi_schema_and_route_iteration arm0_no_retrieval seed=43: LLMCallError: request to 'qwen2.5-coder:14b-instruct-q8_0' timed out after 180s: Request timed out.
