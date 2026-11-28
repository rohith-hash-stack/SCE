## T5 matrix (mean TSR [95% CI], per-task 3-seed mean; cluster bootstrap over tasks)

| config | fastapi | django | express (n=2, anecdotal) | trpc |
|---|---|---|---|---|
| B0M4 | 0.281 [0.179, 0.375] | 0.190 [0.088, 0.307] | 0.250 [0.000, 0.500] | 0.151 [0.080, 0.240] |
| R1 | 0.375 [0.240, 0.514] | 0.407 [0.172, 0.656] | 0.333 [0.000, 0.667] | 0.372 [0.224, 0.535] |
| R0 | 0.518 [0.392, 0.633] | 0.558 [0.280, 0.809] | 0.500 [0.000, 1.000] | 0.666 [0.524, 0.802] |

## Secondary metrics (cell means)

| config | corpus | gold coverage | selection precision | selection recall | cells |
|---|---|---|---|---|---|
| B0M4 | fastapi | 0.310 | 0.402 | 0.304 | 24 |
| B0M4 | django | 0.195 | 0.368 | 0.195 | 24 |
| B0M4 | express | 0.250 | 0.046 | 0.250 | 6 |
| B0M4 | trpc | 0.127 | 0.267 | 0.127 | 42 |
| R1 | fastapi | 0.475 | 0.662 | 0.469 | 24 |
| R1 | django | 0.500 | 0.367 | 0.500 | 24 |
| R1 | express | 0.333 | 0.150 | 0.333 | 6 |
| R1 | trpc | 0.449 | 0.549 | 0.449 | 42 |
| R0 | fastapi | 0.904 | 0.670 | 0.886 | 24 |
| R0 | django | 0.766 | 0.358 | 0.766 | 24 |
| R0 | express | 0.500 | 0.250 | 0.500 | 6 |
| R0 | trpc | 0.982 | 0.902 | 0.982 | 42 |

## Pairwise: R0 vs R1 (Holm over the four corpora)

| corpus | comparison | Δ TSR | 95% CI | p_raw | p_holm | significant |
|---|---|---|---|---|---|---|
| fastapi | R0 − R1 | +0.143 | [-0.013, +0.320] | 0.0764 | 0.2292 | no |
| django | R0 − R1 | +0.151 | [-0.046, +0.391] | 0.1790 | 0.3580 | no |
| express (anecdotal) | R0 − R1 | +0.167 | [+0.000, +0.333] | 0.4930 | 0.4930 | no |
| trpc | R0 − R1 | +0.294 | [+0.159, +0.435] | 0.0000 | 0.0000 | yes |

## Pairwise: vs B0M4 (Holm over these 8 tests)

| corpus | comparison | Δ TSR | 95% CI | p_raw | p_holm | significant |
|---|---|---|---|---|---|---|
| fastapi | R1 − B0M4 | +0.094 | [-0.034, +0.243] | 0.1866 | 0.5598 | no |
| fastapi | R0 − B0M4 | +0.237 | [+0.092, +0.404] | 0.0000 | 0.0000 | yes |
| django | R1 − B0M4 | +0.217 | [-0.000, +0.452] | 0.0518 | 0.2072 | no |
| django | R0 − B0M4 | +0.368 | [+0.151, +0.569] | 0.0012 | 0.0060 | yes |
| express (anecdotal) | R1 − B0M4 | +0.083 | [+0.000, +0.167] | 0.4930 | 0.9860 | no |
| express (anecdotal) | R0 − B0M4 | +0.250 | [+0.000, +0.500] | 0.4930 | 0.9860 | no |
| trpc | R1 − B0M4 | +0.221 | [+0.103, +0.369] | 0.0000 | 0.0000 | yes |
| trpc | R0 − B0M4 | +0.515 | [+0.360, +0.667] | 0.0000 | 0.0000 | yes |

## Anomalies / FAIL rows

* R1: 3 cells with over_budget
* R1: 1 cells with turn1_degenerate
* R1: 1 cells where Turn 1 did not parse
* R0: 3 cells with over_budget
