# Set 6 decision - pre-registered rule (set6/README.md, CANDIDATE_V5.md)

**V5 does not qualify: v2 stays locked**

| comparison | better / worse / ties | sign p | recall@5 delta | MRR delta |
|---|---|---|---|---|
| V5 vs V2 | 25 / 16 / 79 | 0.211 | +0.008 | +0.040 |
| V5 vs BASE | 40 / 16 / 64 | 0.0018 | +0.067 | +0.052 |

Diagnostic (decides nothing):

| comparison | better / worse / ties | sign p | recall@5 delta | MRR delta |
|---|---|---|---|---|
| V4 vs V2 | 20 / 11 / 89 | 0.1496 | +0.000 | +0.021 |
| V2 vs BASE | 37 / 20 / 63 | 0.0331 | +0.058 | +0.012 |
| V5 vs V4 | 16 / 13 / 91 | 0.7111 | +0.008 | +0.019 |

| arm | recall@5 | recall@20 | MRR |
|---|---|---|---|
| BASE | 0.7 | 0.85 | 0.602 |
| V2 | 0.758 | 0.892 | 0.614 |
| V4 | 0.758 | 0.917 | 0.634 |
| V5 | 0.767 | 0.925 | 0.654 |
