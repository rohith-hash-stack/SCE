## Coverage

Total cells: 1200
  baseline_bfs_bidirectional: 300 cells
  oracle: 300 cells
  prism_two_pass: 300 cells
  prism_v11: 300 cells

## TSR per engine

| engine | n | mean TSR |
|---|---|---|
| baseline_bfs_bidirectional | 300 | 0.765 |
| oracle | 300 | 0.854 |
| prism_two_pass | 300 | 0.844 |
| prism_v11 | 300 | 0.797 |

## CPI_answer per engine

| engine | n | mean CPI_answer |
|---|---|---|
| baseline_bfs_bidirectional | 300 | 0.833 |
| oracle | 300 | 0.983 |
| prism_two_pass | 300 | 0.881 |
| prism_v11 | 300 | 0.933 |

## Gate metrics (prism_two_pass vs prism_v11)

ΔTSR:        4.72 pp, 95% CI [1.55, 8.02], excludes zero: yes (n=300 paired cells)
ΔCPI_answer: -5.28 pp, 95% CI [-8.18, -2.25], excludes zero: yes (n=300 paired cells)

## Decision

STOP
(both deltas below 5pp (ΔTSR=4.72pp, ΔCPI_answer=-5.28pp); thresholds: ΔTSR>=15.0pp, ΔCPI_answer>=15.0pp)
