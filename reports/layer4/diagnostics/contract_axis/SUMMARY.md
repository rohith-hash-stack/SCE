# Turn-1 atomic-capability micro-ablation (Layer 4 forward tasks)

| arm | cells | Turn-1 gold recall | gold lost at Turn 1 (per cell, summed over tasks) | Turn-1 precision | requested / cell | F1 | P | R |
|---|---|---|---|---|---|---|---|---|
| `prism_atomic_caps` | 6 | 0.483 | 4.333 | 0.491 | 9.667 | 0.444 | 0.469 | 0.467 |
| `prism_contract` | 6 | 0.683 | 2.667 | 0.413 | 10.333 | 0.536 | 0.588 | 0.500 |
| `prism_full` | 6 | 0.525 | 4.000 | 0.439 | 7.167 | 0.428 | 0.440 | 0.433 |
| `prism_r0_rule_callers` | 6 | 1.000 | 0.000 | 0.283 | 17.500 | 0.450 | 0.444 | 0.500 |

Jaccard, control vs prism_atomic_caps (same task and seed, 6 pairs): Turn-1 requests 0.430, packed symbols 0.655.
Jaccard, control vs prism_contract (same task and seed, 6 pairs): Turn-1 requests 0.654, packed symbols 0.647.
Control vs itself across seeds (noise floor): 0.572.

Mean Turn-1 manifest tokens: prism_full 4195, prism_atomic_caps 4396, prism_contract 4832, no_axes 3108

## Turn-1 gold picked per seed (summed over tasks) and requested rows per task

| arm | per seed (gold picked / gold) | mean ± sd | requested / task, mean ± sd |
|---|---|---|---|
| `prism_atomic_caps` | 42: 5/9, 43: 5/9, 44: 4/9 | 4.67 ± 0.58 | 9.67 ± 11.59 |
| `prism_contract` | 42: 6/9, 43: 5/9, 44: 8/9 | 6.33 ± 1.53 | 10.33 ± 7.18 |
| `prism_full` | 42: 5/9, 43: 5/9, 44: 5/9 | 5.00 ± 0.00 | 7.17 ± 2.25 |
| `prism_r0_rule_callers` | 42: 9/9, 43: 9/9, 44: 9/9 | 9.00 ± 0.00 | 17.50 ± 0.00 |

## Selection delta: P(requested | MUTATES_* row) - P(requested | READ_ONLY row)

| arm | rows | corpus | P(req, MUTATES) | P(req, READ_ONLY) | delta |
|---|---|---|---|---|---|
| `prism_atomic_caps` | all_rows | all | 0.123 | 0.146 | -0.023 |
| `prism_atomic_caps` | all_rows | django | 0.123 | 0.146 | -0.023 |
| `prism_atomic_caps` | non_gold_rows | all | 0.075 | 0.111 | -0.036 |
| `prism_atomic_caps` | non_gold_rows | django | 0.075 | 0.111 | -0.036 |
| `prism_contract` | all_rows | all | 0.196 | 0.243 | -0.047 |
| `prism_contract` | all_rows | django | 0.196 | 0.243 | -0.047 |
| `prism_contract` | non_gold_rows | all | 0.117 | 0.215 | -0.098 |
| `prism_contract` | non_gold_rows | django | 0.117 | 0.215 | -0.098 |
| `prism_full` | all_rows | all | 0.159 | 0.146 | 0.014 |
| `prism_full` | all_rows | django | 0.159 | 0.146 | 0.014 |
| `prism_full` | non_gold_rows | all | 0.108 | 0.111 | -0.003 |
| `prism_full` | non_gold_rows | django | 0.108 | 0.111 | -0.003 |
| `prism_r0_rule_callers` | all_rows | all | 0.609 | 0.146 | 0.463 |
| `prism_r0_rule_callers` | all_rows | django | 0.609 | 0.146 | 0.463 |
| `prism_r0_rule_callers` | non_gold_rows | all | 0.550 | 0.089 | 0.461 |
| `prism_r0_rule_callers` | non_gold_rows | django | 0.550 | 0.089 | 0.461 |

## caps coverage of the manifest rows

| task | rows | rows with caps | gold rows | gold rows with caps | distinct caps |
|---|---|---|---|---|---|
| l4_fwd_django_01 | 65 | 13 | 4 | 2 | caps=[filesystem.delete@0]; caps=[filesystem.delete@2]; caps=[filesystem.read@0,filesystem.write@2,filesystem.delete@1]; caps=[filesystem.read@1,filesystem.delete@2]; caps=[filesystem.read@1,filesystem.write@1,filesystem.delete@1,random.csprng@2]; caps=[filesystem.read@1]; caps=[filesystem.read@2,filesystem.write@2,filesystem.delete@1]; caps=[random.csprng@0]; caps=[random.csprng@1]; caps=[random.csprng@2] |
| l4_fwd_django_02 | 29 | 6 | 5 | 0 | caps=[filesystem.read@2,filesystem.write@2,filesystem.delete@2]; caps=[network.connect@2,filesystem.read@1,filesystem.write@1,filesystem.delete@1,process.spawn@2]; caps=[time.clock@2] |
| **total** | 94 | 19 | 9 | 2 | |
