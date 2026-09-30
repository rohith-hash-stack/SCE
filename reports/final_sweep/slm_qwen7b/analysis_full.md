# Final sweep analysis: qwen2.5-coder:7b-instruct-q8_0

7,200 scored cells; 90 tasks across django, express, fastapi, trpc; seeds [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]. Intervals: 95% stratified cluster bootstrap over tasks (seeds of a task stay together), 4,000 resamples. Pooled means weight every task equally.

| arm | cells | TSR (95% CI) | django | express | fastapi | trpc | cleanliness | sufficiency | ctx tokens |
|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | 900 | 0.626 [0.542, 0.703] | 0.655 | 0.375 | 0.780 | 0.648 | 0.388 | 0.471 | 5099 |
| `pragmatic_oracle` | 900 | 0.706 [0.629, 0.779] | 0.835 | 0.430 | 0.836 | 0.692 | 1.000 | 0.200 | 1808 |
| `scaffolded_oracle` | 900 | 0.778 [0.708, 0.841] | 0.750 | 0.890 | 0.832 | 0.656 | 0.758 | 0.997 | 2634 |
| `prism_full` | 900 | 0.759 [0.687, 0.827] | 0.740 | 0.780 | 0.824 | 0.692 | 0.724 | 0.463 | 3467 |
| `ablation_lexical_anchors` | 900 | 0.617 [0.523, 0.703] | 0.515 | 0.740 | 0.644 | 0.572 | 0.531 | 0.397 | 3079 |
| `ablation_signature_only` | 900 | 0.777 [0.707, 0.841] | 0.725 | 0.815 | 0.872 | 0.692 | 0.710 | 0.479 | 3214 |
| `ablation_no_purity` | 900 | 0.759 [0.687, 0.827] | 0.710 | 0.810 | 0.852 | 0.664 | 0.714 | 0.468 | 3459 |
| `prism_plus_distractors` | 900 | 0.743 [0.669, 0.811] | 0.695 | 0.755 | 0.804 | 0.712 | 0.278 | 0.463 | 5145 |

Paired contrasts (A - B, same task and seed):

| A | B | isolates | ΔTSR (95% CI) | django | express | fastapi | trpc | Δcleanliness |
|---|---|---|---|---|---|---|---|---|
| `scaffolded_oracle` | `pragmatic_oracle` | scaffolding effect (cleanliness paradox) | +0.072 [+0.013, +0.129] * | -0.085 | +0.460 | -0.004 | -0.036 | -0.242 |
| `prism_full` | `baseline_bfs_bidirectional` | PRISM vs floor | +0.133 [+0.069, +0.200] * | +0.085 | +0.405 | +0.044 | +0.044 | +0.336 |
| `prism_full` | `ablation_lexical_anchors` | taxonomy vs lexical anchors | +0.142 [+0.078, +0.213] * | +0.225 | +0.040 | +0.180 | +0.120 | +0.192 |
| `prism_full` | `ablation_signature_only` | 4-axis annotations vs signatures only | -0.018 [-0.040, +0.002] | +0.015 | -0.035 | -0.048 | +0.000 | +0.014 |
| `prism_full` | `ablation_no_purity` | purity axis | -0.000 [-0.023, +0.024] | +0.030 | -0.030 | -0.028 | +0.028 | +0.009 |
| `prism_full` | `prism_plus_distractors` | no noise vs injected distractors | +0.016 [-0.017, +0.047] | +0.045 | +0.025 | +0.020 | -0.020 | +0.446 |

`*` = interval excludes zero.
