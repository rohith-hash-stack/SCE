# R3a main-window tasks: mined and gated (2026-11-07; no model calls)

- Window: commits on or after 2024-12-01, to each clone's HEAD (recorded 2026-11-05). All 18 Family B
  repositories, and Family N's 7. The rules are the same as the pilot's (`mine.py`, `gate.py`).
- Gate: seed 5501, target 80 per family, at most 10 per repository for Family B and 20 for Family N, and the used
  repositories at most half. Each family ran in its own process. Gate code: `5726b7ff` for the gating itself; the
  attrition counts were recomputed at the next commit (see `IMPLEMENTATION_LOG.md`, 2026-11-07).
- **Not frozen.** Both task lists are `"status": "short"`. Nothing here is hash-committed. What R3a does with them
  is open.

Files: `yield.json`, `candidates_{B,N}.json` (mining); `gate_{B,N}.jsonl`, `attrition_{B,N}.json`,
`tasks_{B,N}.json` (gate); `summary.json` (per repository: mined, drawn, passed, failure reasons, and for Family B
the changed-caller distribution, mined and gated).

| | mined | drawn | passed | 1 caller | 2 | 3-4 | 5+ |
|---|---|---|---|---|---|---|---|
| Family B, mined | 132 | | | 102 | 21 | 7 | 2 |
| Family B, gated | | 117 | **66** (Python 38, JS/TS 28) | 52 | 10 | 3 | 1 |
| Family N | 28 | 28 | **23** (Python 15, JS/TS 8) | | | | |
