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

## Why the runs failed (investigated after the verdict; the verdict stands)

**Method:** each run's final tree was rebuilt by replaying its applied `edit_file` calls on the task's start, then
compared with the answer key place by place (scratchpad `replay.py`).

**litestar, PRISM_ONLY 2/3 vs PRISM_LSP 3/3: one run looped.**
- PRISM_ONLY seed 43 started well: `prism_find_references`, then 13 edits.
- It missed two import lines (in `operation.py` and `path_item.py`; it had edited the uses in those files) and was
  rejected twice at `done`.
- It then read the same 10 lines of `link.py` 29 times in a row (turns 32-60) until the cap. The harness's repeat
  note (C3) was attached each time and ignored.
- A model loop, not the map.

**pydantic `Protocol`, 0/3 in every arm: the code repeats itself, and the agents searched instead of asking for
references.**
- `pydantic/v1/parse.py`'s `load_str_bytes` and `load_file` are near-copies (`proto = Protocol.pickle` at lines 28
  and 62), and `main.py` repeats `proto: Protocol = None,`. `edit_file` needs a unique span, so edits were rejected
  over and over ("occurs 2 times"): up to 34 failed edits in a run. All runs reached the 60-turn cap.
- A twin class `Protocol` in `pydantic/deprecated/parse.py`: BASELINE_LSP renamed it by mistake in 2 of 3 runs.
- PRISM_LSP left the same 6 places in `v1/parse.py` in all 3 seeds. It never called `prism_find_references`, which
  lists all 16 places and says that 67 other uses of the name belong to other objects. It searched
  `prism_search "Protocol"` again and again instead.

**fastmcp `Deployment`, PRISM arms 0/3: the history compaction cut PRISM's list.**
- Every PRISM run left the same production place: `deployment = Deployment(` at line 305 in
  `MCPServerConfig.from_cli_args`. (Places left in test files do not count: the scorer applies the answer key's
  tests.)
- No PRISM run edited that line. Three runs edited a look-alike line in a test file instead.
- `prism_find_references` lists all 21 places, but its output is 30 lines and line 305 is on output line 13.
  Compaction (Phase 1 item 2) keeps an old index-tool output's first 12 lines once 6 newer outputs follow. After the
  agent's first six edits, line 305 was gone from its view.
- The language server's output is more compact (no file headers, no labels), and line 305 is on its line 11, kept.
  BASELINE_LSP edited it in 2 of 3 runs, after reading those lines.
- The same rule applies to every arm, but it cuts PRISM's longer, labelled list where it does not cut the LSP's: a
  harness effect against the richer output.

**Control `r4s-delete-05`, PRISM_LSP 1/3: the `else:` trap is still there.**
- Every failed run, in every arm (BASELINE_LSP 1, PRISM_LSP 2, PRISM_ONLY 1), left `multidict/_multidict_py.py`
  unparseable: an empty `else:`, a deleted `if` body, or `GenericAliaselse:` joined onto one line. They then edited
  lines 10-20 until the cap.
- The delete-05 fix shows the block, but the agents still delete the branch's only statement.
- Across both PRISM arms the solve rate (3 of 6) is in line with Phase 2's PRISM (4 of 10). BASELINE_LSP's 2 of 3 is
  below Phase 2 BASELINE's 10 of 10.

**Across the four:**
- None of the losses was the map missing a place. `prism_find_references` listed every place on every task.
- The losses came from:
  1. the compaction rule cutting a long reference list;
  2. `edit_file`'s unique-span rule on repeated code;
  3. model loops at the turn cap;
  4. the agent choosing `prism_search` over `prism_find_references`.
