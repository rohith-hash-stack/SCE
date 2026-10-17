# Final sweep summary (final-sweep-8arms/1.1)

Cells: 150 (ok=150, error=0, dry_run=0); schema-invalid records: 0

| arm | ok/n | TSR (95% CI) | TSR partial | cleanliness | CPI ctx | CPI answer | CPI e2e | suff. ratio | sufficient | truncated | ctx tokens | $/cell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `prism_ask` | 150/150 | 0.740 [0.66, 0.80] | 0.812 | 0.122 | 0.542 | 0.714 | 0.541 | 0.167 | 0.067 | 1.000 | 7409 | 0.00000 |

| arm | Turn-1 parse fail | answer parse fail | anchor = task seed | seed-varying tasks | Turn-1 reused | distractors in ctx / named | mean latency (s) | calls | cost ($) |
|---|---|---|---|---|---|---|---|---|---|
| `prism_ask` | n/a | 0.007 | n/a | 2/15 | 0 | - | 16.09 | 150 | 0.0000 |

Served models: {'qwen2.5-coder:14b-instruct-q8_0': 150}
System fingerprints: {'fp_ollama': 150}
Tasks whose Turn-1 manifest hash varied across seeds (should be none): none

Cost: $0.0000 over 150 LLM calls (1,185,130 prompt + 16,885 completion tokens).
Full-sweep projection: 900 cells (1 arms x 900) ≈ $0.00 - this run's mean cost per ok cell, per arm (repo mix differs - Django/FastAPI contexts are larger).
