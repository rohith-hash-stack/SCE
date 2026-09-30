# Final sweep summary (final-sweep-8arms/1.1)

Cells: 2000 (ok=2000, error=0, dry_run=0); schema-invalid records: 0

| arm | ok/n | TSR (95% CI) | TSR partial | cleanliness | CPI ctx | CPI answer | CPI e2e | suff. ratio | sufficient | truncated | ctx tokens | $/cell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | 250/250 | 0.644 [0.58, 0.70] | 0.887 | 0.369 | 1.000 | 0.907 | 0.907 | 0.388 | 0.280 | 0.760 | 6377 | 0.00091 |
| `pragmatic_oracle` | 250/250 | 0.764 [0.71, 0.81] | 0.935 | 1.000 | 1.000 | 0.975 | 0.975 | 0.160 | 0.160 | 0.000 | 2256 | 0.00040 |
| `scaffolded_oracle` | 250/250 | 0.712 [0.65, 0.76] | 0.925 | 0.693 | 1.000 | 0.951 | 0.951 | 1.000 | 1.000 | 0.000 | 3018 | 0.00050 |
| `prism_full` | 250/250 | 0.724 [0.67, 0.78] | 0.923 | 0.765 | 0.986 | 0.955 | 0.955 | 0.219 | 0.204 | 0.012 | 3609 | 0.00080 |
| `ablation_lexical_anchors` | 250/250 | 0.628 [0.57, 0.69] | 0.866 | 0.694 | 0.897 | 0.884 | 0.869 | 0.192 | 0.172 | 0.036 | 3713 | 0.00081 |
| `ablation_signature_only` | 250/250 | 0.796 [0.74, 0.84] | 0.941 | 0.774 | 0.994 | 0.973 | 0.973 | 0.225 | 0.204 | 0.032 | 3222 | 0.00069 |
| `ablation_no_purity` | 250/250 | 0.728 [0.67, 0.78] | 0.925 | 0.781 | 0.985 | 0.956 | 0.956 | 0.221 | 0.208 | 0.024 | 3508 | 0.00077 |
| `prism_plus_distractors` | 250/250 | 0.688 [0.63, 0.74] | 0.907 | 0.272 | 0.986 | 0.932 | 0.932 | 0.219 | 0.204 | 0.044 | 6009 | 0.00086 |

| arm | Turn-1 parse fail | answer parse fail | anchor = task seed | seed-varying tasks | Turn-1 reused | distractors in ctx / named | mean latency (s) | calls | cost ($) |
|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | n/a | 0.000 | 1.000 | 11/25 | 0 | - | 2.01 | 250 | 0.2277 |
| `pragmatic_oracle` | n/a | 0.000 | 1.000 | 5/25 | 0 | - | 1.86 | 250 | 0.1007 |
| `scaffolded_oracle` | n/a | 0.000 | 1.000 | 8/25 | 0 | - | 1.93 | 250 | 0.1239 |
| `prism_full` | 0.000 | 0.000 | 1.000 | 11/25 | 0 | - | 3.69 | 500 | 0.1996 |
| `ablation_lexical_anchors` | 0.000 | 0.000 | 0.720 | 6/25 | 0 | - | 3.64 | 500 | 0.2032 |
| `ablation_signature_only` | 0.000 | 0.000 | 1.000 | 10/25 | 0 | - | 3.70 | 500 | 0.1737 |
| `ablation_no_purity` | 0.000 | 0.000 | 1.000 | 9/25 | 0 | - | 3.81 | 500 | 0.1929 |
| `prism_plus_distractors` | 0.000 | 0.000 | 1.000 | 12/25 | 250 | 7.8 / 0.04 | 2.09 | 250 | 0.2155 |

Paired contrasts (same task and seed; A - B):

| A | B | what it isolates | pairs | ΔTSR (95% CI) | Δcleanliness |
|---|---|---|---|---|---|
| `scaffolded_oracle` | `pragmatic_oracle` | scaffolding effect (cleanliness paradox) | 250 | -0.052 [-0.104, +0.000] | -0.307 |
| `prism_full` | `baseline_bfs_bidirectional` | PRISM vs floor | 250 | +0.080 [+0.016, +0.144] | +0.396 |
| `prism_full` | `ablation_lexical_anchors` | taxonomy vs lexical anchors | 250 | +0.096 [+0.044, +0.148] | +0.072 |
| `prism_full` | `ablation_signature_only` | 4-axis annotations vs signatures only | 250 | -0.072 [-0.120, -0.028] | -0.009 |
| `prism_full` | `ablation_no_purity` | purity axis | 250 | -0.004 [-0.052, +0.044] | -0.016 |
| `prism_full` | `prism_plus_distractors` | no noise vs injected distractors | 250 | +0.036 [-0.008, +0.080] | +0.494 |

Served models: {'gpt-4o-mini-2024-07-18': 3000}
System fingerprints: {'fp_23703e8e1f': 138, 'fp_8ef2fa014c': 44, 'fp_d9f006541a': 76, 'fp_b99562ea4c': 172, 'fp_359379690f': 63, 'fp_10a9fd5a82': 292, 'fp_0c03eba41c': 67, 'fp_8c12b002a5': 115, 'fp_8223f08ec3': 74, 'fp_5a9d97c6db': 286, 'fp_01c4d84367': 31, 'fp_9262d56da7': 3, 'fp_2e30314e0f': 30, 'fp_1e58d0ce9b': 82, 'fp_0b9898f509': 22, 'fp_c77bee8c7a': 109, 'fp_2a9610bf9f': 55, 'fp_bae4e8e4a7': 64, 'fp_6054aa6593': 72, 'fp_f70887b4d3': 42, 'fp_682e5ceb08': 50, 'fp_48e3e83ec9': 399, 'fp_8c9579f592': 20, 'fp_fd0531e57c': 80, 'fp_30d6d650fd': 20, 'fp_f695c34277': 53, 'fp_915ca29da6': 30, 'fp_e68db64f1a': 54, 'fp_894e7baa82': 33, 'fp_a8bfe22efe': 58, 'fp_6a65994342': 60, 'fp_55929dd0d0': 49, 'fp_fe2337fb29': 61, 'fp_b35aefdf58': 34, 'fp_28f3a9c586': 46, 'fp_5d3ba5542b': 10, 'fp_56fbb242c4': 30, 'fp_f51aa69871': 35, 'fp_e351b00da9': 10, 'fp_a06344dfc4': 14, 'fp_edb0948d4f': 9, 'fp_d87bd7d799': 8}
Tasks whose Turn-1 manifest hash varied across seeds (should be none): none

Cost: $1.4371 over 3000 LLM calls (8,118,933 prompt + 365,550 completion tokens).
Full-sweep projection: 7,200 cells (8 arms x 900) ≈ $5.17 - this run's mean cost per ok cell, per arm (repo mix differs - Django/FastAPI contexts are larger).
