# Set 4 ship decision (pre-registered rule, CANDIDATE.md)

**SHIP FIELD w=0.5**

| arm | better / worse / ties | sign p | beats BASE | recall@5 BASE -> arm (delta) | within 0.02 | MRR BASE -> arm | passes |
|---|---|---|---|---|---|---|---|
| FIELD w=0.5 | 22 / 7 / 19 | 0.0081 | yes | 0.521 -> 0.615 (+0.094) | yes | 0.442 -> 0.486 | yes |
| FIELD w=1.0 | 23 / 10 / 15 | 0.0351 | yes | 0.521 -> 0.625 (+0.104) | yes | 0.442 -> 0.503 | yes |

Rule: evaluate w=0.5 first; it passes if it beats BASE (more questions better than worse, two-sided sign test p < 0.05) and its mean recall@5 drops by at most 0.02. Otherwise apply the same test to w=1.0. If neither passes, do not ship.
