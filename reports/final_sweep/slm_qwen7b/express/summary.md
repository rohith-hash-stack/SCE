# Final sweep summary (final-sweep-8arms/1.1)

Cells: 1600 (ok=1600, error=0, dry_run=0); schema-invalid records: 0

| arm | ok/n | TSR (95% CI) | TSR partial | cleanliness | CPI ctx | CPI answer | CPI e2e | suff. ratio | sufficient | truncated | ctx tokens | $/cell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | 200/200 | 0.375 [0.31, 0.44] | 0.481 | 0.412 | 1.000 | 0.953 | 0.953 | 1.000 | 1.000 | 1.000 | 3293 | 0.00000 |
| `pragmatic_oracle` | 200/200 | 0.430 [0.36, 0.50] | 0.515 | 1.000 | 1.000 | 0.970 | 0.970 | 0.600 | 0.600 | 0.000 | 1190 | 0.00000 |
| `scaffolded_oracle` | 200/200 | 0.890 [0.84, 0.93] | 0.968 | 0.880 | 1.000 | 0.973 | 0.973 | 1.000 | 1.000 | 0.500 | 1524 | 0.00000 |
| `prism_full` | 200/200 | 0.780 [0.72, 0.83] | 0.918 | 0.677 | 0.967 | 0.939 | 0.939 | 0.870 | 0.815 | 0.500 | 2306 | 0.00000 |
| `ablation_lexical_anchors` | 200/200 | 0.740 [0.68, 0.80] | 0.863 | 0.526 | 0.625 | 0.891 | 0.621 | 0.772 | 0.750 | 0.500 | 1560 | 0.00000 |
| `ablation_signature_only` | 200/200 | 0.815 [0.76, 0.86] | 0.938 | 0.680 | 0.983 | 0.955 | 0.955 | 0.873 | 0.825 | 0.515 | 2103 | 0.00000 |
| `ablation_no_purity` | 200/200 | 0.810 [0.75, 0.86] | 0.938 | 0.676 | 0.966 | 0.945 | 0.945 | 0.885 | 0.835 | 0.500 | 2317 | 0.00000 |
| `prism_plus_distractors` | 200/200 | 0.755 [0.69, 0.81] | 0.897 | 0.208 | 0.967 | 0.938 | 0.938 | 0.870 | 0.815 | 0.505 | 3993 | 0.00000 |

| arm | Turn-1 parse fail | answer parse fail | anchor = task seed | seed-varying tasks | Turn-1 reused | distractors in ctx / named | mean latency (s) | calls | cost ($) |
|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | n/a | 0.000 | 1.000 | 9/20 | 0 | - | 8.80 | 200 | 0.0000 |
| `pragmatic_oracle` | n/a | 0.000 | 1.000 | 4/20 | 0 | - | 7.05 | 200 | 0.0000 |
| `scaffolded_oracle` | n/a | 0.005 | 1.000 | 5/20 | 0 | - | 7.08 | 200 | 0.0000 |
| `prism_full` | 0.000 | 0.000 | 1.000 | 7/20 | 0 | - | 16.22 | 500 | 0.0000 |
| `ablation_lexical_anchors` | 0.000 | 0.005 | 0.400 | 3/20 | 0 | - | 15.08 | 500 | 0.0000 |
| `ablation_signature_only` | 0.000 | 0.000 | 1.000 | 8/20 | 0 | - | 15.95 | 500 | 0.0000 |
| `ablation_no_purity` | 0.000 | 0.000 | 1.000 | 7/20 | 0 | - | 16.10 | 500 | 0.0000 |
| `prism_plus_distractors` | 0.000 | 0.000 | 1.000 | 6/20 | 200 | 8.0 / 0.03 | 11.46 | 300 | 0.0000 |

Paired contrasts (same task and seed; A - B):

| A | B | what it isolates | pairs | ΔTSR (95% CI) | Δcleanliness |
|---|---|---|---|---|---|
| `scaffolded_oracle` | `pragmatic_oracle` | scaffolding effect (cleanliness paradox) | 200 | +0.460 [+0.380, +0.540] | -0.120 |
| `prism_full` | `baseline_bfs_bidirectional` | PRISM vs floor | 200 | +0.405 [+0.310, +0.500] | +0.266 |
| `prism_full` | `ablation_lexical_anchors` | taxonomy vs lexical anchors | 200 | +0.040 [-0.015, +0.095] | +0.151 |
| `prism_full` | `ablation_signature_only` | 4-axis annotations vs signatures only | 200 | -0.035 [-0.090, +0.020] | -0.002 |
| `prism_full` | `ablation_no_purity` | purity axis | 200 | -0.030 [-0.075, +0.015] | +0.002 |
| `prism_full` | `prism_plus_distractors` | no noise vs injected distractors | 200 | +0.025 [-0.025, +0.075] | +0.470 |

Served models: {'qwen2.5-coder:7b-instruct-q8_0': 2900}
System fingerprints: {'fp_ollama': 2900}
Tasks whose Turn-1 manifest hash varied across seeds (should be none): none

Cost: $0.0000 over 2900 LLM calls (5,050,825 prompt + 268,453 completion tokens).
Full-sweep projection: 7,200 cells (8 arms x 900) ≈ $0.00 - this run's mean cost per ok cell, per arm (repo mix differs - Django/FastAPI contexts are larger).
