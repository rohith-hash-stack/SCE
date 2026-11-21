# Supplementary: triple scoring of every final-sweep cell

Every scored cell of the 8-arm sweep, re-scored three ways from its saved answer (`answer_response`). Source: `scripts/auditor_recompute.py` → `reports/final_sweep/auditor_numbers.json`.

| Metric | Definition | Code |
|---|---|---|
| **Exact-match** | 1 iff the answer's `symbols` list, compared by bare name, **equals** the ground-truth `pipeline_symbols` list: same members, same order, nothing extra, nothing missing | `benchmarks/tsr/scorer_debug.py::score_debug` |
| **Strict binary TSR** (headline) | 1 iff `score_debug_causal == 1.0`: the whole pipeline appears as an ordered (not necessarily contiguous) subsequence of the answer, **and** every extra named symbol is present in the arm's own context | `score_debug_causal`, thresholded in `benchmarks/final_sweep/runner.py` (`tsr`) |
| **Partial credit** | `score_debug_causal` itself: longest-common-subsequence(pipeline, answer) / len(pipeline), forced to 0 if any named symbol is neither a pipeline stage nor in context | `score_debug_causal` (`tsr_partial`) |

Values are cell means (all 10 seeds for Qwen; gpt-4o-mini covers tRPC fully and Express partially: 933 of 1,600 cells, stopped when OpenAI credits ran out).

## qwen2.5-coder:7b-instruct-q8_0 (7,200 cells, 90 tasks, 10 seeds)

| arm | trpc exact / binary / partial | express exact / binary / partial | fastapi exact / binary / partial | django exact / binary / partial | pooled (cells) exact / binary / partial |
|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | 0.428 / **0.648** / 0.898 | 0.175 / **0.375** / 0.481 | 0.576 / **0.780** / 0.911 | 0.395 / **0.655** / 0.846 | 0.406 / **0.626** / 0.797 |
| `pragmatic_oracle` | 0.692 / **0.692** / 0.919 | 0.390 / **0.430** / 0.515 | 0.820 / **0.836** / 0.926 | 0.835 / **0.835** / 0.962 | 0.692 / **0.706** / 0.840 |
| `scaffolded_oracle` | 0.596 / **0.656** / 0.906 | 0.260 / **0.890** / 0.968 | 0.688 / **0.832** / 0.911 | 0.650 / **0.750** / 0.914 | 0.559 / **0.778** / 0.923 |
| `prism_full` | 0.480 / **0.692** / 0.911 | 0.220 / **0.780** / 0.918 | 0.692 / **0.824** / 0.915 | 0.580 / **0.740** / 0.920 | 0.503 / **0.759** / 0.916 |
| `ablation_lexical_anchors` | 0.408 / **0.572** / 0.854 | 0.185 / **0.740** / 0.863 | 0.520 / **0.644** / 0.744 | 0.375 / **0.515** / 0.782 | 0.382 / **0.617** / 0.809 |
| `ablation_signature_only` | 0.464 / **0.692** / 0.904 | 0.225 / **0.815** / 0.938 | 0.640 / **0.872** / 0.941 | 0.490 / **0.725** / 0.906 | 0.466 / **0.777** / 0.922 |
| `ablation_no_purity` | 0.468 / **0.664** / 0.905 | 0.195 / **0.810** / 0.938 | 0.660 / **0.852** / 0.933 | 0.515 / **0.710** / 0.915 | 0.471 / **0.759** / 0.922 |
| `prism_plus_distractors` | 0.528 / **0.712** / 0.907 | 0.195 / **0.755** / 0.897 | 0.620 / **0.804** / 0.889 | 0.530 / **0.695** / 0.900 | 0.480 / **0.743** / 0.898 |

## gpt-4o-mini-2024-07-18 (tRPC 2,000 cells; Express 933 cells, partial)

| arm | trpc exact / binary / partial | express exact / binary / partial | pooled (cells) exact / binary / partial |
|---|---|---|---|
| `baseline_bfs_bidirectional` | 0.600 / **0.644** / 0.887 | 0.483 / **0.667** / 0.779 | 0.562 / **0.651** / 0.852 |
| `pragmatic_oracle` | 0.764 / **0.764** / 0.935 | 0.597 / **0.597** / 0.775 | 0.710 / **0.710** / 0.884 |
| `scaffolded_oracle` | 0.672 / **0.712** / 0.925 | 0.496 / **0.731** / 0.926 | 0.615 / **0.718** / 0.925 |
| `prism_full` | 0.572 / **0.724** / 0.923 | 0.492 / **0.686** / 0.905 | 0.546 / **0.712** / 0.917 |
| `ablation_lexical_anchors` | 0.516 / **0.628** / 0.866 | 0.345 / **0.560** / 0.739 | 0.462 / **0.607** / 0.826 |
| `ablation_signature_only` | 0.576 / **0.796** / 0.941 | 0.543 / **0.716** / 0.917 | 0.566 / **0.770** / 0.934 |
| `ablation_no_purity` | 0.540 / **0.728** / 0.925 | 0.575 / **0.735** / 0.925 | 0.551 / **0.730** / 0.925 |
| `prism_plus_distractors` | 0.592 / **0.688** / 0.907 | 0.536 / **0.696** / 0.908 | 0.575 / **0.691** / 0.908 |

