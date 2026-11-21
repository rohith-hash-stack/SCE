# Final sweep summary (final-sweep-8arms/1.1)

Cells: 1750 (ok=1748, error=2, dry_run=0); schema-invalid records: 0

| arm | ok/n | TSR (95% CI) | TSR partial | cleanliness | CPI ctx | CPI answer | CPI e2e | suff. ratio | sufficient | truncated | ctx tokens | $/cell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `pragmatic_oracle` | 250/250 | 0.860 [0.81, 0.90] | 0.969 | 1.000 | 1.000 | 0.992 | 0.992 | 0.160 | 0.160 | 0.000 | 1885 | 0.00000 |
| `prism_full` | 250/250 | 0.852 [0.80, 0.89] | 0.960 | 0.736 | 1.000 | 0.983 | 0.983 | 0.258 | 0.216 | 0.000 | 3131 | 0.00000 |
| `arm0_no_retrieval` | 250/250 | 0.200 [0.16, 0.25] | 0.537 | 1.000 | 0.000 | 0.636 | 0.000 | 0.160 | 0.160 | 0.000 | 285 | 0.00000 |
| `arm1_hybrid_rag` | 248/250 | 0.290 [0.24, 0.35] | 0.591 | 0.123 | 0.531 | 0.648 | 0.482 | 0.161 | 0.160 | 1.000 | 8259 | 0.00000 |
| `baseline_pagerank_repomap` | 250/250 | 0.548 [0.49, 0.61] | 0.858 | 0.146 | 0.943 | 0.881 | 0.881 | 0.522 | 0.360 | 0.040 | 3929 | 0.00000 |
| `prism_ask` | 250/250 | 0.316 [0.26, 0.38] | 0.619 | 0.129 | 0.552 | 0.673 | 0.530 | 0.250 | 0.200 | 1.000 | 8507 | 0.00000 |
| `prism_oracle` | 250/250 | 0.864 [0.82, 0.90] | 0.970 | 1.000 | 1.000 | 0.992 | 0.992 | 0.160 | 0.160 | 0.000 | 1885 | 0.00000 |

| arm | Turn-1 parse fail | answer parse fail | anchor = task seed | seed-varying tasks | Turn-1 reused | distractors in ctx / named | mean latency (s) | calls | cost ($) |
|---|---|---|---|---|---|---|---|---|---|
| `pragmatic_oracle` | n/a | 0.000 | 1.000 | 7/25 | 0 | - | 11.81 | 250 | 0.0000 |
| `prism_full` | 0.000 | 0.000 | 1.000 | 9/25 | 0 | - | 24.38 | 500 | 0.0000 |
| `arm0_no_retrieval` | n/a | 0.000 | n/a | 0/25 | 0 | - | 9.18 | 250 | 0.0000 |
| `arm1_hybrid_rag` | n/a | 0.008 | n/a | 10/25 | 0 | - | 15.12 | 248 | 0.0000 |
| `baseline_pagerank_repomap` | n/a | 0.000 | 1.000 | 6/25 | 0 | - | 13.40 | 250 | 0.0000 |
| `prism_ask` | n/a | 0.000 | n/a | 9/25 | 0 | - | 14.22 | 250 | 0.0000 |
| `prism_oracle` | n/a | 0.000 | 1.000 | 7/25 | 0 | - | 10.06 | 250 | 0.0000 |

Paired contrasts (same task and seed; A - B):

| A | B | what it isolates | pairs | ΔTSR (95% CI) | Δcleanliness |
|---|---|---|---|---|---|
| `prism_ask` | `arm1_hybrid_rag` | prism ask payload vs hybrid RAG | 248 | +0.028 [-0.012, +0.069] | +0.007 |
| `prism_full` | `arm1_hybrid_rag` | PRISM two-pass vs hybrid RAG | 248 | +0.560 [+0.500, +0.625] | +0.611 |
| `prism_oracle` | `pragmatic_oracle` | entry-point breadcrumbs over the gold slice | 250 | +0.004 [+0.000, +0.016] | +0.000 |
| `arm1_hybrid_rag` | `arm0_no_retrieval` | hybrid RAG vs no retrieval | 248 | +0.089 [+0.048, +0.133] | -0.877 |

Served models: {'qwen2.5-coder:14b-instruct-q8_0': 1998}
System fingerprints: {'fp_ollama': 1998}
Tasks whose Turn-1 manifest hash varied across seeds (should be none): none

Cost: $0.0000 over 1998 LLM calls (7,849,234 prompt + 236,659 completion tokens).
Full-sweep projection: 6,300 cells (7 arms x 900) ≈ $0.00 - this run's mean cost per ok cell, per arm (repo mix differs - Django/FastAPI contexts are larger).

Errors:
- trpc_t02_003_procedure_caller_error_normalization arm1_hybrid_rag seed=43: LLMCallError: request to 'qwen2.5-coder:14b-instruct-q8_0' timed out after 180s: Request timed out.
- trpc_t02_003_procedure_caller_error_normalization arm1_hybrid_rag seed=45: LLMCallError: request to 'qwen2.5-coder:14b-instruct-q8_0' timed out after 180s: Request timed out.
