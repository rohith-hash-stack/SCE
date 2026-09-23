## Coverage

Total cells: 1800
  baseline_bfs_bidirectional: 300 cells
  baseline_bfs_forward: 300 cells
  baseline_rag: 300 cells
  oracle: 300 cells
  prism_two_pass: 300 cells
  prism_v11: 300 cells

## TSR per engine

| engine | n | mean TSR |
|---|---|---|
| baseline_bfs_bidirectional | 300 | 0.880 |
| baseline_bfs_forward | 300 | 0.912 |
| baseline_rag | 300 | 0.571 |
| oracle | 300 | 0.961 |
| prism_two_pass | 300 | 0.957 |
| prism_v11 | 300 | 0.948 |

## CPI_answer per engine

| engine | n | mean CPI_answer |
|---|---|---|
| baseline_bfs_bidirectional | 300 | 0.833 |
| baseline_bfs_forward | 300 | 0.967 |
| baseline_rag | 300 | 0.017 |
| oracle | 300 | 0.983 |
| prism_two_pass | 300 | 0.957 |
| prism_v11 | 300 | 0.933 |

## Gate metrics (prism_two_pass vs prism_v11)

ΔTSR:        0.89 pp, 95% CI [-0.24, 2.03], excludes zero: no (n=300 paired cells)
ΔCPI_answer: 2.33 pp, 95% CI [-0.18, 5.00], excludes zero: no (n=300 paired cells)

## Decision

STOP
(both deltas below 5pp (ΔTSR=0.89pp, ΔCPI_answer=2.33pp); thresholds: ΔTSR>=15.0pp, ΔCPI_answer>=15.0pp)
