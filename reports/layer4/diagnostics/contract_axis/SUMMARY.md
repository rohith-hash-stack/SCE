# Turn-1 atomic-capability micro-ablation (Layer 4 forward tasks)

| arm | cells | Turn-1 gold recall | gold lost at Turn 1 (per cell, summed over tasks) | Turn-1 precision | requested / cell | F1 | P | R |
|---|---|---|---|---|---|---|---|---|
| `prism_atomic_caps` | 6 | 0.765 | 4.333 | 0.780 | 8.333 | 0.609 | 0.845 | 0.499 |
| `prism_contract` | 6 | 0.821 | 3.667 | 0.790 | 8.667 | 0.602 | 0.816 | 0.499 |
| `prism_full` | 6 | 0.753 | 4.333 | 0.827 | 8.167 | 0.586 | 0.779 | 0.530 |
| `prism_r0_rule_callers` | 6 | 0.917 | 1.000 | 0.483 | 16.000 | 0.643 | 0.786 | 0.636 |

Jaccard, control vs prism_atomic_caps (same task and seed, 6 pairs): Turn-1 requests 0.734, packed symbols 0.766.
Jaccard, control vs prism_contract (same task and seed, 6 pairs): Turn-1 requests 0.696, packed symbols 0.741.
Control vs itself across seeds (noise floor): 0.611.

Mean Turn-1 manifest tokens: prism_full 1344, prism_atomic_caps 1410, prism_contract 1620, no_axes 840

## Turn-1 gold picked per seed (summed over tasks) and requested rows per task

| arm | per seed (gold picked / gold) | mean ± sd | requested / task, mean ± sd |
|---|---|---|---|
| `prism_atomic_caps` | 42: 12/17, 43: 12/17, 44: 14/17 | 12.67 ± 1.15 | 8.33 ± 1.26 |
| `prism_contract` | 42: 12/17, 43: 13/17, 44: 15/17 | 13.33 ± 1.53 | 8.67 ± 1.61 |
| `prism_full` | 42: 13/17, 43: 9/17, 44: 16/17 | 12.67 ± 3.51 | 8.17 ± 3.25 |
| `prism_r0_rule_callers` | 42: 16/17, 43: 16/17, 44: 16/17 | 16.00 ± 0.00 | 16.00 ± 0.00 |

## Selection delta: P(requested | MUTATES_* row) - P(requested | READ_ONLY row)

| arm | rows | corpus | P(req, MUTATES) | P(req, READ_ONLY) | delta |
|---|---|---|---|---|---|
| `prism_atomic_caps` | all_rows | all | 0.495 | 0.056 | 0.439 |
| `prism_atomic_caps` | all_rows | express | 0.495 | 0.056 | 0.439 |
| `prism_atomic_caps` | non_gold_rows | all | 0.229 | 0.056 | 0.174 |
| `prism_atomic_caps` | non_gold_rows | express | 0.229 | 0.056 | 0.174 |
| `prism_contract` | all_rows | all | 0.525 | 0.000 | 0.525 |
| `prism_contract` | all_rows | express | 0.525 | 0.000 | 0.525 |
| `prism_contract` | non_gold_rows | all | 0.250 | 0.000 | 0.250 |
| `prism_contract` | non_gold_rows | express | 0.250 | 0.000 | 0.250 |
| `prism_full` | all_rows | all | 0.485 | 0.056 | 0.429 |
| `prism_full` | all_rows | express | 0.485 | 0.056 | 0.429 |
| `prism_full` | non_gold_rows | all | 0.208 | 0.056 | 0.153 |
| `prism_full` | non_gold_rows | express | 0.208 | 0.056 | 0.153 |
| `prism_r0_rule_callers` | all_rows | all | 0.909 | 0.333 | 0.576 |
| `prism_r0_rule_callers` | all_rows | express | 0.909 | 0.333 | 0.576 |
| `prism_r0_rule_callers` | non_gold_rows | all | 0.875 | 0.333 | 0.542 |
| `prism_r0_rule_callers` | non_gold_rows | express | 0.875 | 0.333 | 0.542 |

## caps coverage of the manifest rows

| task | rows | rows with caps | gold rows | gold rows with caps | distinct caps |
|---|---|---|---|---|---|
| l4_fwd_express_01 | 19 | 5 | 6 | 2 | caps=[time.wait@1]; caps=[time.wait@2] |
| l4_fwd_express_02 | 20 | 5 | 11 | 1 | caps=[time.wait@1]; caps=[time.wait@2] |
| **total** | 39 | 10 | 17 | 3 | |
