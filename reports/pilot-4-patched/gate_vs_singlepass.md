## Coverage

Total cells: 1440
  baseline_bfs_bidirectional: 240 cells
  baseline_bfs_forward: 240 cells
  baseline_rag: 240 cells
  oracle: 240 cells
  prism_two_pass: 240 cells
  prism_v11: 240 cells

## TSR per engine

| engine | n | mean TSR |
|---|---|---|
| baseline_bfs_bidirectional | 240 | 0.880 |
| baseline_bfs_forward | 240 | 0.912 |
| baseline_rag | 240 | 0.571 |
| oracle | 240 | 0.961 |
| prism_two_pass | 240 | 0.957 |
| prism_v11 | 240 | 0.948 |

## CPI_answer per engine

| engine | n | mean CPI_answer |
|---|---|---|
| baseline_bfs_bidirectional | 240 | 0.833 |
| baseline_bfs_forward | 240 | 0.967 |
| baseline_rag | 240 | 0.017 |
| oracle | 240 | 0.983 |
| prism_two_pass | 240 | 0.957 |
| prism_v11 | 240 | 0.933 |

## Gate metrics (prism_two_pass vs prism_v11)

ΔTSR:        0.89 pp, 95% CI [-0.37, 2.16], excludes zero: no (n=240 paired cells)
ΔCPI_answer: 2.33 pp, 95% CI [-0.42, 5.29], excludes zero: no (n=240 paired cells)

## Decision

STOP
(both deltas below 5pp (ΔTSR=0.89pp, ΔCPI_answer=2.33pp); thresholds: ΔTSR>=15.0pp, ΔCPI_answer>=15.0pp)
