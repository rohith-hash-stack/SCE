# POC 2 raw ledger: all 45 runs

From each run's `.score.json` (`reports/r4/phase3_poc2_seed<SEED>/`). Resolved = `score.resolved` (hidden tests and derived check pass). End = how the run stopped (`end`). Top tools = the three most-called tools, with call counts.


## p3-hidden-06

| Seed | Arm | Resolved | End | Turns | Tokens | Top 3 tools (calls) |
|---|---|---|---|---|---|---|
| 201 | BASELINE_LSP | No | turn cap | 60 | 289,137 | edit_file 47, grep 7, read_file 5 |
| 201 | PRISM_LSP | No | turn cap | 60 | 395,706 | edit_file 41, prism_read 11, grep 4 |
| 201 | PRISM_GW | **Yes** | done | 28 | 223,466 | edit_file 24, done 1, prism_change_context 1 |
| 202 | BASELINE_LSP | No | turn cap | 60 | 485,180 | edit_file 44, grep 7, read_file 7 |
| 202 | PRISM_LSP | No | turn cap | 60 | 399,074 | edit_file 40, read_file 13, grep 4 |
| 202 | PRISM_GW | **Yes** | done | 28 | 223,466 | edit_file 24, done 1, prism_change_context 1 |
| 203 | BASELINE_LSP | No | turn cap | 60 | 331,873 | edit_file 37, read_file 11, grep 8 |
| 203 | PRISM_LSP | No | turn cap | 60 | 427,147 | edit_file 30, prism_read 16, prism_search 9 |
| 203 | PRISM_GW | **Yes** | done | 28 | 223,466 | edit_file 24, done 1, prism_change_context 1 |

## p3-hidden-13

| Seed | Arm | Resolved | End | Turns | Tokens | Top 3 tools (calls) |
|---|---|---|---|---|---|---|
| 201 | BASELINE_LSP | No | turn cap | 60 | 290,945 | edit_file 32, read_file 19, grep 7 |
| 201 | PRISM_LSP | **Yes** | turn cap | 60 | 389,220 | prism_read 34, edit_file 23, done 1 |
| 201 | PRISM_GW | **Yes** | done | 34 | 271,392 | edit_file 27, read_file 3, done 1 |
| 202 | BASELINE_LSP | No | turn cap | 60 | 268,086 | edit_file 51, grep 6, read_file 2 |
| 202 | PRISM_LSP | **Yes** | turn cap | 60 | 376,442 | edit_file 39, read_file 15, grep 3 |
| 202 | PRISM_GW | **Yes** | done | 47 | 410,159 | edit_file 28, read_file 15, done 1 |
| 203 | BASELINE_LSP | No | turn cap | 60 | 302,800 | edit_file 46, grep 7, read_file 5 |
| 203 | PRISM_LSP | **Yes** | turn cap | 60 | 375,508 | edit_file 34, read_file 17, prism_find_references 4 |
| 203 | PRISM_GW | **Yes** | turn cap | 60 | 662,626 | edit_file 50, read_file 5, prism_read 3 |

## p3-hidden-15

| Seed | Arm | Resolved | End | Turns | Tokens | Top 3 tools (calls) |
|---|---|---|---|---|---|---|
| 201 | BASELINE_LSP | **Yes** | done | 43 | 175,667 | edit_file 26, grep 9, read_file 5 |
| 201 | PRISM_LSP | No | turn cap | 60 | 411,087 | edit_file 27, prism_read 27, prism_search 2 |
| 201 | PRISM_GW | No | turn cap | 60 | 689,849 | edit_file 54, read_file 2, done 1 |
| 202 | BASELINE_LSP | **Yes** | done | 45 | 192,590 | edit_file 28, grep 9, read_file 5 |
| 202 | PRISM_LSP | No | turn cap | 60 | 355,534 | prism_search 30, edit_file 20, run_tests 4 |
| 202 | PRISM_GW | **Yes** | done | 27 | 188,819 | edit_file 18, read_file 5, done 1 |
| 203 | BASELINE_LSP | No | turn cap | 60 | 296,527 | grep 30, edit_file 24, read_file 5 |
| 203 | PRISM_LSP | No | turn cap | 60 | 399,899 | edit_file 43, read_file 14, done 1 |
| 203 | PRISM_GW | No | turn cap | 60 | 634,932 | edit_file 54, read_file 2, done 1 |

## p3-hidden-16

| Seed | Arm | Resolved | End | Turns | Tokens | Top 3 tools (calls) |
|---|---|---|---|---|---|---|
| 201 | BASELINE_LSP | No | turn cap | 60 | 407,294 | edit_file 28, read_file 18, grep 12 |
| 201 | PRISM_LSP | **Yes** | done | 28 | 143,168 | edit_file 23, done 1, prism_change_context 1 |
| 201 | PRISM_GW | **Yes** | done | 28 | 223,451 | edit_file 24, done 1, prism_change_context 1 |
| 202 | BASELINE_LSP | No | turn cap | 60 | 484,730 | edit_file 32, read_file 15, grep 8 |
| 202 | PRISM_LSP | **Yes** | done | 30 | 155,637 | edit_file 24, read_file 2, done 1 |
| 202 | PRISM_GW | **Yes** | done | 28 | 223,446 | edit_file 24, done 1, prism_change_context 1 |
| 203 | BASELINE_LSP | No | turn cap | 60 | 355,630 | grep 23, edit_file 18, read_file 13 |
| 203 | PRISM_LSP | **Yes** | done | 37 | 225,789 | edit_file 26, read_file 7, done 1 |
| 203 | PRISM_GW | **Yes** | done | 34 | 288,504 | edit_file 25, read_file 5, done 1 |

## r4s-delete-09

| Seed | Arm | Resolved | End | Turns | Tokens | Top 3 tools (calls) |
|---|---|---|---|---|---|---|
| 201 | BASELINE_LSP | **Yes** | turn cap | 60 | 409,051 | read_file 25, edit_file 20, grep 12 |
| 201 | PRISM_LSP | **Yes** | turn cap | 60 | 458,647 | read_file 28, edit_file 22, grep 3 |
| 201 | PRISM_GW | **Yes** | turn cap | 60 | 470,092 | read_file 31, edit_file 22, grep 2 |
| 202 | BASELINE_LSP | **Yes** | turn cap | 60 | 400,733 | read_file 42, edit_file 13, grep 3 |
| 202 | PRISM_LSP | **Yes** | turn cap | 60 | 544,829 | read_file 29, edit_file 25, run_tests 2 |
| 202 | PRISM_GW | **Yes** | turn cap | 60 | 530,641 | edit_file 28, read_file 23, run_tests 4 |
| 203 | BASELINE_LSP | **Yes** | turn cap | 60 | 416,880 | edit_file 34, read_file 17, grep 8 |
| 203 | PRISM_LSP | **Yes** | turn cap | 60 | 553,982 | edit_file 27, prism_read 23, run_tests 3 |
| 203 | PRISM_GW | **Yes** | turn cap | 60 | 480,761 | prism_read 31, edit_file 25, done 1 |
