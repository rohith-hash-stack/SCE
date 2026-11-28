# R3a task list: hash-committed (2026-11-08)

**This is the R3a task list.** It supersedes the earlier base `ba4de9ae…` (kept as `tasks_B.ba4de9ae.json`,
`gate_B.ba4de9ae.jsonl`, `attrition_B.ba4de9ae.json`).

| | |
|---|---|
| File | `harness/experiments/r3/r3a_tasks/tasks_B.json` |
| sha256 of the task list (`json.dumps(tasks, sort_keys=True)`, as `tasks_sha256` in the file) | `70a6aede19a0dd08997c45d4479bd6f92e7a5a44fa91cc48a7027189a6687921` |
| sha256 of the file's bytes | `9d713867fc65357de0b5e4c697c2fdc3fde5080618881c26d196d17b070a576b` |
| Status | `frozen`: 80 Family B tasks, `r3a-B-01` to `r3a-B-80` |
| Gate | seed 5501, target 80, at most 10 per repository, used repositories at most half; 192 candidates, 137 drawn |

**Changes from `ba4de9ae`** (the full-pool bug verification, `IMPLEMENTATION_LOG.md`, 2026-11-08):
- **r3a-B-09 (pydantic), definition corrected, same id.** The anchor gained `@overload` stubs in the commit. The
  old start patch replaced `ModelPrivateAttr.get_default` with a bodiless stub. The start patch and prompt now
  carry the commit's whole overload group (`repair_overloads.py`). Re-gated: 50 fail-to-pass, 79 pass-to-pass (the
  same counts); gold resolved at F1 1.0; empty unresolved.
- **The old r3a-B-29 (h3 `validateData`) is out.** It had the same stub defect. With the corrected start patch it
  has no fail-to-pass test: its 28 tests failed only because the body was missing.
- **Refill:** the gate resumed on the same draw order, and svelte `220b526d` passed. It is now r3a-B-26
  (`build_event`, 2 changed callers).
  - Failures before it: rollup ×2 (known attrition, install) and prettier ×1 (no fail-to-pass test).
  - Dry run (PRISM gold resolved, BASELINE empty unresolved, pad 4, cap 30, 0 orphans) and anchor check (in the
    index, 0 test callers) pass for the new task and for the repaired B-09.
- **Ids:** the refill's draw position shifts three ids (old B-26, B-27 and B-28 are now B-27, B-28 and B-29).
  Renumbering was accepted.

**Caveats, recorded with the hash** (the interpretation must state them):
- **80 Family B tasks:** Python 41, JS/TS 39, across 16 repositories.
- **Depth:** 18 deep tasks (2 or more changed callers: 14 with 2, 2 with 3-4, 2 with 5+).
- **Scaling test: not run — insufficient depth variance after pool expansion** (pre-committed: it needed 30 deep
  tasks; the recorded rules allowed at most 28).
- **Representativeness:**
  - 62 of 80 (78%) are one-caller, close to the real change share (77% of the main window's mined candidates).
  - The 4 repositories added under R3-8 were chosen partly on deep share, so the deep tasks are not
    representative of real code.
- **Family N is dropped to R3b.** R3a is Family B only.
- **Known attrition** (not counted toward the refill's stop rule): rollup install failures, vitest browser mode.
