# Final sweep summary (final-sweep-8arms/1.1)

Cells: 940 (ok=933, error=7, dry_run=0); schema-invalid records: 0

| arm | ok/n | TSR (95% CI) | TSR partial | cleanliness | CPI ctx | CPI answer | CPI e2e | suff. ratio | sufficient | truncated | ctx tokens | $/cell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | 120/120 | 0.667 [0.58, 0.74] | 0.779 | 0.392 | 1.000 | 0.956 | 0.956 | 1.000 | 1.000 | 1.000 | 4732 | 0.00068 |
| `pragmatic_oracle` | 119/119 | 0.597 [0.51, 0.68] | 0.775 | 1.000 | 1.000 | 0.950 | 0.950 | 0.496 | 0.496 | 0.000 | 1843 | 0.00034 |
| `scaffolded_oracle` | 119/119 | 0.731 [0.65, 0.80] | 0.926 | 0.868 | 1.000 | 0.940 | 0.940 | 1.000 | 1.000 | 0.244 | 2276 | 0.00039 |
| `prism_full` | 118/119 | 0.686 [0.60, 0.76] | 0.905 | 0.678 | 0.936 | 0.907 | 0.907 | 0.839 | 0.712 | 0.237 | 3360 | 0.00077 |
| `ablation_lexical_anchors` | 116/118 | 0.560 [0.47, 0.65] | 0.739 | 0.504 | 0.612 | 0.742 | 0.610 | 0.685 | 0.655 | 0.224 | 2143 | 0.00056 |
| `ablation_signature_only` | 116/116 | 0.716 [0.63, 0.79] | 0.917 | 0.704 | 0.942 | 0.917 | 0.917 | 0.810 | 0.681 | 0.224 | 2888 | 0.00064 |
| `ablation_no_purity` | 113/115 | 0.735 [0.65, 0.81] | 0.925 | 0.710 | 0.947 | 0.927 | 0.927 | 0.796 | 0.667 | 0.211 | 3305 | 0.00074 |
| `prism_plus_distractors` | 112/114 | 0.696 [0.61, 0.77] | 0.908 | 0.232 | 0.933 | 0.908 | 0.908 | 0.830 | 0.702 | 0.211 | 5471 | 0.00079 |

| arm | Turn-1 parse fail | answer parse fail | anchor = task seed | seed-varying tasks | Turn-1 reused | distractors in ctx / named | mean latency (s) | calls | cost ($) |
|---|---|---|---|---|---|---|---|---|---|
| `baseline_bfs_bidirectional` | n/a | 0.000 | 1.000 | 4/12 | 0 | - | 1.90 | 120 | 0.0818 |
| `pragmatic_oracle` | n/a | 0.000 | 1.000 | 3/12 | 0 | - | 1.91 | 119 | 0.0399 |
| `scaffolded_oracle` | n/a | 0.000 | 1.000 | 2/12 | 0 | - | 1.80 | 119 | 0.0464 |
| `prism_full` | 0.000 | 0.000 | 1.000 | 3/12 | 0 | - | 3.71 | 264 | 0.0907 |
| `ablation_lexical_anchors` | 0.000 | 0.000 | 0.237 | 1/12 | 0 | - | 3.37 | 260 | 0.0649 |
| `ablation_signature_only` | 0.000 | 0.000 | 1.000 | 3/12 | 0 | - | 3.95 | 258 | 0.0747 |
| `ablation_no_purity` | 0.000 | 0.000 | 1.000 | 3/12 | 0 | - | 3.70 | 252 | 0.0845 |
| `prism_plus_distractors` | 0.000 | 0.000 | 1.000 | 3/12 | 112 | 8.0 / 0.00 | 2.09 | 136 | 0.0887 |

Paired contrasts (same task and seed; A - B):

| A | B | what it isolates | pairs | ΔTSR (95% CI) | Δcleanliness |
|---|---|---|---|---|---|
| `scaffolded_oracle` | `pragmatic_oracle` | scaffolding effect (cleanliness paradox) | 119 | +0.134 [+0.050, +0.227] | -0.132 |
| `prism_full` | `baseline_bfs_bidirectional` | PRISM vs floor | 118 | +0.008 [-0.102, +0.110] | +0.281 |
| `prism_full` | `ablation_lexical_anchors` | taxonomy vs lexical anchors | 116 | +0.121 [+0.026, +0.207] | +0.178 |
| `prism_full` | `ablation_signature_only` | 4-axis annotations vs signatures only | 116 | -0.034 [-0.095, +0.026] | -0.022 |
| `prism_full` | `ablation_no_purity` | purity axis | 113 | -0.062 [-0.115, -0.009] | -0.023 |
| `prism_full` | `prism_plus_distractors` | no noise vs injected distractors | 112 | -0.027 [-0.071, +0.018] | +0.456 |

Served models: {'gpt-4o-mini-2024-07-18': 1521}
System fingerprints: {'fp_b35aefdf58': 54, 'fp_e68db64f1a': 11, 'fp_359379690f': 75, 'fp_48e3e83ec9': 234, 'fp_b99562ea4c': 10, 'fp_0c03eba41c': 13, 'fp_6a65994342': 30, 'fp_23703e8e1f': 19, 'fp_10a9fd5a82': 132, 'fp_2e30314e0f': 29, 'fp_5a9d97c6db': 123, 'fp_f70887b4d3': 19, 'fp_8223f08ec3': 42, 'fp_6054aa6593': 35, 'fp_fd0531e57c': 43, 'fp_0b9898f509': 21, 'fp_f51aa69871': 21, 'fp_c77bee8c7a': 37, 'fp_8c9579f592': 20, 'fp_2a9610bf9f': 83, 'fp_915ca29da6': 50, 'fp_bae4e8e4a7': 6, 'fp_28f3a9c586': 20, 'fp_8ef2fa014c': 33, 'fp_894e7baa82': 22, 'fp_1e58d0ce9b': 41, 'fp_e351b00da9': 10, 'fp_d9f006541a': 31, 'fp_55929dd0d0': 10, 'fp_f695c34277': 21, 'fp_9262d56da7': 10, 'fp_edb0948d4f': 10, 'fp_a8bfe22efe': 35, 'fp_30d6d650fd': 10, 'fp_8c12b002a5': 29, 'fp_682e5ceb08': 29, 'fp_01c4d84367': 60, 'fp_fe2337fb29': 12, 'fp_0eb4be9d6c': 7, 'fp_5d3ba5542b': 24}
Tasks whose Turn-1 manifest hash varied across seeds (should be none): none

Cost: $0.5717 over 1528 LLM calls (3,210,016 prompt + 150,438 completion tokens).
Full-sweep projection: 7,200 cells (8 arms x 900) ≈ $4.42 - this run's mean cost per ok cell, per arm (repo mix differs - Django/FastAPI contexts are larger).

Errors:
- express_t02_012_cookie_signing prism_full seed=50: LLMCallError: the LLM endpoint rate-limited this request after 4 retries: Error code: 429 - {'error': {'message': 'You have no credits remaining. Add credits to
- express_t02_012_cookie_signing ablation_lexical_anchors seed=49: LLMCallError: the LLM endpoint rate-limited this request after 4 retries: Error code: 429 - {'error': {'message': 'You have no credits remaining. Add credits to
- express_t02_012_cookie_signing ablation_no_purity seed=45: LLMCallError: the LLM endpoint rate-limited this request after 4 retries: Error code: 429 - {'error': {'message': 'You have no credits remaining. Add credits to
- express_t02_012_cookie_signing prism_plus_distractors seed=42: LLMCallError: the LLM endpoint rate-limited this request after 4 retries: Error code: 429 - {'error': {'message': 'You have no credits remaining. Add credits to
- express_t02_012_cookie_signing ablation_lexical_anchors seed=47: LLMCallError: the LLM endpoint rate-limited this request after 4 retries: Error code: 429 - {'error': {'message': 'You have no credits remaining. Add credits to
- express_t02_012_cookie_signing prism_plus_distractors seed=44: LLMCallError: the LLM endpoint rate-limited this request after 4 retries: Error code: 429 - {'error': {'message': 'You have no credits remaining. Add credits to
- express_t02_012_cookie_signing ablation_no_purity seed=46: LLMCallError: the LLM endpoint rate-limited this request after 4 retries: Error code: 429 - {'error': {'message': 'You have no credits remaining. Add credits to
