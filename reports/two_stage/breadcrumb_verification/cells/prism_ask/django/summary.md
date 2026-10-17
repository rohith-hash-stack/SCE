# Final sweep summary (final-sweep-8arms/1.1)

Cells: 80 (ok=80, error=0, dry_run=0); schema-invalid records: 0

| arm | ok/n | TSR (95% CI) | TSR partial | cleanliness | CPI ctx | CPI answer | CPI e2e | suff. ratio | sufficient | truncated | ctx tokens | $/cell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `prism_ask` | 80/80 | 0.113 [0.06, 0.20] | 0.628 | 0.183 | 0.312 | 0.720 | 0.312 | 0.514 | 0.250 | 1.000 | 8247 | 0.00000 |

| arm | Turn-1 parse fail | answer parse fail | anchor = task seed | seed-varying tasks | Turn-1 reused | distractors in ctx / named | mean latency (s) | calls | cost ($) |
|---|---|---|---|---|---|---|---|---|---|
| `prism_ask` | n/a | 0.000 | n/a | 3/8 | 0 | - | 19.09 | 80 | 0.0000 |

Served models: {'qwen2.5-coder:14b-instruct-q8_0': 80}
System fingerprints: {'fp_ollama': 80}
Tasks whose Turn-1 manifest hash varied across seeds (should be none): none

Cost: $0.0000 over 80 LLM calls (697,720 prompt + 10,106 completion tokens).
Full-sweep projection: 900 cells (1 arms x 900) ≈ $0.00 - this run's mean cost per ok cell, per arm (repo mix differs - Django/FastAPI contexts are larger).
