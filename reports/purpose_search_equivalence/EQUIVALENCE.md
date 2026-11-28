# Shipped routing reproduces the evaluated FIELD w=0.5 arm

**PASS** - 8 repositories, 158 questions.

Each repository's committed summary cache was installed with `prism summarize --from-cache` code; `prism.ask`'s index then chose its summary weight by the shipped rule.

| repo | functions | installed / reported summaries | coverage | auto w | ranking == harness FIELD | BASE == reported | FIELD w=0.5 == reported | blast-radius identical |
|---|---|---|---|---|---|---|---|---|
| fastapi_template | 71 | 71 / 71 | 1.000 | 0.5 | 30/30 | yes | yes | 30/30 |
| styleguide | 203 | 203 / 203 | 1.000 | 0.5 | 30/30 | yes | yes | 35/35 |
| express | 270 | 270 / 270 | 1.000 | 0.5 | 25/25 | yes | yes | 26/26 |
| trpc | 1099 | 1099 / 1099 | 1.000 | 0.5 | 25/25 | yes | yes | 30/30 |
| flask | 369 | 366 / 366 | 0.992 | 0.5 | 12/12 | yes | yes | 12/12 |
| httpie | 615 | 615 / 615 | 1.000 | 0.5 | 12/12 | yes | yes | 12/12 |
| fastify | 800 | 800 / 800 | 1.000 | 0.5 | 12/12 | yes | yes | 14/14 |
| zod | 274 | 274 / 274 | 1.000 | 0.5 | 12/12 | yes | yes | 14/14 |

## Metrics per set (recomputed on the shipped path)

| set | arm | recall@5 | recall@20 | pool recall | MRR |
|---|---|---|---|---|---|
| set 2 (60) | BASE | 0.834 | 0.983 | 0.983 | 0.678 |
| set 2 (60) | FIELD w=0.5 | 0.933 | 1.000 | 1.000 | 0.748 |
| set 2 (60) | served (pin applied) | 0.933 | 1.000 | 1.000 | 0.748 |
| set 3 (50) | BASE | 0.740 | 0.890 | 0.900 | 0.502 |
| set 3 (50) | FIELD w=0.5 | 0.770 | 0.920 | 0.930 | 0.549 |
| set 3 (50) | served (pin applied) | 0.770 | 0.920 | 0.930 | 0.549 |
| set 4 (48) | BASE | 0.521 | 0.750 | 0.823 | 0.442 |
| set 4 (48) | FIELD w=0.5 | 0.614 | 0.844 | 0.864 | 0.486 |
| set 4 (48) | served (pin applied) | 0.614 | 0.844 | 0.864 | 0.486 |

## Questions the exact-name pin touched: 0

- none

Metrics are means over each set's questions (repository means weighted by question count). `served` is what `prism.ask` returns: the FIELD w=0.5 ranking with exact name matches of a mixed or identifier query in front.
