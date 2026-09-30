# Final sweep summary (final-sweep-8arms/1.0)

Cells: 400 (ok=400, error=0, dry_run=0); schema-invalid records: 0

| arm | ok/n | TSR (95% CI) | TSR partial | cleanliness | CPI ctx | CPI answer | CPI e2e | suff. ratio | sufficient | truncated | ctx tokens | $/cell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | 50/50 | 0.580 [0.44, 0.71] | 0.877 | 0.369 | 1.000 | 0.892 | 0.892 | 0.388 | 0.280 | 0.760 | 6377 | 0.00091 |
| `pragmatic_oracle` | 50/50 | 0.740 [0.60, 0.84] | 0.932 | 1.000 | 1.000 | 0.978 | 0.978 | 0.160 | 0.160 | 0.000 | 2256 | 0.00040 |
| `scaffolded_oracle` | 50/50 | 0.640 [0.50, 0.76] | 0.905 | 0.693 | 1.000 | 0.937 | 0.937 | 1.000 | 1.000 | 0.000 | 3018 | 0.00049 |
| `prism_full` | 50/50 | 0.800 [0.67, 0.89] | 0.938 | 0.776 | 0.990 | 0.957 | 0.957 | 0.229 | 0.200 | 0.040 | 3540 | 0.00079 |
| `ablation_lexical_anchors` | 50/50 | 0.660 [0.52, 0.78] | 0.868 | 0.671 | 0.893 | 0.886 | 0.873 | 0.192 | 0.160 | 0.080 | 3778 | 0.00082 |
| `ablation_signature_only` | 50/50 | 0.840 [0.71, 0.92] | 0.952 | 0.773 | 0.995 | 0.985 | 0.985 | 0.217 | 0.200 | 0.020 | 3237 | 0.00070 |
| `ablation_no_purity` | 50/50 | 0.760 [0.63, 0.86] | 0.930 | 0.783 | 0.977 | 0.952 | 0.952 | 0.212 | 0.200 | 0.000 | 3494 | 0.00077 |
| `prism_plus_distractors` | 50/50 | 0.700 [0.56, 0.81] | 0.910 | 0.273 | 0.990 | 0.920 | 0.920 | 0.229 | 0.200 | 0.040 | 5911 | 0.00085 |

| arm | Turn-1 parse fail | answer parse fail | anchor = task seed | seed-varying tasks | Turn-1 reused | distractors in ctx / named | mean latency (s) | calls | cost ($) |
|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | n/a | 0.000 | 1.000 | 3/25 | 0 | - | 4.39 | 50 | 0.0455 |
| `pragmatic_oracle` | n/a | 0.000 | 1.000 | 1/25 | 0 | - | 4.84 | 50 | 0.0201 |
| `scaffolded_oracle` | n/a | 0.000 | 1.000 | 2/25 | 0 | - | 3.01 | 50 | 0.0247 |
| `prism_full` | 0.000 | 0.000 | 1.000 | 2/25 | 0 | - | 5.04 | 100 | 0.0396 |
| `ablation_lexical_anchors` | 0.000 | 0.000 | 0.720 | 3/25 | 0 | - | 6.25 | 100 | 0.0410 |
| `ablation_signature_only` | 0.000 | 0.000 | 1.000 | 2/25 | 0 | - | 5.50 | 100 | 0.0349 |
| `ablation_no_purity` | 0.000 | 0.000 | 1.000 | 0/25 | 0 | - | 4.89 | 100 | 0.0385 |
| `prism_plus_distractors` | 0.000 | 0.000 | 1.000 | 3/25 | 50 | 7.7 / 0.04 | 7.64 | 50 | 0.0424 |

Paired contrasts (same task and seed; A - B):

| A | B | what it isolates | pairs | ΔTSR (95% CI) | Δcleanliness |
|---|---|---|---|---|---|
| `scaffolded_oracle` | `pragmatic_oracle` | scaffolding effect (cleanliness paradox) | 50 | -0.100 [-0.200, +0.000] | -0.307 |
| `prism_full` | `baseline_bfs_bidirectional` | PRISM vs floor | 50 | +0.220 [+0.120, +0.340] | +0.407 |
| `prism_full` | `ablation_lexical_anchors` | taxonomy vs lexical anchors | 50 | +0.140 [+0.020, +0.260] | +0.105 |
| `prism_full` | `ablation_signature_only` | 4-axis annotations vs signatures only | 50 | -0.040 [-0.140, +0.060] | +0.003 |
| `prism_full` | `ablation_no_purity` | purity axis | 50 | +0.040 [-0.060, +0.140] | -0.007 |
| `prism_full` | `prism_plus_distractors` | no noise vs injected distractors | 50 | +0.100 [+0.000, +0.200] | +0.502 |

Served models: {'gpt-4o-mini-2024-07-18': 600}
System fingerprints: {'fp_8223f08ec3': 14, 'fp_0b9898f509': 4, 'fp_fd0531e57c': 16, 'fp_23703e8e1f': 27, 'fp_bae4e8e4a7': 9, 'fp_30d6d650fd': 5, 'fp_c77bee8c7a': 22, 'fp_8ef2fa014c': 9, 'fp_f695c34277': 9, 'fp_f70887b4d3': 8, 'fp_d9f006541a': 19, 'fp_b99562ea4c': 109, 'fp_8c12b002a5': 21, 'fp_1e58d0ce9b': 15, 'fp_0c03eba41c': 15, 'fp_48e3e83ec9': 4, 'fp_2a9610bf9f': 12, 'fp_10a9fd5a82': 59, 'fp_e68db64f1a': 14, 'fp_5a9d97c6db': 57, 'fp_8c9579f592': 3, 'fp_894e7baa82': 6, 'fp_6054aa6593': 19, 'fp_9262d56da7': 2, 'fp_359379690f': 13, 'fp_01c4d84367': 6, 'fp_a8bfe22efe': 13, 'fp_6a65994342': 12, 'fp_55929dd0d0': 10, 'fp_915ca29da6': 4, 'fp_682e5ceb08': 6, 'fp_2e30314e0f': 6, 'fp_5d3ba5542b': 4, 'fp_fe2337fb29': 10, 'fp_b35aefdf58': 9, 'fp_56fbb242c4': 7, 'fp_f51aa69871': 7, 'fp_e351b00da9': 2, 'fp_28f3a9c586': 7, 'fp_a06344dfc4': 2, 'fp_edb0948d4f': 2, 'fp_d87bd7d799': 2}
Tasks whose Turn-1 manifest hash varied across seeds (should be none): none

Cost: $0.2866 over 600 LLM calls (1,618,285 prompt + 73,183 completion tokens).
Full-sweep projection: 7,200 cells (8 arms x 900) ≈ $5.16 - this run's mean cost per ok cell, per arm (repo mix differs - Django/FastAPI contexts are larger).
