# Phase 3 proof of concept: results (2026-11-23)

45 runs (4 hidden-dependency renames + `r4s-delete-05`, 3 arms, seeds 42-44), code `af8e8044`, analysis by the
pre-registered rule (`rebaseline_report.go_no_go`; report: `reports/r4/phase3_poc_final/report.json`).

## Verdict: NO-GO

**The rule:** a PRISM arm solves at least 3 more of its 12 hidden-task runs than BASELINE_LSP, losing at most 1 task.

| Arm | Hidden runs solved (of 12) | vs BASELINE_LSP | Hidden tasks lost | GO? |
|---|---|---|---|---|
| BASELINE_LSP | 6 | - | - | - |
| PRISM_LSP | 6 | +0 | 1 | no |
| PRISM_ONLY | 5 | -1 | 1 | no |

- **Runs:** 0 infrastructure deaths.
- **Shard 2's failed health check** (one run's unreadable replies) was counted as decided before unblinding: all 45
  kept. It does not change the verdict: that run's arm, PRISM_LSP, solved none of the 3 runs of that task, and even
  a solve would leave it at +1.

## Per task (runs solved of 3; mean score)

| Task | BASELINE_LSP | PRISM_LSP | PRISM_ONLY |
|---|---|---|---|
| p3-hidden-01 litestar `Server` (15 places) | 2 (0.67) | 3 (1.00) | 2 (0.67) |
| p3-hidden-02 pydantic `Protocol` (16) | 0 (0.00) | 0 (0.00) | 0 (0.00) |
| p3-hidden-03 werkzeug `Range.from_header` (16) | 2 (0.67) | 3 (1.00) | 3 (1.00) |
| p3-hidden-04 fastmcp `Deployment` (22) | 2 (0.67) | 0 (0.00) | 0 (0.00) |
| r4s-delete-05 (control) | 2 (0.83) | 1 (0.67) | 2 (0.83) |

## What the runs show

- **The PRISM arms win two tasks and lose one.** PRISM_LSP solved every run of litestar and werkzeug, and
  PRISM_ONLY every run of werkzeug. On fastmcp both PRISM arms failed all 3 runs, and BASELINE_LSP solved 2.
- **fastmcp is an execution miss, not a map miss.** Every PRISM run left exactly 1 or 2 of the 21 places
  un-renamed, and every run hit the 60-turn cap. `prism_find_references` lists all 21 places on that code (checked
  after the run). The agents had the full map and did not finish applying it.
- **pydantic `Protocol` was solved by no arm.** Every run hit the cap with 4-12 places left. BASELINE_LSP also
  renamed look-alikes, `typing.Protocol` among them, in 2 of its 3 runs.
- **Neither side mainly uses a reference search.**
  - BASELINE_LSP called `find_references` less than once a run and worked through `grep` and `read_file`.
  - The PRISM arms called `prism_find_references` 0-3 times a run and used `prism_search` and `prism_read` instead.
  - Most runs of every arm hit the 60-turn cap.

## What follows from the rule

The confirmatory stage (225 runs) is not run as registered. Any next step is a new amendment, written before its runs.
