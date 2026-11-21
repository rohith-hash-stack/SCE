# Turn-1 atomic-capability micro-ablation (Layer 4 forward tasks)

| arm | cells | Turn-1 gold recall | gold lost at Turn 1 (per cell, summed over tasks) | Turn-1 precision | requested / cell | F1 | P | R |
|---|---|---|---|---|---|---|---|---|
| `prism_r0_rule_callers` | 24 | 0.964 | 2.000 | 0.413 | 15.000 | - | - | - |

Jaccard, control vs caps (same task and seed, 0 pairs): Turn-1 requests -, packed symbols -; control vs itself across seeds (noise floor): -.

## caps coverage of the manifest rows

| task | rows | rows with caps | gold rows | gold rows with caps | distinct caps |
|---|---|---|---|---|---|
| l4_fwd_django_01 | 54 | 10 | 4 | 2 | caps=[filesystem.delete@2]; caps=[filesystem.read@0,filesystem.write@2,filesystem.delete@1]; caps=[filesystem.read@1,filesystem.delete@2]; caps=[filesystem.read@2,filesystem.write@2,filesystem.delete@1]; caps=[random.csprng@0]; caps=[random.csprng@1]; caps=[random.csprng@2] |
| l4_fwd_django_02 | 27 | 6 | 5 | 0 | caps=[filesystem.read@2,filesystem.write@2,filesystem.delete@2]; caps=[network.connect@2,filesystem.read@1,filesystem.write@1,filesystem.delete@1,process.spawn@2]; caps=[time.clock@2] |
| l4_fwd_express_01 | 19 | 5 | 6 | 2 | caps=[time.wait@1]; caps=[time.wait@2] |
| l4_fwd_express_02 | 20 | 5 | 11 | 1 | caps=[time.wait@1]; caps=[time.wait@2] |
| l4_fwd_fastapi_01 | 22 | 0 | 8 | 0 | - |
| l4_fwd_fastapi_02 | 19 | 0 | 2 | 0 | - |
| l4_fwd_trpc_01 | 22 | 2 | 7 | 0 | caps=[time.wait@0]; caps=[time.wait@1] |
| l4_fwd_trpc_02 | 17 | 0 | 6 | 0 | - |
| **total** | 200 | 28 | 49 | 5 | |
