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

## Yield (43 of 48 repositories, 2026-11-10 05:20 UTC)

**Still running:** pydantic-ai (5,263 commits in the window), fastmcp (4,311), pdm, hatch and instructor, one process
per repository. A sandbox restart killed the sequential run twice at pydantic-ai. This section is updated when they
finish.

**Candidate lists:** `harness/experiments/r4/candidates/`, one file per family, with one entry per commit. None is
gated.

| Family | Candidates | Largest contributors | Note |
|---|---|---|---|
| delete | 276 | svelte 36, nitro 31, vitest 25, prettier 22 | removed function, with callers at the parent, and the callers updated |
| move | **31** | prettier 8; no other repository above 3 | **the thin one** |
| refactor | 696 | sqlglot 149, nitro 108, prettier 60, textual 51 | 68 renames with callers updated; 628 refactor-worded commits with no test change (noisy) |
| tests | 523 | fastify 103, es-toolkit 52, scrapy 51, textual 38 | test-only commits adding test functions that name repository functions |
| feature | 291 | scrapy 44, es-toolkit 28, nitro 20, vitest 19 | 2+ new production functions, named by a changed test |
| **N** (R3's rule) | 14 from 21 new repositories | | about 0.7 per repository |

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

## Expected yield timeline per family *(gate pass rates unknown)*

**Caution from R3a's investigation (2026-11-10):**
- R3a's gate admitted tasks whose visible tests all pass at the start (18 of 80), or that had no visible tests (5).
- It also admitted tasks whose developer change goes well beyond the stated change (54 of 62 one-caller tasks).
- Gate rules for these families are a scope decision. A stricter gate (a visible failing test at the start; a gold
  change close to the stated change) will lower every number below by an unknown fraction.

| Family | Candidates | Gate + oracle (roadmap D) | Ready for a pilot | Risk |
|---|---|---|---|---|
| tests | 523 | 1–1.5 weeks (the "fails on the parent" oracle) | ~week 2 | low on volume |
| feature | 291 | 1 week | ~week 2 | moderate: feature commits are where R3a's "gold beyond the prompt" problem is worst |
| delete | 276 | 1 week | ~week 3 | moderate: gate attrition unknown |
| refactor | 696 (68 renames) | 1–1.5 weeks (the "refactor done" oracle needs design) | ~week 3 | the oracle design; worded candidates need a stricter filter |
| move | 31 | 1 week (+ B3 import sites) | **not at 40 after any gate attrition** without more repositories or a wider rule | **high** |
| N | 14 so far | 1–1.5 weeks | **needs the rule decision above** | **high** |

**Overall:** 6–8 weeks, as estimated. Mining is done except 5 repositories. The long pole is gating and oracle work
(about 5–6 weeks across families, on CPU), then the per-family pilots on Kaggle. Move and N are the two families
whose volume is not assured.
