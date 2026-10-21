# Turn-1 atomic-capability micro-ablation (Layer 4 forward tasks)

| arm | cells | Turn-1 gold recall | gold lost at Turn 1 (per cell, summed over tasks) | Turn-1 precision | requested / cell | F1 | P | R |
|---|---|---|---|---|---|---|---|---|
| `prism_atomic_caps` | 6 | 0.562 | 7.000 | 0.389 | 3.833 | 0.363 | 0.431 | 0.383 |
| `prism_contract` | 6 | 0.479 | 7.333 | 0.361 | 3.833 | 0.313 | 0.370 | 0.344 |
| `prism_full` | 6 | 0.667 | 5.333 | 0.514 | 4.667 | 0.464 | 0.550 | 0.450 |
| `prism_r0_rule_callers` | 6 | 0.938 | 1.000 | 0.500 | 9.000 | 0.429 | 0.494 | 0.433 |

Jaccard, control vs prism_atomic_caps (same task and seed, 6 pairs): Turn-1 requests 0.778, packed symbols 0.805.
Jaccard, control vs prism_contract (same task and seed, 6 pairs): Turn-1 requests 0.683, packed symbols 0.779.
Control vs itself across seeds (noise floor): 0.745.

Mean Turn-1 manifest tokens: prism_full 2550, prism_atomic_caps 2598, prism_contract 2870, no_axes 1962

## Turn-1 gold picked per seed (summed over tasks) and requested rows per task

| arm | per seed (gold picked / gold) | mean ± sd | requested / task, mean ± sd |
|---|---|---|---|
| `prism_atomic_caps` | 42: 3/10, 43: 3/10, 44: 3/10 | 3.00 ± 0.00 | 3.83 ± 0.29 |
| `prism_contract` | 42: 3/10, 43: 3/10, 44: 2/10 | 2.67 ± 0.58 | 3.83 ± 0.76 |
| `prism_full` | 42: 5/10, 43: 3/10, 44: 6/10 | 4.67 ± 1.53 | 4.67 ± 1.26 |
| `prism_r0_rule_callers` | 42: 9/10, 43: 9/10, 44: 9/10 | 9.00 ± 0.00 | 9.00 ± 0.00 |

## Selection delta: P(requested | MUTATES_* row) - P(requested | READ_ONLY row)

| arm | rows | corpus | P(req, MUTATES) | P(req, READ_ONLY) | delta |
|---|---|---|---|---|---|
| `prism_atomic_caps` | all_rows | all | 0.216 | 0.115 | 0.101 |
| `prism_atomic_caps` | all_rows | fastapi | 0.216 | 0.115 | 0.101 |
| `prism_atomic_caps` | non_gold_rows | all | 0.167 | 0.090 | 0.077 |
| `prism_atomic_caps` | non_gold_rows | fastapi | 0.167 | 0.090 | 0.077 |
| `prism_contract` | all_rows | all | 0.196 | 0.126 | 0.070 |
| `prism_contract` | all_rows | fastapi | 0.196 | 0.126 | 0.070 |
| `prism_contract` | non_gold_rows | all | 0.167 | 0.103 | 0.064 |
| `prism_contract` | non_gold_rows | fastapi | 0.167 | 0.103 | 0.064 |
| `prism_full` | all_rows | all | 0.314 | 0.126 | 0.187 |
| `prism_full` | all_rows | fastapi | 0.314 | 0.126 | 0.187 |
| `prism_full` | non_gold_rows | all | 0.233 | 0.077 | 0.156 |
| `prism_full` | non_gold_rows | fastapi | 0.233 | 0.077 | 0.156 |
| `prism_r0_rule_callers` | all_rows | all | 0.588 | 0.276 | 0.312 |
| `prism_r0_rule_callers` | all_rows | fastapi | 0.588 | 0.276 | 0.312 |
| `prism_r0_rule_callers` | non_gold_rows | all | 0.400 | 0.192 | 0.208 |
| `prism_r0_rule_callers` | non_gold_rows | fastapi | 0.400 | 0.192 | 0.208 |

## caps coverage of the manifest rows

| task | rows | rows with caps | gold rows | gold rows with caps | distinct caps |
|---|---|---|---|---|---|
| l4_fwd_fastapi_01 | 27 | 0 | 8 | 0 | - |
| l4_fwd_fastapi_02 | 19 | 0 | 2 | 0 | - |
| **total** | 46 | 0 | 10 | 0 | |
