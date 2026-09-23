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

## Gate metrics (prism_two_pass vs baseline_bfs_bidirectional)

ΔTSR:        7.67 pp, 95% CI [5.29, 10.31], excludes zero: yes (n=240 paired cells)
ΔCPI_answer: 12.33 pp, 95% CI [7.90, 16.98], excludes zero: yes (n=240 paired cells)

## Decision

EXPAND
(neither metric clearly resolved (ΔTSR=7.67pp, ΔCPI_answer=12.33pp) - more data needed; thresholds: ΔTSR>=15.0pp, ΔCPI_answer>=15.0pp)
