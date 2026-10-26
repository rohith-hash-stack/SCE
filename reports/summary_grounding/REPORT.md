# Summary grounding on set 6 (development comparison; README.md)

**Validity check (V2 vs BASE reproduces the recorded set-6 result): PASS**

| arm | recall@5 | recall@20 | MRR |
|---|---|---|---|
| BASE | 0.7 | 0.85 | 0.602 |
| V2 | 0.758 | 0.892 | 0.614 |
| SRC | 0.792 | 0.925 | 0.679 |
| SRCC | 0.808 | 0.917 | 0.691 |
| SRCC_PLUS | 0.808 | 0.917 | 0.714 |

| contrast | better / worse / ties | sign p | recall@5 delta | MRR delta | beats (pre-registered) |
|---|---|---|---|---|---|
| SRC vs V2 | 34 / 15 / 71 | 0.0094 | +0.033 | +0.065 | yes |
| SRCC vs V2 | 33 / 15 / 72 | 0.0133 | +0.050 | +0.077 | yes |
| SRC vs BASE | 42 / 12 / 66 | 0.0001 | +0.092 | +0.077 | yes |
| SRCC vs BASE | 43 / 14 / 63 | 0.0002 | +0.108 | +0.089 | yes |
| SRCC vs SRC | 23 / 21 / 76 | 0.8804 | +0.017 | +0.012 | no |
| SRCC_PLUS vs V2 | 38 / 15 / 67 | 0.0022 | +0.050 | +0.100 | yes |
| V2 vs BASE | 37 / 20 / 63 | 0.0331 | +0.058 | +0.012 | yes |

Recall@5 by repository:

| arm | httpx | rich | elysia | oclif |
|---|---|---|---|---|
| BASE | 0.9 | 0.8 | 0.5 | 0.6 |
| V2 | 0.9 | 0.867 | 0.633 | 0.633 |
| SRC | 0.933 | 0.833 | 0.667 | 0.733 |
| SRCC | 0.933 | 0.833 | 0.633 | 0.833 |
| SRCC_PLUS | 0.933 | 0.833 | 0.633 | 0.833 |

Diagnostic - rank vs V2, split by whether the gold function's own summary changed:

| arm | group | n | better | worse |
|---|---|---|---|---|
| SRC | gold summary changed | 120 | 34 | 15 |
| SRC | gold summary unchanged or missing | 0 | 0 | 0 |
| SRCC | gold summary changed | 119 | 33 | 14 |
| SRCC | gold summary unchanged or missing | 1 | 0 | 1 |

Generation:

```
{
 "httpx/src": {
  "written": 448,
  "repaired": 3,
  "truncated": 9,
  "mean_words": 13.0
 },
 "httpx/srcc": {
  "written": 447,
  "repaired": 14,
  "truncated": 9,
  "mean_words": 11.5,
  "facts": 1716,
  "facts_dropped": 79
 },
 "rich/src": {
  "written": 905,
  "repaired": 4,
  "truncated": 43,
  "mean_words": 12.6
 },
 "rich/srcc": {
  "written": 903,
  "repaired": 30,
  "truncated": 42,
  "mean_words": 11.5,
  "facts": 3608,
  "facts_dropped": 151
 },
 "elysia/src": {
  "written": 421,
  "repaired": 24,
  "truncated": 65,
  "mean_words": 13.1
 },
 "elysia/srcc": {
  "written": 418,
  "repaired": 65,
  "truncated": 63,
  "mean_words": 11.7,
  "facts": 1910,
  "facts_dropped": 56
 },
 "oclif/src": {
  "written": 417,
  "repaired": 15,
  "truncated": 17,
  "mean_words": 13.3
 },
 "oclif/srcc": {
  "written": 416,
  "repaired": 29,
  "truncated": 16,
  "mean_words": 11.9,
  "facts": 1977,
  "facts_dropped": 45
 }
}
```

Coverage (functions with a summary): {"httpx/V2": 1.0, "httpx/SRC": 1.0, "httpx/SRCC": 0.998, "httpx/SRCC_PLUS": 0.998, "rich/V2": 0.992, "rich/SRC": 0.992, "rich/SRCC": 0.99, "rich/SRCC_PLUS": 0.99, "elysia/V2": 1.0, "elysia/SRC": 1.0, "elysia/SRCC": 0.993, "elysia/SRCC_PLUS": 0.993, "oclif/V2": 1.0, "oclif/SRC": 1.0, "oclif/SRCC": 0.998, "oclif/SRCC_PLUS": 0.998}
