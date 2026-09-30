# Final sweep summary (final-sweep-8arms/1.1)

Cells: 800 (ok=800, error=0, dry_run=0); schema-invalid records: 0

| arm | ok/n | TSR (95% CI) | TSR partial | cleanliness | CPI ctx | CPI answer | CPI e2e | suff. ratio | sufficient | truncated | ctx tokens | $/cell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | 100/100 | 0.640 [0.54, 0.73] | 0.826 | 0.320 | 0.915 | 0.880 | 0.860 | 0.226 | 0.050 | 0.950 | 6299 | 0.00000 |
| `pragmatic_oracle` | 100/100 | 0.850 [0.77, 0.91] | 0.965 | 1.000 | 1.000 | 0.985 | 0.985 | 0.050 | 0.050 | 0.000 | 1819 | 0.00000 |
| `scaffolded_oracle` | 100/100 | 0.760 [0.67, 0.83] | 0.910 | 0.871 | 1.000 | 0.958 | 0.958 | 1.000 | 1.000 | 0.000 | 2742 | 0.00000 |
| `prism_full` | 100/100 | 0.760 [0.67, 0.83] | 0.934 | 0.852 | 0.937 | 0.934 | 0.934 | 0.589 | 0.360 | 0.190 | 4998 | 0.00000 |
| `ablation_lexical_anchors` | 100/100 | 0.490 [0.39, 0.59] | 0.782 | 0.600 | 0.613 | 0.799 | 0.588 | 0.512 | 0.250 | 0.180 | 4635 | 0.00000 |
| `ablation_signature_only` | 100/100 | 0.720 [0.63, 0.80] | 0.909 | 0.854 | 0.941 | 0.922 | 0.922 | 0.639 | 0.410 | 0.190 | 4580 | 0.00000 |
| `ablation_no_purity` | 100/100 | 0.730 [0.64, 0.81] | 0.917 | 0.844 | 0.940 | 0.920 | 0.920 | 0.604 | 0.360 | 0.200 | 4971 | 0.00000 |
| `prism_plus_distractors` | 100/100 | 0.670 [0.57, 0.75] | 0.899 | 0.416 | 0.937 | 0.910 | 0.910 | 0.589 | 0.360 | 0.390 | 6451 | 0.00000 |

| arm | Turn-1 parse fail | answer parse fail | anchor = task seed | seed-varying tasks | Turn-1 reused | distractors in ctx / named | mean latency (s) | calls | cost ($) |
|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | n/a | 0.010 | 1.000 | 4/20 | 0 | - | 15.54 | 100 | 0.0000 |
| `pragmatic_oracle` | n/a | 0.000 | 1.000 | 5/20 | 0 | - | 10.06 | 100 | 0.0000 |
| `scaffolded_oracle` | n/a | 0.000 | 1.000 | 8/20 | 0 | - | 11.00 | 100 | 0.0000 |
| `prism_full` | 0.000 | 0.000 | 1.000 | 4/20 | 0 | - | 23.02 | 200 | 0.0000 |
| `ablation_lexical_anchors` | 0.010 | 0.000 | 0.450 | 5/20 | 0 | - | 22.91 | 200 | 0.0000 |
| `ablation_signature_only` | 0.020 | 0.000 | 1.000 | 7/20 | 0 | - | 24.40 | 200 | 0.0000 |
| `ablation_no_purity` | 0.030 | 0.010 | 1.000 | 7/20 | 0 | - | 28.50 | 200 | 0.0000 |
| `prism_plus_distractors` | 0.000 | 0.000 | 1.000 | 8/20 | 100 | 6.6 / 0.00 | 14.47 | 100 | 0.0000 |

Paired contrasts (same task and seed; A - B):

| A | B | what it isolates | pairs | ΔTSR (95% CI) | Δcleanliness |
|---|---|---|---|---|---|
| `scaffolded_oracle` | `pragmatic_oracle` | scaffolding effect (cleanliness paradox) | 100 | -0.090 [-0.180, -0.010] | -0.129 |
| `prism_full` | `baseline_bfs_bidirectional` | PRISM vs floor | 100 | +0.120 [+0.030, +0.210] | +0.532 |
| `prism_full` | `ablation_lexical_anchors` | taxonomy vs lexical anchors | 100 | +0.270 [+0.180, +0.360] | +0.252 |
| `prism_full` | `ablation_signature_only` | 4-axis annotations vs signatures only | 100 | +0.040 [-0.020, +0.110] | -0.002 |
| `prism_full` | `ablation_no_purity` | purity axis | 100 | +0.030 [-0.030, +0.090] | +0.007 |
| `prism_full` | `prism_plus_distractors` | no noise vs injected distractors | 100 | +0.090 [+0.030, +0.150] | +0.436 |

Served models: {'qwen2.5-coder:7b-instruct-q8_0': 1200}
System fingerprints: {'fp_ollama': 1200}
Tasks whose Turn-1 manifest hash varied across seeds (should be none): none

Cost: $0.0000 over 1200 LLM calls (4,574,533 prompt + 165,260 completion tokens).
Full-sweep projection: 7,200 cells (8 arms x 900) ≈ $0.00 - this run's mean cost per ok cell, per arm (repo mix differs - Django/FastAPI contexts are larger).
