# Held-out set 7: digest-grounded summaries (pre-registered rule, scored once)

**DIGEST does not qualify: digest summaries do not improve on V2**

- 1 DIGEST beats V2: no
- 2 SRC does not beat DIGEST: yes
- 3 DIGEST recall@5 >= SRC - 0.02: yes

| arm | recall@5 | recall@20 | MRR | mean words |
|---|---|---|---|---|
| BASE | 0.508 | 0.775 | 0.42 | - |
| V2 | 0.592 | 0.783 | 0.489 | 9.3 |
| SRC | 0.617 | 0.783 | 0.489 | 13.1 |
| DIGEST | 0.608 | 0.783 | 0.491 | 12.8 |

| contrast | better / worse / ties | sign p | recall@5 delta | MRR delta | beats |
|---|---|---|---|---|---|
| DIGEST vs V2 | 37 / 31 / 52 | 0.5446 | +0.017 | +0.002 | no |
| SRC vs DIGEST | 29 / 31 / 60 | 0.8974 | +0.008 | -0.002 | no |
| DIGEST vs SRC | 31 / 29 / 60 | 0.8974 | -0.008 | +0.002 | no |
| SRC vs V2 | 36 / 33 / 51 | 0.8099 | +0.025 | +0.000 | no |
| DIGEST vs BASE | 63 / 14 / 43 | 0.0 | +0.100 | +0.071 | yes |
| SRC vs BASE | 61 / 11 / 48 | 0.0 | +0.108 | +0.070 | yes |
| V2 vs BASE | 57 / 15 / 48 | 0.0 | +0.083 | +0.070 | yes |

Recall@5 by repository (diagnostic):

| arm | werkzeug | aiohttp | kysely | nest |
|---|---|---|---|---|
| BASE | 0.567 | 0.6 | 0.433 | 0.433 |
| V2 | 0.667 | 0.667 | 0.467 | 0.567 |
| SRC | 0.667 | 0.733 | 0.433 | 0.633 |
| DIGEST | 0.667 | 0.7 | 0.433 | 0.633 |

By gold function length (diagnostic; SRC sees the first 60 lines):

| group | n | DIGEST vs SRC | DIGEST vs V2 | SRC vs V2 |
|---|---|---|---|---|
| gold over 60 lines | 11 | 2 / 0 | 2 / 0 | 2 / 0 |
| gold 60 lines or fewer | 109 | 29 / 29 | 35 / 31 | 34 / 33 |

Coverage: {"werkzeug/V2": 0.969, "werkzeug/SRC": 0.969, "werkzeug/DIGEST": 0.969, "aiohttp/V2": 0.981, "aiohttp/SRC": 0.981, "aiohttp/DIGEST": 0.981, "kysely/V2": 1.0, "kysely/SRC": 1.0, "kysely/DIGEST": 1.0, "nest/V2": 1.0, "nest/SRC": 1.0, "nest/DIGEST": 1.0}

questions_sha256: `d6f3b12fbe0bf8bf8d9e7cd96d6603f9aa45f450c9e10ad751ea95458c51ec2f`
