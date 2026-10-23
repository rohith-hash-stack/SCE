# Stage 2: sets 2-5, diagnostic (V5)

**Stop rule (V5 worse than better against V2, pooled): CONTINUE to set 6**

| comparison | better / worse / ties | sign p | recall@5 delta | MRR delta |
|---|---|---|---|---|
| pooled V5 vs V2 | 37 / 24 / 127 | 0.1237 | +0.021 | +0.028 |
| pooled V5 vs BASE | 82 / 19 / 87 | 0.0 | +0.101 | +0.077 |
| pooled V2 vs BASE | 73 / 25 / 90 | 0.0 | +0.080 | +0.048 |
| pooled V5 vs V4 | 32 / 20 / 136 | 0.1263 | +0.019 | +0.022 |
| set2 V5 vs V2 | 7 / 5 / 48 | 0.7744 | +0.025 | +0.018 |
| set2 V5 vs BASE | 22 / 5 / 33 | 0.0015 | +0.100 | +0.066 |
| set2 V2 vs BASE | 22 / 5 / 33 | 0.0015 | +0.075 | +0.049 |
| set2 V5 vs V4 | 5 / 5 / 50 | 1.0 | +0.017 | +0.002 |
| set3 V5 vs V2 | 11 / 7 / 32 | 0.4807 | +0.010 | +0.023 |
| set3 V5 vs BASE | 26 / 4 / 20 | 0.0001 | +0.090 | +0.082 |
| set3 V2 vs BASE | 25 / 5 / 20 | 0.0003 | +0.080 | +0.059 |
| set3 V5 vs V4 | 12 / 5 / 33 | 0.1435 | +0.000 | +0.027 |
| set4 V5 vs V2 | 13 / 9 / 26 | 0.5235 | +0.042 | +0.018 |
| set4 V5 vs BASE | 25 / 6 / 17 | 0.0009 | +0.125 | +0.062 |
| set4 V2 vs BASE | 20 / 9 / 19 | 0.0614 | +0.083 | +0.044 |
| set4 V5 vs V4 | 11 / 6 / 31 | 0.3323 | +0.052 | +0.021 |
| set5 V5 vs V2 | 6 / 3 / 21 | 0.5078 | +0.000 | +0.075 |
| set5 V5 vs BASE | 9 / 4 / 17 | 0.2668 | +0.083 | +0.111 |
| set5 V2 vs BASE | 6 / 6 / 18 | 1.0 | +0.083 | +0.036 |
| set5 V5 vs V4 | 4 / 4 / 22 | 1.0 | +0.000 | +0.053 |

| set | BASE recall@5 | BASE MRR | V2 recall@5 | V2 MRR | V4 recall@5 | V4 MRR | V5 recall@5 | V5 MRR |
|---|---|---|---|---|---|---|---|---|
| set2 | 0.85 | 0.69 | 0.925 | 0.738 | 0.933 | 0.754 | 0.95 | 0.756 |
| set3 | 0.72 | 0.499 | 0.8 | 0.557 | 0.81 | 0.553 | 0.81 | 0.58 |
| set4 | 0.562 | 0.433 | 0.646 | 0.477 | 0.635 | 0.474 | 0.688 | 0.495 |
| set5 | 0.683 | 0.479 | 0.767 | 0.515 | 0.767 | 0.538 | 0.767 | 0.59 |

Questions whose best gold rank moved (V2 -> V5; inf = unranked):

- set2:ft03: 4 -> 5
- set2:ft14: 2 -> 1
- set2:ft17: 7 -> 8
- set2:ft26: 2 -> 1
- set2:ft27: 1 -> 2
- set2:ft28: 6 -> 5
- set2:sg07: 3 -> 4
- set2:sg08: 9 -> 8
- set2:sg09: 2 -> 1
- set2:sg14: 10 -> 9
- set2:sg26: 3 -> 1
- set2:sg30: 1 -> 2
- set3:ex10: 8 -> 4
- set3:ex14: 5 -> 6
- set3:ex19: 4 -> 1
- set3:ex21: 3 -> 2
- set3:tr01: 4 -> 3
- set3:tr03: 15 -> 9
- set3:tr04: 103 -> 99
- set3:tr05: 2 -> 4
- set3:tr07: 34 -> 36
- set3:tr08: 2 -> 1
- set3:tr09: 2 -> 3
- set3:tr10: 4 -> 3
- set3:tr12: 3 -> 4
- set3:tr14: 7 -> 5
- set3:tr16: 95 -> 102
- set3:tr20: 26 -> 22
- set3:tr21: 4 -> 10
- set3:tr23: 8 -> 7
- set4:fl01: 7 -> 4
- set4:fl03: 28 -> 3
- set4:fl04: 14 -> 29
- set4:fl09: 1 -> 2
- set4:fl11: 4 -> 3
- set4:fl12: 3 -> 2
- set4:fy01: 13 -> 12
- set4:fy02: 3 -> 2
- set4:fy04: 5 -> 6
- set4:fy05: 21 -> 20
- set4:fy06: 4 -> 3
- set4:fy08: 2 -> 4
- set4:hp04: 17 -> 8
- set4:hp09: 41 -> 43
- set4:hp10: 2 -> 3
- set4:hp11: 16 -> 2
- set4:zd01: 8 -> 5
- set4:zd03: 33 -> 38
- set4:zd04: 2 -> 20
- set4:zd07: 30 -> 33
- set4:zd08: 85 -> 44
- set4:zd09: 5 -> 1
- set5:hn06: 3 -> 1
- set5:hn14: 52 -> 55
- set5:hn16: 3 -> 4
- set5:hn19: 41 -> 39
- set5:hn21: 25 -> 26
- set5:hn22: 61 -> 37
- set5:hn23: 3 -> 1
- set5:hn24: 2 -> 1
- set5:hn29: 2 -> 1
