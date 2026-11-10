# R4's family gates, as implemented (`gate_families.py`)

**The contract** is `ROADMAP_R4_R6.md` §2a (the user's decision, 2026-11-10). This file records how each signal gate
is checked on the mined candidates, and the choices the contract leaves open.
- **No model calls.** Each candidate is checked in a fresh git worktree at its parent, with R3's environment
  builders (`r3/shared_tools.make_env`: uv for Python, the repository's package manager for JS).
- **Draw:** R3's seeded draw order (`r3/gate.draw_order`).
- **Stopping:** the gate stops at `--want` passes or `--max-attempts` tries. A repository contributes at most
  `--per-repo` passes.
- **Log:** every attempt is one line in `gate_<family>.jsonl`.

## Shared definitions

- **"Tests pass":** every test file run produced results, at least one test passed, and none failed (skips
  allowed).
- **One run per state.** R3's gate used 3 runs per state to catch flaky tests. Here flakiness is checked when a task
  is calibrated, before any pilot.
- **"Related tests"** (delete, refactor, move), selected at the parent:
  - the commit's own changed test files;
  - plus up to 4 of the repository's runnable test files that mention the changed functions' names (whole words,
    names over 3 characters, no dunders) or the module (Python `pkg.mod`; JS a `dir/name` import tail), most
    mentions first.
  - A candidate with no related test file fails ("no related test file").
- **Family B's gate (G1)** is not used for refactor, move or tests (`ROADMAP_R4_R6.md` §2a).

## Per family

| Family | Contract | Implemented as | Candidates from |
|---|---|---|---|
| delete | tests pass at end, target absent | all the commit's changes applied; the related tests pass; the removed function (qualified name) is not defined in its file | `mine_families.py` delete |
| refactor | tests pass at start AND end; diff is structural | the related tests pass at the parent, and again with the commit's source files. "Structural": checked at mining (> 5 changed lines; a function's code differs once comments and whitespace are removed) | `refactor_candidates.py` (the refactor signature; no message filter) |
| move | tests pass at end; old path gone, new path exists | all the commit's changes applied; the related tests pass; the function is not defined in the old file and is defined in the new file | `mine_families.py` move |
| tests | score by fail-to-pass on the prior version | the commit's test files pass on the current code; 1+ of their tests fails with the target's file at its prior version (below) | `mine_families.py` tests |
| feature | visible failing test at start | the commit's test files, given at the start, have 1+ test failing (or absent) at the parent and passing with the commit's source | `mine_families.py` feature |

## The tests family's "prior version"

A tests-only commit leaves the source unchanged, so the developer's new tests also pass on the parent. "Fail on the
prior version" therefore needs an earlier version of the code under test.
- **The target:** the repository function or class the added test lines name most often. It must be defined in
  exactly one non-test source file; otherwise the candidate fails ("no named target defined in exactly one source
  file"). Benchmark-only commits usually fail here.
- **The prior version:** that file as it was before the last commit that changed it (`git log -1 <parent> --
  <file>`, then that commit's parent). Every other file stays at the current code.
- **Passes** when at least one of the developer's tests passes on the current code and fails, or does not run, on the
  prior version.
- **Recorded per candidate:**
  - whether the failures are assertions or collection errors (`f2p_kind`);
  - whether the target's own code differs in the prior version (`prior_differs_in_target`).
- **Why the second field matters:** a pass where the target is unchanged means the change that breaks the tests is
  elsewhere in the file. Such tasks are flagged for review at calibration.

## Open, for calibration (not decided here)

- the prompt's `[goal]` (refactor), `[destination]` (move) and `[feature spec]` (feature), from the commit;
- flakiness: 3 runs per state, as R3;
- the gold change's size relative to the stated change (R3a's investigation: `r3/R3A_FLOOR_INVESTIGATION.md` §4).
