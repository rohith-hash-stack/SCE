# Final sweep analysis: qwen2.5-coder:7b-instruct-q8_0

3,600 scored cells; 90 tasks across django, express, fastapi, trpc; seeds [1, 2, 3, 4, 5]. Intervals: 95% stratified cluster bootstrap over tasks (seeds of a task stay together), 4,000 resamples. Pooled means weight every task equally.

| arm | cells | TSR (95% CI) | django | express | fastapi | trpc | cleanliness | sufficiency | ctx tokens |
|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | 450 | 0.620 [0.533, 0.702] | 0.640 | 0.360 | 0.792 | 0.640 | 0.388 | 0.471 | 5099 |
| `pragmatic_oracle` | 450 | 0.709 [0.629, 0.784] | 0.850 | 0.440 | 0.824 | 0.696 | 1.000 | 0.200 | 1808 |
| `scaffolded_oracle` | 450 | 0.776 [0.704, 0.840] | 0.760 | 0.880 | 0.832 | 0.648 | 0.758 | 0.997 | 2634 |
| `prism_full` | 450 | 0.760 [0.684, 0.831] | 0.760 | 0.780 | 0.824 | 0.680 | 0.727 | 0.455 | 3442 |
| `ablation_lexical_anchors` | 450 | 0.609 [0.516, 0.698] | 0.490 | 0.730 | 0.648 | 0.568 | 0.534 | 0.396 | 3055 |
| `ablation_signature_only` | 450 | 0.782 [0.711, 0.849] | 0.720 | 0.810 | 0.872 | 0.720 | 0.711 | 0.487 | 3229 |
| `ablation_no_purity` | 450 | 0.764 [0.693, 0.833] | 0.730 | 0.790 | 0.848 | 0.688 | 0.721 | 0.464 | 3435 |
| `prism_plus_distractors` | 450 | 0.724 [0.649, 0.793] | 0.670 | 0.730 | 0.792 | 0.696 | 0.277 | 0.455 | 5123 |

Paired contrasts (A - B, same task and seed):

| A | B | isolates | ΔTSR (95% CI) | django | express | fastapi | trpc | Δcleanliness |
|---|---|---|---|---|---|---|---|---|
| `scaffolded_oracle` | `pragmatic_oracle` | scaffolding effect (cleanliness paradox) | +0.067 [+0.007, +0.124] * | -0.090 | +0.440 | +0.008 | -0.048 | -0.242 |
| `prism_full` | `baseline_bfs_bidirectional` | PRISM vs floor | +0.140 [+0.069, +0.216] * | +0.120 | +0.420 | +0.032 | +0.040 | +0.339 |
| `prism_full` | `ablation_lexical_anchors` | taxonomy vs lexical anchors | +0.151 [+0.080, +0.227] * | +0.270 | +0.050 | +0.176 | +0.112 | +0.193 |
| `prism_full` | `ablation_signature_only` | 4-axis annotations vs signatures only | -0.022 [-0.051, +0.007] | +0.040 | -0.030 | -0.048 | -0.040 | +0.016 |
| `prism_full` | `ablation_no_purity` | purity axis | -0.004 [-0.036, +0.029] | +0.030 | -0.010 | -0.024 | -0.008 | +0.006 |
| `prism_full` | `prism_plus_distractors` | no noise vs injected distractors | +0.036 [-0.000, +0.073] | +0.090 | +0.050 | +0.032 | -0.016 | +0.450 |

`*` = interval excludes zero.
