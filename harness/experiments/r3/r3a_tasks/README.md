# R3a main-window tasks: mined and gated (2026-11-07; no model calls)

- Window: commits on or after 2024-12-01, to each clone's HEAD (recorded 2026-11-05). All 18 Family B
  repositories, and Family N's 7. The rules are the same as the pilot's (`mine.py`, `gate.py`).
- Gate: seed 5501, target 80 per family, at most 10 per repository for Family B and 20 for Family N, and the used
  repositories at most half. Each family ran in its own process. Gate code: `5726b7ff` for the gating itself; the
  attrition counts were recomputed at the next commit (see `IMPLEMENTATION_LOG.md`, 2026-11-07).
- **Family B is hash-committed (2026-11-08): `"status": "frozen"` at 80, sha256 of the task list `70a6aede…`**
  (`R3A_TASKS_HASH.md`). This supersedes `ba4de9ae…`: B-09's start state was corrected, the old B-29 is out, and one
  svelte task refilled its slot from the same draw. Family N is dropped to R3b (`tasks_N.json` stays `"short"`, 23,
  unused by R3a).
- Family B was expanded under amendment R3-8 (2026-11-08).
  - Pool: the 132 main-window candidates plus 60 from svelte, rollup, prettier and pydantic (`screen/`). The same
    seeded draw (5501) ran over the 192, resuming from the 117 records already gated; all 66 earlier passes stay.
  - Gate code `037f27b6` (sample-folder tests mapped to their suite's runner, for svelte); the attrition count was
    fixed at the next commit (records after the stopping point now count as drawn). Task list unchanged by that fix.
  - Task ids are renumbered, since they follow the draw order over the larger pool. The 66-task outputs are kept as
    `tasks_B.66.json` and `attrition_B.66.json`.

Files: `yield.json`, `candidates_{B,N}.json` (mining); `gate_{B,N}.jsonl`, `attrition_{B,N}.json`,
`tasks_{B,N}.json` (gate); `summary.json` (per repository: mined, drawn, passed, failure reasons, and for Family B
the changed-caller distribution, mined and gated).

| | mined | drawn | passed | 1 caller | 2 | 3-4 | 5+ |
|---|---|---|---|---|---|---|---|
| Family B, mined | 132 | | | 102 | 21 | 7 | 2 |
| Family B, gated (main window) | | 117 | 66 (Python 38, JS/TS 28) | 52 | 10 | 3 | 1 |
| Family B, mined (with R3-8) | 192 | | | 139 | 39 | 10 | 4 |
| Family B, gated (with R3-8) | | 133 | 80 (Python 41, JS/TS 39) | 62 | 13 | 3 | 2 |
| **Family B, hash-committed** | | 137 | **80** (Python 41, JS/TS 39) | 62 | 14 | 2 | 2 |
| Family N | 28 | 28 | **23** (Python 15, JS/TS 8) | | | | |
