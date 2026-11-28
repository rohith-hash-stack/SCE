# Final sweep summary (final-sweep-8arms/1.1)

Cells: 2000 (ok=2000, error=0, dry_run=0); schema-invalid records: 0

| arm | ok/n | TSR (95% CI) | TSR partial | cleanliness | CPI ctx | CPI answer | CPI e2e | suff. ratio | sufficient | truncated | ctx tokens | $/cell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | 250/250 | 0.780 [0.72, 0.83] | 0.911 | 0.467 | 0.984 | 0.961 | 0.961 | 0.327 | 0.200 | 1.000 | 4886 | 0.00000 |
| `pragmatic_oracle` | 250/250 | 0.836 [0.79, 0.88] | 0.926 | 1.000 | 1.000 | 0.984 | 0.984 | 0.040 | 0.040 | 0.000 | 2215 | 0.00000 |
| `scaffolded_oracle` | 250/250 | 0.832 [0.78, 0.87] | 0.911 | 0.636 | 1.000 | 0.970 | 0.970 | 0.990 | 0.960 | 0.040 | 3566 | 0.00000 |
| `prism_full` | 250/250 | 0.824 [0.77, 0.87] | 0.915 | 0.584 | 0.984 | 0.980 | 0.972 | 0.276 | 0.200 | 0.308 | 3673 | 0.00000 |
| `ablation_lexical_anchors` | 250/250 | 0.644 [0.58, 0.70] | 0.744 | 0.284 | 0.489 | 0.789 | 0.476 | 0.218 | 0.200 | 0.400 | 3312 | 0.00000 |
| `ablation_signature_only` | 250/250 | 0.872 [0.82, 0.91] | 0.941 | 0.559 | 0.994 | 0.989 | 0.989 | 0.292 | 0.200 | 0.304 | 3660 | 0.00000 |
| `ablation_no_purity` | 250/250 | 0.852 [0.80, 0.89] | 0.933 | 0.571 | 0.984 | 0.983 | 0.975 | 0.279 | 0.200 | 0.316 | 3676 | 0.00000 |
| `prism_plus_distractors` | 250/250 | 0.804 [0.75, 0.85] | 0.889 | 0.227 | 0.984 | 0.966 | 0.958 | 0.276 | 0.200 | 0.368 | 5215 | 0.00000 |

| arm | Turn-1 parse fail | answer parse fail | anchor = task seed | seed-varying tasks | Turn-1 reused | distractors in ctx / named | mean latency (s) | calls | cost ($) |
|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | n/a | 0.004 | 1.000 | 5/25 | 0 | - | 12.17 | 250 | 0.0000 |
| `pragmatic_oracle` | n/a | 0.000 | 1.000 | 6/25 | 0 | - | 10.25 | 250 | 0.0000 |
| `scaffolded_oracle` | n/a | 0.000 | 1.000 | 6/25 | 0 | - | 10.81 | 250 | 0.0000 |
| `prism_full` | 0.020 | 0.000 | 1.000 | 8/25 | 0 | - | 21.76 | 500 | 0.0000 |
| `ablation_lexical_anchors` | 0.084 | 0.012 | 0.360 | 6/25 | 0 | - | 28.80 | 500 | 0.0000 |
| `ablation_signature_only` | 0.016 | 0.000 | 1.000 | 7/25 | 0 | - | 22.63 | 500 | 0.0000 |
| `ablation_no_purity` | 0.012 | 0.000 | 1.000 | 6/25 | 0 | - | 22.10 | 500 | 0.0000 |
| `prism_plus_distractors` | 0.020 | 0.000 | 1.000 | 8/25 | 250 | 6.8 / 0.03 | 12.47 | 250 | 0.0000 |

Paired contrasts (same task and seed; A - B):

| A | B | what it isolates | pairs | ΔTSR (95% CI) | Δcleanliness |
|---|---|---|---|---|---|
| `scaffolded_oracle` | `pragmatic_oracle` | scaffolding effect (cleanliness paradox) | 250 | -0.004 [-0.036, +0.028] | -0.364 |
| `prism_full` | `baseline_bfs_bidirectional` | PRISM vs floor | 250 | +0.044 [+0.000, +0.088] | +0.116 |
| `prism_full` | `ablation_lexical_anchors` | taxonomy vs lexical anchors | 250 | +0.180 [+0.128, +0.236] | +0.300 |
| `prism_full` | `ablation_signature_only` | 4-axis annotations vs signatures only | 250 | -0.048 [-0.088, -0.008] | +0.025 |
| `prism_full` | `ablation_no_purity` | purity axis | 250 | -0.028 [-0.064, +0.008] | +0.013 |
| `prism_full` | `prism_plus_distractors` | no noise vs injected distractors | 250 | +0.020 [-0.024, +0.064] | +0.357 |

Served models: {'qwen2.5-coder:7b-instruct-q8_0': 3000}
System fingerprints: {'fp_ollama': 3000}
Tasks whose Turn-1 manifest hash varied across seeds (should be none): none

Cost: $0.0000 over 3000 LLM calls (9,374,813 prompt + 401,505 completion tokens).
Full-sweep projection: 7,200 cells (8 arms x 900) ≈ $0.00 - this run's mean cost per ok cell, per arm (repo mix differs - Django/FastAPI contexts are larger).
