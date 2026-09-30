# Final sweep summary (final-sweep-8arms/1.1)

Cells: 1600 (ok=1600, error=0, dry_run=0); schema-invalid records: 0

| arm | ok/n | TSR (95% CI) | TSR partial | cleanliness | CPI ctx | CPI answer | CPI e2e | suff. ratio | sufficient | truncated | ctx tokens | $/cell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | 200/200 | 0.655 [0.59, 0.72] | 0.846 | 0.320 | 0.915 | 0.890 | 0.867 | 0.226 | 0.050 | 0.950 | 6299 | 0.00000 |
| `pragmatic_oracle` | 200/200 | 0.835 [0.78, 0.88] | 0.962 | 1.000 | 1.000 | 0.980 | 0.980 | 0.050 | 0.050 | 0.000 | 1819 | 0.00000 |
| `scaffolded_oracle` | 200/200 | 0.750 [0.69, 0.80] | 0.914 | 0.871 | 1.000 | 0.955 | 0.955 | 1.000 | 1.000 | 0.000 | 2742 | 0.00000 |
| `prism_full` | 200/200 | 0.740 [0.68, 0.80] | 0.920 | 0.864 | 0.942 | 0.929 | 0.929 | 0.594 | 0.360 | 0.190 | 5028 | 0.00000 |
| `ablation_lexical_anchors` | 200/200 | 0.515 [0.45, 0.58] | 0.782 | 0.606 | 0.609 | 0.794 | 0.587 | 0.511 | 0.250 | 0.180 | 4654 | 0.00000 |
| `ablation_signature_only` | 200/200 | 0.725 [0.66, 0.78] | 0.906 | 0.852 | 0.940 | 0.925 | 0.925 | 0.625 | 0.390 | 0.200 | 4487 | 0.00000 |
| `ablation_no_purity` | 200/200 | 0.710 [0.64, 0.77] | 0.915 | 0.839 | 0.939 | 0.916 | 0.915 | 0.603 | 0.365 | 0.200 | 5001 | 0.00000 |
| `prism_plus_distractors` | 200/200 | 0.695 [0.63, 0.75] | 0.900 | 0.420 | 0.942 | 0.915 | 0.915 | 0.594 | 0.360 | 0.400 | 6489 | 0.00000 |

| arm | Turn-1 parse fail | answer parse fail | anchor = task seed | seed-varying tasks | Turn-1 reused | distractors in ctx / named | mean latency (s) | calls | cost ($) |
|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | n/a | 0.010 | 1.000 | 5/20 | 0 | - | 15.44 | 200 | 0.0000 |
| `pragmatic_oracle` | n/a | 0.000 | 1.000 | 6/20 | 0 | - | 10.20 | 200 | 0.0000 |
| `scaffolded_oracle` | n/a | 0.000 | 1.000 | 10/20 | 0 | - | 10.23 | 200 | 0.0000 |
| `prism_full` | 0.010 | 0.000 | 1.000 | 7/20 | 0 | - | 23.29 | 400 | 0.0000 |
| `ablation_lexical_anchors` | 0.010 | 0.000 | 0.450 | 7/20 | 0 | - | 22.20 | 400 | 0.0000 |
| `ablation_signature_only` | 0.025 | 0.000 | 1.000 | 8/20 | 0 | - | 24.84 | 400 | 0.0000 |
| `ablation_no_purity` | 0.025 | 0.005 | 1.000 | 10/20 | 0 | - | 28.00 | 400 | 0.0000 |
| `prism_plus_distractors` | 0.010 | 0.000 | 1.000 | 10/20 | 200 | 6.6 / 0.00 | 14.72 | 200 | 0.0000 |

Paired contrasts (same task and seed; A - B):

| A | B | what it isolates | pairs | ΔTSR (95% CI) | Δcleanliness |
|---|---|---|---|---|---|
| `scaffolded_oracle` | `pragmatic_oracle` | scaffolding effect (cleanliness paradox) | 200 | -0.085 [-0.155, -0.020] | -0.129 |
| `prism_full` | `baseline_bfs_bidirectional` | PRISM vs floor | 200 | +0.085 [+0.030, +0.140] | +0.544 |
| `prism_full` | `ablation_lexical_anchors` | taxonomy vs lexical anchors | 200 | +0.225 [+0.160, +0.290] | +0.258 |
| `prism_full` | `ablation_signature_only` | 4-axis annotations vs signatures only | 200 | +0.015 [-0.030, +0.060] | +0.012 |
| `prism_full` | `ablation_no_purity` | purity axis | 200 | +0.030 [-0.015, +0.075] | +0.025 |
| `prism_full` | `prism_plus_distractors` | no noise vs injected distractors | 200 | +0.045 [+0.005, +0.085] | +0.444 |

Served models: {'qwen2.5-coder:7b-instruct-q8_0': 2400}
System fingerprints: {'fp_ollama': 2400}
Tasks whose Turn-1 manifest hash varied across seeds (should be none): none

Cost: $0.0000 over 2400 LLM calls (9,154,122 prompt + 334,761 completion tokens).
Full-sweep projection: 7,200 cells (8 arms x 900) ≈ $0.00 - this run's mean cost per ok cell, per arm (repo mix differs - Django/FastAPI contexts are larger).
