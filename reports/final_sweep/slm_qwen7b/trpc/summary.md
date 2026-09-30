# Final sweep summary (final-sweep-8arms/1.1)

Cells: 1000 (ok=1000, error=0, dry_run=0); schema-invalid records: 0

| arm | ok/n | TSR (95% CI) | TSR partial | cleanliness | CPI ctx | CPI answer | CPI e2e | suff. ratio | sufficient | truncated | ctx tokens | $/cell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | 125/125 | 0.640 [0.55, 0.72] | 0.899 | 0.345 | 1.000 | 0.931 | 0.931 | 0.388 | 0.280 | 0.720 | 5796 | 0.00000 |
| `pragmatic_oracle` | 125/125 | 0.696 [0.61, 0.77] | 0.911 | 1.000 | 1.000 | 0.947 | 0.947 | 0.160 | 0.160 | 0.000 | 1885 | 0.00000 |
| `scaffolded_oracle` | 125/125 | 0.648 [0.56, 0.73] | 0.898 | 0.693 | 1.000 | 0.934 | 0.934 | 1.000 | 1.000 | 0.000 | 2505 | 0.00000 |
| `prism_full` | 125/125 | 0.680 [0.59, 0.76] | 0.906 | 0.806 | 0.991 | 0.928 | 0.928 | 0.211 | 0.200 | 0.000 | 2867 | 0.00000 |
| `ablation_lexical_anchors` | 125/125 | 0.568 [0.48, 0.65] | 0.852 | 0.728 | 0.893 | 0.865 | 0.828 | 0.183 | 0.168 | 0.024 | 2773 | 0.00000 |
| `ablation_signature_only` | 125/125 | 0.720 [0.64, 0.79] | 0.911 | 0.778 | 0.996 | 0.940 | 0.940 | 0.241 | 0.216 | 0.000 | 2635 | 0.00000 |
| `ablation_no_purity` | 125/125 | 0.688 [0.60, 0.76] | 0.913 | 0.804 | 0.995 | 0.930 | 0.930 | 0.211 | 0.184 | 0.000 | 2871 | 0.00000 |
| `prism_plus_distractors` | 125/125 | 0.696 [0.61, 0.77] | 0.901 | 0.272 | 0.991 | 0.922 | 0.922 | 0.211 | 0.200 | 0.016 | 4854 | 0.00000 |

| arm | Turn-1 parse fail | answer parse fail | anchor = task seed | seed-varying tasks | Turn-1 reused | distractors in ctx / named | mean latency (s) | calls | cost ($) |
|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | n/a | 0.000 | 1.000 | 5/25 | 0 | - | 12.28 | 125 | 0.0000 |
| `pragmatic_oracle` | n/a | 0.000 | 1.000 | 6/25 | 0 | - | 8.82 | 125 | 0.0000 |
| `scaffolded_oracle` | n/a | 0.000 | 1.000 | 8/25 | 0 | - | 9.72 | 125 | 0.0000 |
| `prism_full` | 0.000 | 0.000 | 1.000 | 10/25 | 0 | - | 17.81 | 250 | 0.0000 |
| `ablation_lexical_anchors` | 0.000 | 0.000 | 0.720 | 6/25 | 0 | - | 18.78 | 250 | 0.0000 |
| `ablation_signature_only` | 0.000 | 0.000 | 1.000 | 9/25 | 0 | - | 19.01 | 250 | 0.0000 |
| `ablation_no_purity` | 0.000 | 0.000 | 1.000 | 9/25 | 0 | - | 17.83 | 250 | 0.0000 |
| `prism_plus_distractors` | 0.000 | 0.000 | 1.000 | 12/25 | 125 | 8.0 / 0.04 | 11.64 | 125 | 0.0000 |

Paired contrasts (same task and seed; A - B):

| A | B | what it isolates | pairs | ΔTSR (95% CI) | Δcleanliness |
|---|---|---|---|---|---|
| `scaffolded_oracle` | `pragmatic_oracle` | scaffolding effect (cleanliness paradox) | 125 | -0.048 [-0.120, +0.024] | -0.307 |
| `prism_full` | `baseline_bfs_bidirectional` | PRISM vs floor | 125 | +0.040 [-0.032, +0.112] | +0.461 |
| `prism_full` | `ablation_lexical_anchors` | taxonomy vs lexical anchors | 125 | +0.112 [+0.032, +0.192] | +0.077 |
| `prism_full` | `ablation_signature_only` | 4-axis annotations vs signatures only | 125 | -0.040 [-0.112, +0.040] | +0.028 |
| `prism_full` | `ablation_no_purity` | purity axis | 125 | -0.008 [-0.080, +0.072] | +0.002 |
| `prism_full` | `prism_plus_distractors` | no noise vs injected distractors | 125 | -0.016 [-0.088, +0.048] | +0.534 |

Served models: {'qwen2.5-coder:7b-instruct-q8_0': 1500}
System fingerprints: {'fp_ollama': 1500}
Tasks whose Turn-1 manifest hash varied across seeds (should be none): none

Cost: $0.0000 over 1500 LLM calls (4,117,325 prompt + 170,810 completion tokens).
Full-sweep projection: 7,200 cells (8 arms x 900) ≈ $0.00 - this run's mean cost per ok cell, per arm (repo mix differs - Django/FastAPI contexts are larger).
