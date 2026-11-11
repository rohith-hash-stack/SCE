# R4 amendments

## R4-SANITY-2 (2026-11-11): Verification at done

**Status:** approved by the user (2026-11-11). A protocol change, applied to both arms with shared wording. It is a
development change in the sanity phase, not a pre-registered test, and goes into R4's pre-registration before the
main run.

**What changes:**
- **From the second sanity run on, a `done` call is accepted only if a gate verifies the task's goal** against the
  working tree. The gate uses checks the model could make itself (`done_gate.py`):

  | Family | Check |
  |---|---|
  | every family | at least one file changed |
  | delete | the target is no longer defined in its file, and no visible test that passed at the start (3 runs, flaky dropped) now fails. Deleted tests do not count |
  | feature | the listed fail-to-pass tests pass and the visible pass-to-pass tests still pass, with the test files restored to their start version for the check |
  | Family B | no new failure among the visible tests |

  The gate never uses the hidden scoring tests or PRISM's output.
- **A rejected done returns specific feedback.** It says what is still unmet: the target and its line, failing test
  names and the first error lines. It never lists call sites (user decision C1: listing them would shrink the
  PRISM-vs-baseline comparison; if ever added, grep-derived only). A rejected done counts as a turn.
- **Three rejected dones in a row** with no applied edit between them end the run as **`stuck_done`**. It counts as
  unresolved and is reported separately.
- **The task message states the gate and its checks** (`sanity_gate_tasks.gate_paragraph`). Both arms get the same
  gate, wording and limits.
- **Scoring is unchanged except for one thing.** The scored test files are restored to their start version before the
  hidden patch is applied: the files the hidden patch touches and, for feature, the task's test files. This fixes the
  first run's delete scorer bug, where the hidden patch did not apply over the agent's test edits.
- **A new counter, "gate accepted, scorer unresolved"** (user addition), measures how lenient the gate is
  (`sanity_report.gate_counts`).

**Why:** in the first sanity run, 25 of 37 failed runs ended with an accepted `done` while the goal was unmet. The
example is r4s-delete-02: done after 1 of 5 edits, with the target still defined and 58 tests failing. The protocol
intended `done` to mean "complete", but the model used it to stop. The gate enforces that intent.

**Not changed:**
- R3a's results;
- the turn cap (28), the window rule and the budget;
- the task prompts otherwise. In particular, the delete prompt still names the target by its index name: the
  explicitness fixes stay parked.
- The 12 runs that ended without calling done (edit-failure loops) are a separate problem; the edit-error fixes are
  parked until this run is read.

**Build check (before the run):** each task's developer change must pass the gate, and the unchanged start must not
(`sanity_gate_tasks.py`). For delete, the developer change is the source and test changes; for feature, the source
changes.

### Expectations, recorded before the second sanity run (user, 2026-11-11)

| Measure | Expected |
|---|---|
| Early done | near zero. **Accepted** early dones are near zero by construction; **attempted** early dones (rejected) are reported too |
| `stuck_done` | a new outcome; it will appear |
| Loop rate on the first run's 12 edit-loop runs | about unchanged: the gate does not help runs that never call done |
| Solve rate | uncertain |

**If the solve rate holds flat,** the edit-error fixes (currently parked) are the next step.
