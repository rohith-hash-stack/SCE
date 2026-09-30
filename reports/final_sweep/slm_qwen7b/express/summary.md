# Final sweep summary (final-sweep-8arms/1.1)

Cells: 800 (ok=800, error=0, dry_run=0); schema-invalid records: 0

| arm | ok/n | TSR (95% CI) | TSR partial | cleanliness | CPI ctx | CPI answer | CPI e2e | suff. ratio | sufficient | truncated | ctx tokens | $/cell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | 100/100 | 0.360 [0.27, 0.46] | 0.472 | 0.412 | 1.000 | 0.953 | 0.953 | 1.000 | 1.000 | 1.000 | 3293 | 0.00000 |
| `pragmatic_oracle` | 100/100 | 0.440 [0.35, 0.54] | 0.521 | 1.000 | 1.000 | 0.973 | 0.973 | 0.600 | 0.600 | 0.000 | 1190 | 0.00000 |
| `scaffolded_oracle` | 100/100 | 0.880 [0.80, 0.93] | 0.961 | 0.880 | 1.000 | 0.963 | 0.963 | 1.000 | 1.000 | 0.500 | 1524 | 0.00000 |
| `prism_full` | 100/100 | 0.780 [0.69, 0.85] | 0.918 | 0.677 | 0.963 | 0.943 | 0.943 | 0.850 | 0.790 | 0.500 | 2331 | 0.00000 |
| `ablation_lexical_anchors` | 100/100 | 0.730 [0.64, 0.81] | 0.864 | 0.526 | 0.625 | 0.898 | 0.623 | 0.775 | 0.750 | 0.500 | 1560 | 0.00000 |
| `ablation_signature_only` | 100/100 | 0.810 [0.72, 0.87] | 0.938 | 0.675 | 0.983 | 0.945 | 0.945 | 0.890 | 0.850 | 0.510 | 2116 | 0.00000 |
| `ablation_no_purity` | 100/100 | 0.790 [0.70, 0.86] | 0.928 | 0.675 | 0.959 | 0.942 | 0.942 | 0.870 | 0.810 | 0.500 | 2333 | 0.00000 |
| `prism_plus_distractors` | 100/100 | 0.730 [0.64, 0.81] | 0.883 | 0.206 | 0.963 | 0.938 | 0.938 | 0.850 | 0.790 | 0.510 | 4014 | 0.00000 |

| arm | Turn-1 parse fail | answer parse fail | anchor = task seed | seed-varying tasks | Turn-1 reused | distractors in ctx / named | mean latency (s) | calls | cost ($) |
|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | n/a | 0.000 | 1.000 | 7/20 | 0 | - | 8.66 | 100 | 0.0000 |
| `pragmatic_oracle` | n/a | 0.000 | 1.000 | 4/20 | 0 | - | 7.20 | 100 | 0.0000 |
| `scaffolded_oracle` | n/a | 0.010 | 1.000 | 4/20 | 0 | - | 7.33 | 100 | 0.0000 |
| `prism_full` | 0.000 | 0.000 | 1.000 | 5/20 | 0 | - | 16.54 | 250 | 0.0000 |
| `ablation_lexical_anchors` | 0.000 | 0.000 | 0.400 | 1/20 | 0 | - | 15.26 | 250 | 0.0000 |
| `ablation_signature_only` | 0.000 | 0.000 | 1.000 | 4/20 | 0 | - | 16.68 | 250 | 0.0000 |
| `ablation_no_purity` | 0.000 | 0.000 | 1.000 | 5/20 | 0 | - | 16.67 | 250 | 0.0000 |
| `prism_plus_distractors` | 0.000 | 0.000 | 1.000 | 6/20 | 100 | 8.0 / 0.03 | 11.97 | 150 | 0.0000 |

Paired contrasts (same task and seed; A - B):

| A | B | what it isolates | pairs | ΔTSR (95% CI) | Δcleanliness |
|---|---|---|---|---|---|
| `scaffolded_oracle` | `pragmatic_oracle` | scaffolding effect (cleanliness paradox) | 100 | +0.440 [+0.320, +0.550] | -0.120 |
| `prism_full` | `baseline_bfs_bidirectional` | PRISM vs floor | 100 | +0.420 [+0.290, +0.540] | +0.265 |
| `prism_full` | `ablation_lexical_anchors` | taxonomy vs lexical anchors | 100 | +0.050 [-0.030, +0.140] | +0.151 |
| `prism_full` | `ablation_signature_only` | 4-axis annotations vs signatures only | 100 | -0.030 [-0.090, +0.020] | +0.002 |
| `prism_full` | `ablation_no_purity` | purity axis | 100 | -0.010 [-0.070, +0.050] | +0.002 |
| `prism_full` | `prism_plus_distractors` | no noise vs injected distractors | 100 | +0.050 [-0.020, +0.120] | +0.470 |

Served models: {'qwen2.5-coder:7b-instruct-q8_0': 1450}
System fingerprints: {'fp_ollama': 1450}
Tasks whose Turn-1 manifest hash varied across seeds (should be none): none

Cost: $0.0000 over 1450 LLM calls (2,533,153 prompt + 132,278 completion tokens).
Full-sweep projection: 7,200 cells (8 arms x 900) ≈ $0.00 - this run's mean cost per ok cell, per arm (repo mix differs - Django/FastAPI contexts are larger).
