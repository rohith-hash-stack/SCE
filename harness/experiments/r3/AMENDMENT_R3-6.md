# Amendment R3-6: the pilot window, widened for JS/TS Family B; Family N's pilot not run (2026-11-06)

Written after the pilot's first mining and gate, and before any pilot model call. It changes only the pilot's tasks.
R3a's rules, its main window (on or after 2024-12-01) and its tasks are unchanged. No pass/fail rule changes.

**What the first pilot gate found** (`IMPLEMENTATION_LOG.md`, 2026-11-06; `pilot/`):
- Family B: 10 tasks, all Python. vitest's 3 candidates are browser-mode tests, and no other JS/TS repository gave a
  candidate in 2024-10-01 to 2024-11-30. Pilot pass criterion 3 (tests run inside the loop in both languages) could
  not be met for JS/TS.
- Family N: 1 task of 10.

**1. The pilot window for JS/TS Family B is 2024-04-01 to 2024-11-30.**
- The pre-registration's pilot window ("Pilot-only tasks", 2024-10-01 to 2024-11-30) is kept for the Python
  repositories.
- For the JS/TS repositories (vitest, nitro, h3, execa, unstorage, msw, unplugin), Family B's pilot candidates come
  from 2024-04-01 to 2024-11-30.
- The purpose is only to admit JS/TS Family B tasks, so the pipeline is verified in both languages. "The pilot's role
  is pipeline verification, not scientific claim."
- Everything else is the same: the mining rules, the gate, the seeded draw (seed 6601), and 10 tasks. The target is
  at least 2 JS/TS tasks among the 10. A shortfall is reported, not forced.
- No pilot task can overlap R3a's tasks: every pilot commit is dated before 2024-12-01.

**2. Family N's pilot is not run.**
- Family N is deferred to R3b with its own mining plan (`pilot/README.md`). Its definition and the docstring/JSDoc
  rule are not changed.
- The pilot runs Family B only. The pilot criteria are checked on Family B.
