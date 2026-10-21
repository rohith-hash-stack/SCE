# Turn-1 atomic-capability micro-ablation (Layer 4 forward tasks)

| arm | cells | Turn-1 gold recall | gold lost at Turn 1 (per cell, summed over tasks) | Turn-1 precision | requested / cell | F1 | P | R |
|---|---|---|---|---|---|---|---|---|
| `prism_atomic_caps` | 24 | 0.592 | 21.333 | 0.564 | 7.625 | 0.465 | 0.568 | 0.435 |
| `prism_contract` | 24 | 0.642 | 19.000 | 0.498 | 8.208 | 0.472 | 0.569 | 0.436 |
| `prism_full` | 24 | 0.652 | 18.000 | 0.559 | 7.458 | 0.493 | 0.577 | 0.469 |
| `prism_r0_rule_callers` | 24 | 0.964 | 2.000 | 0.405 | 15.250 | 0.521 | 0.573 | 0.553 |

Jaccard, control vs prism_atomic_caps (same task and seed, 24 pairs): Turn-1 requests 0.624, packed symbols 0.703.
Jaccard, control vs prism_contract (same task and seed, 24 pairs): Turn-1 requests 0.661, packed symbols 0.696.
Control vs itself across seeds (noise floor): 0.643.

Mean Turn-1 manifest tokens: prism_full 2605, prism_atomic_caps 2696, prism_contract 2978, no_axes 1903

## Turn-1 gold picked per seed (summed over tasks) and requested rows per task

| arm | per seed (gold picked / gold) | mean ± sd | requested / task, mean ± sd |
|---|---|---|---|
| `prism_atomic_caps` | 42: 27/49, 43: 28/49, 44: 28/49 | 27.67 ± 0.58 | 7.62 ± 2.81 |
| `prism_contract` | 42: 29/49, 43: 28/49, 44: 33/49 | 30.00 ± 2.65 | 8.21 ± 1.31 |
| `prism_full` | 42: 31/49, 43: 26/49, 44: 36/49 | 31.00 ± 5.00 | 7.46 ± 1.75 |
| `prism_r0_rule_callers` | 42: 47/49, 43: 47/49, 44: 47/49 | 47.00 ± 0.00 | 15.25 ± 0.00 |

## Selection delta: P(requested | MUTATES_* row) - P(requested | READ_ONLY row)

| arm | rows | corpus | P(req, MUTATES) | P(req, READ_ONLY) | delta |
|---|---|---|---|---|---|
| `prism_atomic_caps` | all_rows | all | 0.269 | 0.225 | 0.044 |
| `prism_atomic_caps` | all_rows | django | 0.123 | 0.146 | -0.023 |
| `prism_atomic_caps` | all_rows | express | 0.495 | 0.056 | 0.439 |
| `prism_atomic_caps` | all_rows | fastapi | 0.216 | 0.115 | 0.101 |
| `prism_atomic_caps` | all_rows | trpc | 0.333 | 0.441 | -0.108 |
| `prism_atomic_caps` | non_gold_rows | all | 0.135 | 0.165 | -0.030 |
| `prism_atomic_caps` | non_gold_rows | django | 0.075 | 0.111 | -0.036 |
| `prism_atomic_caps` | non_gold_rows | express | 0.229 | 0.056 | 0.174 |
| `prism_atomic_caps` | non_gold_rows | fastapi | 0.167 | 0.090 | 0.077 |
| `prism_atomic_caps` | non_gold_rows | trpc | 0.333 | 0.375 | -0.042 |
| `prism_contract` | all_rows | all | 0.316 | 0.275 | 0.041 |
| `prism_contract` | all_rows | django | 0.196 | 0.243 | -0.047 |
| `prism_contract` | all_rows | express | 0.525 | 0.000 | 0.525 |
| `prism_contract` | all_rows | fastapi | 0.196 | 0.126 | 0.070 |
| `prism_contract` | all_rows | trpc | 0.556 | 0.477 | 0.078 |
| `prism_contract` | non_gold_rows | all | 0.174 | 0.221 | -0.047 |
| `prism_contract` | non_gold_rows | django | 0.117 | 0.215 | -0.098 |
| `prism_contract` | non_gold_rows | express | 0.250 | 0.000 | 0.250 |
| `prism_contract` | non_gold_rows | fastapi | 0.167 | 0.103 | 0.064 |
| `prism_contract` | non_gold_rows | trpc | 0.556 | 0.417 | 0.139 |
| `prism_full` | all_rows | all | 0.310 | 0.233 | 0.076 |
| `prism_full` | all_rows | django | 0.159 | 0.146 | 0.014 |
| `prism_full` | all_rows | express | 0.485 | 0.056 | 0.429 |
| `prism_full` | all_rows | fastapi | 0.314 | 0.126 | 0.187 |
| `prism_full` | all_rows | trpc | 0.667 | 0.459 | 0.207 |
| `prism_full` | non_gold_rows | all | 0.174 | 0.155 | 0.019 |
| `prism_full` | non_gold_rows | django | 0.108 | 0.111 | -0.003 |
| `prism_full` | non_gold_rows | express | 0.208 | 0.056 | 0.153 |
| `prism_full` | non_gold_rows | fastapi | 0.233 | 0.077 | 0.156 |
| `prism_full` | non_gold_rows | trpc | 0.667 | 0.347 | 0.319 |
| `prism_r0_rule_callers` | all_rows | all | 0.717 | 0.425 | 0.292 |
| `prism_r0_rule_callers` | all_rows | django | 0.609 | 0.146 | 0.463 |
| `prism_r0_rule_callers` | all_rows | express | 0.909 | 0.333 | 0.576 |
| `prism_r0_rule_callers` | all_rows | fastapi | 0.588 | 0.276 | 0.312 |
| `prism_r0_rule_callers` | all_rows | trpc | 1.000 | 0.919 | 0.081 |
| `prism_r0_rule_callers` | non_gold_rows | all | 0.623 | 0.317 | 0.306 |
| `prism_r0_rule_callers` | non_gold_rows | django | 0.550 | 0.089 | 0.461 |
| `prism_r0_rule_callers` | non_gold_rows | express | 0.875 | 0.333 | 0.542 |
| `prism_r0_rule_callers` | non_gold_rows | fastapi | 0.400 | 0.192 | 0.208 |
| `prism_r0_rule_callers` | non_gold_rows | trpc | 1.000 | 0.875 | 0.125 |

## caps coverage of the manifest rows

| task | rows | rows with caps | gold rows | gold rows with caps | distinct caps |
|---|---|---|---|---|---|
| l4_fwd_django_01 | 65 | 13 | 4 | 2 | caps=[filesystem.delete@0]; caps=[filesystem.delete@2]; caps=[filesystem.read@0,filesystem.write@2,filesystem.delete@1]; caps=[filesystem.read@1,filesystem.delete@2]; caps=[filesystem.read@1,filesystem.write@1,filesystem.delete@1,random.csprng@2]; caps=[filesystem.read@1]; caps=[filesystem.read@2,filesystem.write@2,filesystem.delete@1]; caps=[random.csprng@0]; caps=[random.csprng@1]; caps=[random.csprng@2] |
| l4_fwd_django_02 | 29 | 6 | 5 | 0 | caps=[filesystem.read@2,filesystem.write@2,filesystem.delete@2]; caps=[network.connect@2,filesystem.read@1,filesystem.write@1,filesystem.delete@1,process.spawn@2]; caps=[time.clock@2] |
| l4_fwd_express_01 | 19 | 5 | 6 | 2 | caps=[time.wait@1]; caps=[time.wait@2] |
| l4_fwd_express_02 | 20 | 5 | 11 | 1 | caps=[time.wait@1]; caps=[time.wait@2] |
| l4_fwd_fastapi_01 | 27 | 0 | 8 | 0 | - |
| l4_fwd_fastapi_02 | 19 | 0 | 2 | 0 | - |
| l4_fwd_trpc_01 | 22 | 2 | 7 | 0 | caps=[time.wait@0]; caps=[time.wait@1] |
| l4_fwd_trpc_02 | 18 | 0 | 6 | 0 | - |
| **total** | 219 | 31 | 49 | 5 | |
