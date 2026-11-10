# R4 task mining: status (started 2026-11-10; candidate lists only, no gating)

**What runs:**
- `harness/experiments/r4/mine_families.py` mines refactor, delete, move, tests and multi-function feature
  candidates over 48 repositories:
  - R3's 22 local clones;
  - 14 new repositories, batch 1: httpx, starlette, rich, click, flask, jinja, attrs, marshmallow, pluggy, zod, hono,
    fastify, ky, commander;
  - 12 new repositories, batch 2: es-toolkit, remeda, radashi, valibot, more-itertools, boltons, textual,
    pydantic-ai, fastmcp, pdm, hatch, instructor.
- `harness/experiments/r3/mine.py` (Family N, R3's rule unchanged) runs over the 26 new repositories.
- **The window** for both is commits from 2024-12-01 to 2026-11-10, R3a's start date.
- **Not decided here:** which window R4 and R5 each use, how many tasks per family, and the gate rules. These are
  post-R3a scope decisions. Candidates are not tasks: none has been gated.

## Yield so far (12 of 48 repositories; the update follows when the run ends)

| Family | Candidates | Note |
|---|---|---|
| delete | 48 | removed function, with callers at the parent, and the callers updated |
| move | 6 | **the thin one**: a function moved between files and other files' imports updated |
| refactor | 303 | 24 renames with callers updated; 279 refactor-worded commits with no test change (noisy, and sqlglot alone has 149) |
| tests | 171 | test-only commits adding test functions that name repository functions |
| feature | 83 | 2+ new production functions, named by a changed test |
| **N** (R3's rule) | 14 from 26 new repositories | about 0.5 per repository |

**Family N's drop points** (the rule is R3's, pre-registered for R3; changing it is a scope decision):
- **"The function's file exists at the parent":**
  - es-toolkit has 51 relaxed candidates, and 50 are lost here: it adds each new function in a new file;
  - radashi loses 6 of 6, and valibot 2 of 2.
- **"Calls at least 2 repository functions":** the helper count uses a pattern that sees `def` and `function`, not
  arrow functions or method calls. Most JS commits drop here (hono: 404 commits of the right size, 8 left).
- **"A description of 5+ words":** hono loses its remaining 6 here (no JSDoc).
- **So:** at about 0.5 per repository, reaching about 80 from R3's 23 needs about 110 more repositories under the
  same rule. Two rule changes to decide after R3a:
  - allow a new function in a new file;
  - count arrow functions and methods as helpers.
  Either could change the yield several-fold. Not measured yet.

## Expected yield timeline per family *(projected from 12 of 48 repositories; gate pass rates unknown)*

| Family | Projected candidates (48 repositories) | Mining | Gate + oracle (roadmap D) | Ready for a pilot | Risk |
|---|---|---|---|---|---|
| tests | ~600 | today | 1–1.5 weeks (the "fails on the parent" oracle) | ~week 2 | low on volume |
| feature | ~300 | today | 1 week | ~week 2 | low |
| refactor | ~1,000 (mostly worded) | today | 1–1.5 weeks (the "refactor done" oracle needs design) | ~week 3 | the oracle design; worded candidates need a stricter filter |
| delete | ~150–200 | today | 1 week | ~week 3 | moderate: gate attrition unknown |
| move | ~20–25 | today | 1 week (+ B3 import sites) | **not at 40 without more repositories or a wider rule** (e.g. class moves; moves where the import is updated in the same file) | **high** |
| N | ~14 so far | today | 1–1.5 weeks | **needs the rule decision above** | **high** |

**Overall:** 6–8 weeks, as estimated. Mining ends today, so the long pole is gating and oracle work (about 5–6
weeks across families, run one after another on CPU), then the per-family pilots (Kaggle, after R3a). Move and N
are the two families whose volume is not yet assured.
