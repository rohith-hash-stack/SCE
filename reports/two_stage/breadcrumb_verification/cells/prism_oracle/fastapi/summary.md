# Final sweep summary (final-sweep-8arms/1.1)

Cells: 20 (ok=20, error=0, dry_run=0); schema-invalid records: 0

| arm | ok/n | TSR (95% CI) | TSR partial | cleanliness | CPI ctx | CPI answer | CPI e2e | suff. ratio | sufficient | truncated | ctx tokens | $/cell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `prism_oracle` | 20/20 | 1.000 [0.84, 1.00] | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 | 1922 | 0.00000 |

| arm | Turn-1 parse fail | answer parse fail | anchor = task seed | seed-varying tasks | Turn-1 reused | distractors in ctx / named | mean latency (s) | calls | cost ($) |
|---|---|---|---|---|---|---|---|---|---|
| `prism_oracle` | n/a | 0.000 | 1.000 | 0/2 | 0 | - | 7.55 | 20 | 0.0000 |

Served models: {'qwen2.5-coder:14b-instruct-q8_0': 20}
System fingerprints: {'fp_ollama': 20}
Tasks whose Turn-1 manifest hash varied across seeds (should be none): none

Cost: $0.0000 over 20 LLM calls (45,110 prompt + 1,634 completion tokens).
Full-sweep projection: 900 cells (1 arms x 900) ≈ $0.00 - this run's mean cost per ok cell, per arm (repo mix differs - Django/FastAPI contexts are larger).
