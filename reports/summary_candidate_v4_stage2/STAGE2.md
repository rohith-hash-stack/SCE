# Stage 2: sets 2-5, diagnostic (CANDIDATE_V4.md)

**Stop rule (V4 worse than better against V2, pooled): CONTINUE to set 6**

| comparison | better / worse / ties | sign p | recall@5 delta | MRR delta |
|---|---|---|---|---|
| pooled V4 vs V2 | 18 / 17 / 153 | 1.0 | +0.003 | +0.007 |
| pooled V4 vs BASE | 74 / 20 / 94 | 0.0 | +0.082 | +0.055 |
| pooled V2 vs BASE | 73 / 25 / 90 | 0.0 | +0.080 | +0.048 |
| set2 V4 vs V2 | 3 / 1 / 56 | 0.625 | +0.008 | +0.016 |
| set2 V4 vs BASE | 21 / 4 / 35 | 0.0009 | +0.083 | +0.064 |
| set2 V2 vs BASE | 22 / 5 / 33 | 0.0015 | +0.075 | +0.049 |
| set3 V4 vs V2 | 5 / 6 / 39 | 1.0 | +0.010 | -0.004 |
| set3 V4 vs BASE | 23 / 5 / 22 | 0.0009 | +0.090 | +0.054 |
| set3 V2 vs BASE | 25 / 5 / 20 | 0.0003 | +0.080 | +0.059 |
| set4 V4 vs V2 | 8 / 9 / 31 | 1.0 | -0.010 | -0.003 |
| set4 V4 vs BASE | 22 / 7 / 19 | 0.0081 | +0.073 | +0.042 |
| set4 V2 vs BASE | 20 / 9 / 19 | 0.0614 | +0.083 | +0.044 |
| set5 V4 vs V2 | 2 / 1 / 27 | 1.0 | +0.000 | +0.023 |
| set5 V4 vs BASE | 8 / 4 / 18 | 0.3877 | +0.083 | +0.059 |
| set5 V2 vs BASE | 6 / 6 / 18 | 1.0 | +0.083 | +0.036 |

| set | BASE recall@5 | BASE MRR | V2 recall@5 | V2 MRR | V4 recall@5 | V4 MRR |
|---|---|---|---|---|---|---|
| set2 | 0.85 | 0.69 | 0.925 | 0.738 | 0.933 | 0.754 |
| set3 | 0.72 | 0.499 | 0.8 | 0.557 | 0.81 | 0.553 |
| set4 | 0.562 | 0.433 | 0.646 | 0.477 | 0.635 | 0.474 |
| set5 | 0.683 | 0.479 | 0.767 | 0.515 | 0.767 | 0.538 |

Questions whose best gold rank moved (V2 -> V4; inf = unranked):

- set2:ft13: 3 -> 4
- set2:ft26: 2 -> 1
- set2:sg08: 9 -> 8
- set2:sg09: 2 -> 1
- set3:ex06: 2 -> 4
- set3:ex19: 4 -> 3
- set3:ex21: 3 -> 2
- set3:ex22: 2 -> 3
- set3:tr01: 4 -> 5
- set3:tr04: 103 -> 102
- set3:tr07: 34 -> 35
- set3:tr10: 4 -> 3
- set3:tr12: 3 -> 4
- set3:tr16: 95 -> 136
- set3:tr20: 26 -> 24
- set4:fl01: 7 -> 6
- set4:fl03: 28 -> 6
- set4:fl09: 1 -> 2
- set4:fl11: 4 -> 3
- set4:fy05: 21 -> 22
- set4:fy06: 4 -> 5
- set4:fy08: 2 -> 4
- set4:hp04: 17 -> 8
- set4:hp09: 41 -> 43
- set4:hp11: 16 -> 15
- set4:hp12: 27 -> 28
- set4:zd01: 8 -> 7
- set4:zd03: 33 -> 37
- set4:zd04: 2 -> 19
- set4:zd07: 30 -> 32
- set4:zd08: 85 -> 84
- set4:zd09: 5 -> 1
- set5:hn06: 3 -> 1
- set5:hn14: 52 -> 54
- set5:hn22: 61 -> 33
