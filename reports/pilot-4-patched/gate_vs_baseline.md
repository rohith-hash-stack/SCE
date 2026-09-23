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

## Gate metrics (prism_two_pass vs baseline_bfs_bidirectional)

ΔTSR:        7.67 pp, 95% CI [5.53, 10.05], excludes zero: yes (n=300 paired cells)
ΔCPI_answer: 12.33 pp, 95% CI [8.32, 16.43], excludes zero: yes (n=300 paired cells)

## Decision

EXPAND
(neither metric clearly resolved (ΔTSR=7.67pp, ΔCPI_answer=12.33pp) - more data needed; thresholds: ΔTSR>=15.0pp, ΔCPI_answer>=15.0pp)
