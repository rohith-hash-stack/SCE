# Amendment R3-8: R3a's depth: a bounded expansion of the Family B pool; vitest browser mode deferred (2026-11-07)

Written after R3a's main-window mining and gate (`r3a_tasks/`, `IMPLEMENTATION_LOG.md` 2026-11-07), and before R3a's
hash-commit. No model call has been made.

**Where it starts:**
- 66 gated Family B tasks. 14 of them (21%) have 2 or more changed callers: 10 with 2, 3 with 3-4, 1 with 5+.
- Family N: 23 gated. Confirmed moved to R3b (decision of 2026-11-07); no action here.

**The decision** (2026-11-07, verbatim):

"R3a's scaling test requires ≥30 gated Family B tasks with 2+ changed callers. Current pool: 14. One week is
budgeted to expand the pool through additional repository mining. New repositories are added outside the original
18; this amendment records the expansion.

The week proceeds if and only if the pre-screen shows at least one candidate repo with a deep share (2+ caller
tasks) roughly twice the current pool's 21% (i.e., ≥40%). If no repo screens at that threshold, the week is
cancelled and R3a runs with the scaling test reported as not run.

If the week proceeds: mine and gate up to 4 repos, stop at end of week. If gated deep tasks reach 30, the scaling
test runs with a power note. If not, scaling is reported as not run. The threshold of 30 is not changed.

Representativeness caveat: 21% deep is a property of how real code changes, not a mining artifact. A shallow R3a is
representative of real work; a deep R3a is not. Whichever sample R3a runs on, the results must state its
representativeness in the interpretation section."

"Vitest browser-mode recovery is deferred. It would add ~3 deep tasks, requires Kaggle browser install, and is not
on the critical path to R3a."

**The pre-screen:**
- Repositories (8):
  - named in the decision: mypy, sqlalchemy (Python); rollup, prettier (JS/TS);
  - chosen to match: 2 more Python and 2 more JS/TS (listed in `IMPLEMENTATION_LOG.md` with the reason for each,
    before the screen runs).
  - Not screened: django, pandas, vite (custom runners, build cost, browser dependency).
- Window: R3a's main window (2024-12-01 to the clone's HEAD).
- What is measured: R3a's Family B miner on the whole window, the same rules as the 18 repositories. A repository's
  deep share is the share of its mined Family B candidates with 2 or more changed callers.
  - The 21% reference is the gated pool's share (14/66). The mined pool's share is 30/132 = 23%.
- Reported per repository: commits kept, candidates, and the caller-count distribution. The screen does not gate.
- Threshold: at least one repository at a deep share of 40% or more. A repository with very few candidates is
  reported with its count, so that a share from 1 of 2 is not read as 50% without its count.

**If the week proceeds:**
- Up to 4 repositories, chosen from the screen by deep candidates.
- The same miner and gate as the 18: seed 5501, at most 10 per repository, the used-repository rule.
- The draw continues the current one: the new repositories' candidates join the pool, and the seeded draw is run over
  the whole pool, so the existing 66 stay.
- The week ends 5 working days after the screen is reported. What is gated by then is final.
