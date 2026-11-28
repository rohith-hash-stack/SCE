# Retrieval lift

_Source: `reports/harness_m4/<corpus>/cells.parquet` at `8353a24`. 3-seed (42/43/44) mean TSR. 95% CI: task-level cluster bootstrap, 10,000 resamples, percentile. Corpora are never pooled._

lift_vs_arm0 = (mean_arm − mean_arm0) / mean_arm0 (null when mean_arm0 = 0).  
lift_vs_oracle = (mean_arm − mean_arm0) / (mean_oracle − mean_arm0): the fraction of the headroom above no-retrieval that the arm captures.

**Warnings:** express T5: arm0 mean is 0 -> lift_vs_arm0 undefined (null) [n=2, anecdotal]; trpc T5: arm0 mean is 0 -> lift_vs_arm0 undefined (null)

## Matrix A — lift vs Arm 0, T2

| Arm | fastapi | django | express | trpc |
|---|---|---|---|---|
| A0 no retrieval | +0.000 | +0.000 | +0.000 | +0.000 |
| A1 RAG | +0.429 | +0.733 | +0.139 | +1.000 |
| A2 packing | +0.500 | +0.733 | +0.389 | +1.067 |
| A3 LSP | +1.107 | +0.533 | -0.028 | +0.267 |
| A4 agent | +0.286 | -0.067 | +0.028 | +0.200 |
| A5 PRISM | +1.393 | +1.600 | +0.583 | +3.200 |
| Oracle | +1.393 | +2.067 | +0.667 | +3.533 |
| _arm0 / oracle means_ | 0.389 / 0.931 | 0.250 / 0.767 | 0.600 / 1.000 | 0.200 / 0.907 |

## Matrix A — lift vs Arm 0, T5

| Arm | fastapi | django | express (n=2) | trpc |
|---|---|---|---|---|
| A0 no retrieval | +0.000 | +0.000 | null | null |
| A1 RAG | +82.133 | +43.190 | null | null |
| A2 packing | +69.233 | +52.524 | null | null |
| A3 LSP | +42.967 | +13.000 | null | null |
| A4 agent | +6.000 | +1.143 | null | null |
| A5 PRISM | +46.200 | +35.429 | null | null |
| Oracle | +92.600 | +132.143 | null | null |
| _arm0 / oracle means_ | 0.006 / 0.557 | 0.005 / 0.693 | 0.000 / 0.833 | 0.000 / 0.570 |

## Matrix B — lift vs Oracle (headroom captured), T2

| Arm | fastapi | django | express | trpc |
|---|---|---|---|---|
| A0 no retrieval | +0.000 | +0.000 | +0.000 | +0.000 |
| A1 RAG | +0.308 | +0.355 | +0.208 | +0.283 |
| A2 packing | +0.359 | +0.355 | +0.583 | +0.302 |
| A3 LSP | +0.795 | +0.258 | -0.042 | +0.075 |
| A4 agent | +0.205 | -0.032 | +0.042 | +0.057 |
| A5 PRISM | +1.000 | +0.774 | +0.875 | +0.906 |
| Oracle | +1.000 | +1.000 | +1.000 | +1.000 |
| _arm0 / oracle means_ | 0.389 / 0.931 | 0.250 / 0.767 | 0.600 / 1.000 | 0.200 / 0.907 |

## Matrix B — lift vs Oracle (headroom captured), T5

| Arm | fastapi | django | express (n=2) | trpc |
|---|---|---|---|---|
| A0 no retrieval | +0.000 | +0.000 | +0.000 | +0.000 |
| A1 RAG | +0.887 | +0.327 | +0.433 | +0.442 |
| A2 packing | +0.748 | +0.397 | +0.967 | +0.460 |
| A3 LSP | +0.464 | +0.098 | +0.100 | +0.033 |
| A4 agent | +0.065 | +0.009 | +0.000 | +0.000 |
| A5 PRISM | +0.499 | +0.268 | +0.300 | +0.265 |
| Oracle | +1.000 | +1.000 | +1.000 | +1.000 |
| _arm0 / oracle means_ | 0.006 / 0.557 | 0.005 / 0.693 | 0.000 / 0.833 | 0.000 / 0.570 |

