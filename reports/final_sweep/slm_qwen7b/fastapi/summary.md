# Final sweep summary (final-sweep-8arms/1.1)

Cells: 1000 (ok=1000, error=0, dry_run=0); schema-invalid records: 0

| arm | ok/n | TSR (95% CI) | TSR partial | cleanliness | CPI ctx | CPI answer | CPI e2e | suff. ratio | sufficient | truncated | ctx tokens | $/cell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | 125/125 | 0.792 [0.71, 0.85] | 0.915 | 0.467 | 0.984 | 0.972 | 0.972 | 0.327 | 0.200 | 1.000 | 4886 | 0.00000 |
| `pragmatic_oracle` | 125/125 | 0.824 [0.75, 0.88] | 0.921 | 1.000 | 1.000 | 0.985 | 0.985 | 0.040 | 0.040 | 0.000 | 2215 | 0.00000 |
| `scaffolded_oracle` | 125/125 | 0.832 [0.76, 0.89] | 0.911 | 0.636 | 1.000 | 0.969 | 0.969 | 0.990 | 0.960 | 0.040 | 3566 | 0.00000 |
| `prism_full` | 125/125 | 0.824 [0.75, 0.88] | 0.915 | 0.588 | 0.984 | 0.982 | 0.974 | 0.276 | 0.200 | 0.288 | 3661 | 0.00000 |
| `ablation_lexical_anchors` | 125/125 | 0.648 [0.56, 0.73] | 0.727 | 0.292 | 0.490 | 0.788 | 0.478 | 0.214 | 0.200 | 0.400 | 3268 | 0.00000 |
| `ablation_signature_only` | 125/125 | 0.872 [0.80, 0.92] | 0.942 | 0.557 | 0.994 | 0.988 | 0.988 | 0.290 | 0.200 | 0.272 | 3633 | 0.00000 |
| `ablation_no_purity` | 125/125 | 0.848 [0.77, 0.90] | 0.940 | 0.576 | 0.982 | 0.981 | 0.971 | 0.279 | 0.200 | 0.296 | 3651 | 0.00000 |
| `prism_plus_distractors` | 125/125 | 0.792 [0.71, 0.85] | 0.869 | 0.227 | 0.984 | 0.974 | 0.966 | 0.276 | 0.200 | 0.360 | 5216 | 0.00000 |

| arm | Turn-1 parse fail | answer parse fail | anchor = task seed | seed-varying tasks | Turn-1 reused | distractors in ctx / named | mean latency (s) | calls | cost ($) |
|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | n/a | 0.000 | 1.000 | 4/25 | 0 | - | 10.64 | 125 | 0.0000 |
| `pragmatic_oracle` | n/a | 0.000 | 1.000 | 2/25 | 0 | - | 9.75 | 125 | 0.0000 |
| `scaffolded_oracle` | n/a | 0.000 | 1.000 | 5/25 | 0 | - | 9.98 | 125 | 0.0000 |
| `prism_full` | 0.024 | 0.000 | 1.000 | 7/25 | 0 | - | 21.80 | 250 | 0.0000 |
| `ablation_lexical_anchors` | 0.088 | 0.016 | 0.360 | 4/25 | 0 | - | 27.63 | 250 | 0.0000 |
| `ablation_signature_only` | 0.016 | 0.000 | 1.000 | 5/25 | 0 | - | 22.22 | 250 | 0.0000 |
| `ablation_no_purity` | 0.000 | 0.000 | 1.000 | 5/25 | 0 | - | 19.74 | 250 | 0.0000 |
| `prism_plus_distractors` | 0.024 | 0.000 | 1.000 | 8/25 | 125 | 6.8 / 0.00 | 10.98 | 125 | 0.0000 |

Paired contrasts (same task and seed; A - B):

| A | B | what it isolates | pairs | ΔTSR (95% CI) | Δcleanliness |
|---|---|---|---|---|---|
| `scaffolded_oracle` | `pragmatic_oracle` | scaffolding effect (cleanliness paradox) | 125 | +0.008 [-0.040, +0.056] | -0.364 |
| `prism_full` | `baseline_bfs_bidirectional` | PRISM vs floor | 125 | +0.032 [-0.032, +0.096] | +0.121 |
| `prism_full` | `ablation_lexical_anchors` | taxonomy vs lexical anchors | 125 | +0.176 [+0.104, +0.248] | +0.296 |
| `prism_full` | `ablation_signature_only` | 4-axis annotations vs signatures only | 125 | -0.048 [-0.104, +0.000] | +0.031 |
| `prism_full` | `ablation_no_purity` | purity axis | 125 | -0.024 [-0.080, +0.032] | +0.012 |
| `prism_full` | `prism_plus_distractors` | no noise vs injected distractors | 125 | +0.032 [-0.040, +0.104] | +0.361 |

Served models: {'qwen2.5-coder:7b-instruct-q8_0': 1500}
System fingerprints: {'fp_ollama': 1500}
Tasks whose Turn-1 manifest hash varied across seeds (should be none): none

Cost: $0.0000 over 1500 LLM calls (4,673,910 prompt + 197,122 completion tokens).
Full-sweep projection: 7,200 cells (8 arms x 900) ≈ $0.00 - this run's mean cost per ok cell, per arm (repo mix differs - Django/FastAPI contexts are larger).
