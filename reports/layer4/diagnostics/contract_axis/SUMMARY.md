# Turn-1 atomic-capability micro-ablation (Layer 4 forward tasks)

| arm | cells | Turn-1 gold recall | gold lost at Turn 1 (per cell, summed over tasks) | Turn-1 precision | requested / cell | F1 | P | R |
|---|---|---|---|---|---|---|---|---|
| `prism_atomic_caps` | 6 | 0.556 | 5.667 | 0.584 | 8.667 | 0.444 | 0.526 | 0.390 |
| `prism_contract` | 6 | 0.583 | 5.333 | 0.426 | 10.000 | 0.438 | 0.503 | 0.400 |
| `prism_full` | 6 | 0.663 | 4.333 | 0.457 | 9.833 | 0.496 | 0.541 | 0.464 |
| `prism_r0_rule_callers` | 6 | 1.000 | 0.000 | 0.354 | 18.500 | 0.562 | 0.568 | 0.640 |

Jaccard, control vs prism_atomic_caps (same task and seed, 6 pairs): Turn-1 requests 0.555, packed symbols 0.585.
Jaccard, control vs prism_contract (same task and seed, 6 pairs): Turn-1 requests 0.609, packed symbols 0.617.
Control vs itself across seeds (noise floor): 0.644.

Mean Turn-1 manifest tokens: prism_full 2330, prism_atomic_caps 2378, prism_contract 2592, no_axes 1702

## Turn-1 gold picked per seed (summed over tasks) and requested rows per task

| arm | per seed (gold picked / gold) | mean ± sd | requested / task, mean ± sd |
|---|---|---|---|
| `prism_atomic_caps` | 42: 7/13, 43: 8/13, 44: 7/13 | 7.33 ± 0.58 | 8.67 ± 0.29 |
| `prism_contract` | 42: 8/13, 43: 7/13, 44: 8/13 | 7.67 ± 0.58 | 10.00 ± 2.65 |
| `prism_full` | 42: 8/13, 43: 9/13, 44: 9/13 | 8.67 ± 0.58 | 9.83 ± 1.26 |
| `prism_r0_rule_callers` | 42: 13/13, 43: 13/13, 44: 13/13 | 13.00 ± 0.00 | 18.50 ± 0.00 |

## Selection delta: P(requested | MUTATES_* row) - P(requested | READ_ONLY row)

| arm | rows | corpus | P(req, MUTATES) | P(req, READ_ONLY) | delta |
|---|---|---|---|---|---|
| `prism_atomic_caps` | all_rows | all | 0.333 | 0.441 | -0.108 |
| `prism_atomic_caps` | all_rows | trpc | 0.333 | 0.441 | -0.108 |
| `prism_atomic_caps` | non_gold_rows | all | 0.333 | 0.375 | -0.042 |
| `prism_atomic_caps` | non_gold_rows | trpc | 0.333 | 0.375 | -0.042 |
| `prism_contract` | all_rows | all | 0.556 | 0.477 | 0.078 |
| `prism_contract` | all_rows | trpc | 0.556 | 0.477 | 0.078 |
| `prism_contract` | non_gold_rows | all | 0.556 | 0.417 | 0.139 |
| `prism_contract` | non_gold_rows | trpc | 0.556 | 0.417 | 0.139 |
| `prism_full` | all_rows | all | 0.667 | 0.459 | 0.207 |
| `prism_full` | all_rows | trpc | 0.667 | 0.459 | 0.207 |
| `prism_full` | non_gold_rows | all | 0.667 | 0.347 | 0.319 |
| `prism_full` | non_gold_rows | trpc | 0.667 | 0.347 | 0.319 |
| `prism_r0_rule_callers` | all_rows | all | 1.000 | 0.919 | 0.081 |
| `prism_r0_rule_callers` | all_rows | trpc | 1.000 | 0.919 | 0.081 |
| `prism_r0_rule_callers` | non_gold_rows | all | 1.000 | 0.875 | 0.125 |
| `prism_r0_rule_callers` | non_gold_rows | trpc | 1.000 | 0.875 | 0.125 |

## caps coverage of the manifest rows

| task | rows | rows with caps | gold rows | gold rows with caps | distinct caps |
|---|---|---|---|---|---|
| l4_fwd_trpc_01 | 22 | 2 | 7 | 0 | caps=[time.wait@0]; caps=[time.wait@1] |
| l4_fwd_trpc_02 | 18 | 0 | 6 | 0 | - |
| **total** | 40 | 2 | 13 | 0 | |
