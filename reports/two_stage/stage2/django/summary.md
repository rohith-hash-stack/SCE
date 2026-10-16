# Final sweep summary (final-sweep-8arms/1.1)

Cells: 1400 (ok=1400, error=0, dry_run=0); schema-invalid records: 0

| arm | ok/n | TSR (95% CI) | TSR partial | cleanliness | CPI ctx | CPI answer | CPI e2e | suff. ratio | sufficient | truncated | ctx tokens | $/cell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `pragmatic_oracle` | 200/200 | 0.895 [0.84, 0.93] | 0.975 | 1.000 | 1.000 | 0.977 | 0.977 | 0.050 | 0.050 | 0.000 | 1813 | 0.00000 |
| `prism_full` | 200/200 | 0.780 [0.72, 0.83] | 0.941 | 0.809 | 0.950 | 0.946 | 0.946 | 0.569 | 0.305 | 0.290 | 5027 | 0.00000 |
| `arm0_no_retrieval` | 200/200 | 0.170 [0.12, 0.23] | 0.384 | 1.000 | 0.000 | 0.671 | 0.000 | 0.050 | 0.050 | 0.000 | 285 | 0.00000 |
| `arm1_hybrid_rag` | 200/200 | 0.285 [0.23, 0.35] | 0.660 | 0.202 | 0.581 | 0.740 | 0.571 | 0.381 | 0.200 | 1.000 | 8471 | 0.00000 |
| `baseline_pagerank_repomap` | 200/200 | 0.155 [0.11, 0.21] | 0.505 | 0.033 | 0.088 | 0.614 | 0.088 | 0.183 | 0.050 | 1.000 | 3912 | 0.00000 |
| `prism_ask` | 200/200 | 0.235 [0.18, 0.30] | 0.632 | 0.217 | 0.372 | 0.717 | 0.372 | 0.414 | 0.150 | 1.000 | 8217 | 0.00000 |
| `prism_oracle` | 200/200 | 0.860 [0.81, 0.90] | 0.968 | 1.000 | 1.000 | 0.970 | 0.970 | 0.050 | 0.050 | 0.000 | 1813 | 0.00000 |

| arm | Turn-1 parse fail | answer parse fail | anchor = task seed | seed-varying tasks | Turn-1 reused | distractors in ctx / named | mean latency (s) | calls | cost ($) |
|---|---|---|---|---|---|---|---|---|---|
| `pragmatic_oracle` | n/a | 0.000 | 1.000 | 3/20 | 0 | - | 12.34 | 200 | 0.0000 |
| `prism_full` | 0.005 | 0.000 | 1.000 | 5/20 | 0 | - | 28.83 | 400 | 0.0000 |
| `arm0_no_retrieval` | n/a | 0.015 | n/a | 6/20 | 0 | - | 12.39 | 200 | 0.0000 |
| `arm1_hybrid_rag` | n/a | 0.000 | n/a | 4/20 | 0 | - | 16.51 | 200 | 0.0000 |
| `baseline_pagerank_repomap` | n/a | 0.010 | 0.100 | 5/20 | 0 | - | 13.11 | 200 | 0.0000 |
| `prism_ask` | n/a | 0.000 | n/a | 6/20 | 0 | - | 14.86 | 200 | 0.0000 |
| `prism_oracle` | n/a | 0.000 | 1.000 | 4/20 | 0 | - | 11.18 | 200 | 0.0000 |

Paired contrasts (same task and seed; A - B):

| A | B | what it isolates | pairs | ΔTSR (95% CI) | Δcleanliness |
|---|---|---|---|---|---|
| `prism_ask` | `arm1_hybrid_rag` | prism ask payload vs hybrid RAG | 200 | -0.050 [-0.120, +0.020] | +0.015 |
| `prism_full` | `arm1_hybrid_rag` | PRISM two-pass vs hybrid RAG | 200 | +0.495 [+0.425, +0.565] | +0.607 |
| `prism_oracle` | `pragmatic_oracle` | entry-point breadcrumbs over the gold slice | 200 | -0.035 [-0.065, -0.015] | +0.000 |
| `arm1_hybrid_rag` | `arm0_no_retrieval` | hybrid RAG vs no retrieval | 200 | +0.115 [+0.045, +0.190] | -0.798 |

Served models: {'qwen2.5-coder:14b-instruct-q8_0': 1600}
System fingerprints: {'fp_ollama': 1600}
Tasks whose Turn-1 manifest hash varied across seeds (should be none): none

Cost: $0.0000 over 1600 LLM calls (6,875,415 prompt + 206,011 completion tokens).
Full-sweep projection: 6,300 cells (7 arms x 900) ≈ $0.00 - this run's mean cost per ok cell, per arm (repo mix differs - Django/FastAPI contexts are larger).
