# Amendment R3-10: the interim readiness check, an analysis bug fix (2026-11-09, before the valid interim)

Recorded verbatim, as written by the user:

"The interim readiness rule (R3-1) requires the first 40 tasks in frozen order to have their runs. The analysis code checked this by counting any task with a run result, including error results pending retry. Tasks B-01 and B-02 (prettier) had errored on missing yarn and were awaiting retry in session 2. Because the readiness check counted them as finished, it declared the interim ready and computed it with those 8 runs scored as failures.

The readiness rule is fixed: a run counts as finished only if it has a real result. B-01 and B-02 must succeed before the interim runs. The 'interim reached' flag is fixed to count the first 40 tasks in frozen order, not any 40 tasks.

This is a bug fix to the analysis code, not a rule change. The rule (R3-1) was always 'first 40 tasks complete.' The code's implementation was wrong. The fix makes the code match the rule.

The invalid interim computation (PRISM 0.000, baseline 0.025, p = 0.32) is recorded but not interpreted as a result. Its summary was viewed to notice the readiness bug; no per-task results were opened. The valid interim will be the one computed after session 2 retries B-01 and B-02 successfully. The invalid computation's numbers are not to be cited as a finding.

The analysis code is frozen again after this fix; the cell re-renders at the new commit."

## Implementation

- `harness/experiments/r3/analyze.py`:
  - `load_runs` marks a run that has only a harness `.error.json` (no score file) as `pending_retry`.
  - `interim_look` treats such a run, like a missing one, as not finished.
  - An `error` outcome inside a score file (the model call failed during the run) is a finished run, and still
    counts as a death in the `invalid` check.
- `kaggle/r3a_cell.template.py`: `progress()` sets `interim_reached` only when the first 40 tasks of the frozen order
  have a score file for every run.
- Tests (`tests/test_r3_interim.py`): a harness error awaiting retry is not finished; a loop-recorded error is.
- **Checked on session 1's real results:** `interim_look` now returns
  `{'ready': False, 'tasks_missing_runs': ['r3a-B-01', 'r3a-B-02']}`.
