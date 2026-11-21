# Final sweep summary (final-sweep-8arms/1.1)

Cells: 2000 (ok=2000, error=0, dry_run=0); schema-invalid records: 0

| arm | ok/n | TSR (95% CI) | TSR partial | cleanliness | CPI ctx | CPI answer | CPI e2e | suff. ratio | sufficient | truncated | ctx tokens | $/cell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | 250/250 | 0.648 [0.59, 0.70] | 0.898 | 0.345 | 1.000 | 0.935 | 0.935 | 0.388 | 0.280 | 0.720 | 5796 | 0.00000 |
| `pragmatic_oracle` | 250/250 | 0.692 [0.63, 0.75] | 0.919 | 1.000 | 1.000 | 0.946 | 0.946 | 0.160 | 0.160 | 0.000 | 1885 | 0.00000 |
| `scaffolded_oracle` | 250/250 | 0.656 [0.60, 0.71] | 0.906 | 0.693 | 1.000 | 0.934 | 0.934 | 1.000 | 1.000 | 0.000 | 2505 | 0.00000 |
| `prism_full` | 250/250 | 0.692 [0.63, 0.75] | 0.911 | 0.789 | 0.995 | 0.936 | 0.936 | 0.219 | 0.196 | 0.000 | 2942 | 0.00000 |
| `ablation_lexical_anchors` | 250/250 | 0.572 [0.51, 0.63] | 0.854 | 0.724 | 0.898 | 0.870 | 0.835 | 0.184 | 0.168 | 0.028 | 2800 | 0.00000 |
| `ablation_signature_only` | 250/250 | 0.692 [0.63, 0.75] | 0.904 | 0.771 | 0.996 | 0.936 | 0.936 | 0.235 | 0.212 | 0.000 | 2639 | 0.00000 |
| `ablation_no_purity` | 250/250 | 0.664 [0.60, 0.72] | 0.905 | 0.790 | 0.995 | 0.927 | 0.927 | 0.217 | 0.188 | 0.000 | 2922 | 0.00000 |
| `prism_plus_distractors` | 250/250 | 0.712 [0.65, 0.76] | 0.907 | 0.271 | 0.995 | 0.929 | 0.929 | 0.219 | 0.196 | 0.020 | 4919 | 0.00000 |

| arm | Turn-1 parse fail | answer parse fail | anchor = task seed | seed-varying tasks | Turn-1 reused | distractors in ctx / named | mean latency (s) | calls | cost ($) |
|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | n/a | 0.000 | 1.000 | 11/25 | 0 | - | 12.32 | 250 | 0.0000 |
| `pragmatic_oracle` | n/a | 0.000 | 1.000 | 7/25 | 0 | - | 8.79 | 250 | 0.0000 |
| `scaffolded_oracle` | n/a | 0.000 | 1.000 | 11/25 | 0 | - | 9.37 | 250 | 0.0000 |
| `prism_full` | 0.000 | 0.000 | 1.000 | 12/25 | 0 | - | 17.62 | 500 | 0.0000 |
| `ablation_lexical_anchors` | 0.000 | 0.000 | 0.720 | 9/25 | 0 | - | 17.27 | 500 | 0.0000 |
| `ablation_signature_only` | 0.000 | 0.000 | 1.000 | 12/25 | 0 | - | 18.21 | 500 | 0.0000 |
| `ablation_no_purity` | 0.000 | 0.000 | 1.000 | 9/25 | 0 | - | 17.65 | 500 | 0.0000 |
| `prism_plus_distractors` | 0.000 | 0.000 | 1.000 | 13/25 | 250 | 7.9 / 0.04 | 11.71 | 250 | 0.0000 |

Paired contrasts (same task and seed; A - B):

| A | B | what it isolates | pairs | ΔTSR (95% CI) | Δcleanliness |
|---|---|---|---|---|---|
| `scaffolded_oracle` | `pragmatic_oracle` | scaffolding effect (cleanliness paradox) | 250 | -0.036 [-0.084, +0.012] | -0.307 |
| `prism_full` | `baseline_bfs_bidirectional` | PRISM vs floor | 250 | +0.044 [-0.004, +0.096] | +0.444 |
| `prism_full` | `ablation_lexical_anchors` | taxonomy vs lexical anchors | 250 | +0.120 [+0.064, +0.176] | +0.065 |
| `prism_full` | `ablation_signature_only` | 4-axis annotations vs signatures only | 250 | +0.000 [-0.048, +0.052] | +0.018 |
| `prism_full` | `ablation_no_purity` | purity axis | 250 | +0.028 [-0.024, +0.084] | -0.000 |
| `prism_full` | `prism_plus_distractors` | no noise vs injected distractors | 250 | -0.020 [-0.072, +0.032] | +0.518 |

Served models: {'qwen2.5-coder:7b-instruct-q8_0': 3000}
System fingerprints: {'fp_ollama': 3000}
Tasks whose Turn-1 manifest hash varied across seeds (should be none): none

Cost: $0.0000 over 3000 LLM calls (8,291,844 prompt + 345,098 completion tokens).
Full-sweep projection: 7,200 cells (8 arms x 900) ≈ $0.00 - this run's mean cost per ok cell, per arm (repo mix differs - Django/FastAPI contexts are larger).
