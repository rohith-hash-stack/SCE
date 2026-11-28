# Final sweep analysis: qwen2.5-coder:7b-instruct-q8_0

6,400 scored cells; 80 tasks across django, express, fastapi, trpc; seeds [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]. Intervals: 95% stratified cluster bootstrap over tasks (seeds of a task stay together), 4,000 resamples. Pooled means weight every task equally.

| arm | cells | TSR (95% CI) | django | express | fastapi | trpc | cleanliness | sufficiency | ctx tokens |
|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | 800 | 0.690 [0.603, 0.773] | 0.655 | 0.640 | 0.780 | 0.648 | 0.376 | 0.405 | 5545 |
| `pragmatic_oracle` | 800 | 0.787 [0.711, 0.859] | 0.835 | 0.810 | 0.836 | 0.692 | 1.000 | 0.125 | 1944 |
| `scaffolded_oracle` | 800 | 0.754 [0.675, 0.825] | 0.750 | 0.810 | 0.832 | 0.656 | 0.735 | 0.997 | 2837 |
| `prism_full` | 800 | 0.732 [0.651, 0.807] | 0.740 | 0.590 | 0.824 | 0.692 | 0.728 | 0.396 | 3691 |
| `ablation_lexical_anchors` | 800 | 0.581 [0.485, 0.675] | 0.515 | 0.580 | 0.644 | 0.572 | 0.515 | 0.334 | 3294 |
| `ablation_signature_only` | 800 | 0.754 [0.676, 0.823] | 0.725 | 0.670 | 0.872 | 0.692 | 0.712 | 0.414 | 3437 |
| `ablation_no_purity` | 800 | 0.731 [0.654, 0.806] | 0.710 | 0.640 | 0.852 | 0.664 | 0.718 | 0.402 | 3675 |
| `prism_plus_distractors` | 800 | 0.724 [0.645, 0.800] | 0.695 | 0.610 | 0.804 | 0.712 | 0.288 | 0.396 | 5365 |

Paired contrasts (A - B, same task and seed):

| A | B | isolates | ΔTSR (95% CI) | django | express | fastapi | trpc | Δcleanliness |
|---|---|---|---|---|---|---|---|---|
| `scaffolded_oracle` | `pragmatic_oracle` | scaffolding effect (cleanliness paradox) | -0.034 [-0.074, +0.003] | -0.085 | -0.000 | -0.004 | -0.036 | -0.265 |
| `prism_full` | `baseline_bfs_bidirectional` | PRISM vs floor | +0.042 [-0.004, +0.090] | +0.085 | -0.050 | +0.044 | +0.044 | +0.352 |
| `prism_full` | `ablation_lexical_anchors` | taxonomy vs lexical anchors | +0.151 [+0.079, +0.225] * | +0.225 | +0.010 | +0.180 | +0.120 | +0.213 |
| `prism_full` | `ablation_signature_only` | 4-axis annotations vs signatures only | -0.021 [-0.045, +0.001] | +0.015 | -0.080 | -0.048 | +0.000 | +0.016 |
| `prism_full` | `ablation_no_purity` | purity axis | +0.001 [-0.025, +0.028] | +0.030 | -0.050 | -0.028 | +0.028 | +0.010 |
| `prism_full` | `prism_plus_distractors` | no noise vs injected distractors | +0.009 [-0.024, +0.040] | +0.045 | -0.020 | +0.020 | -0.020 | +0.440 |

`*` = interval excludes zero.
