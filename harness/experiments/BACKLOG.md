# Post-R3 backlog (not R3 work; not scheduled)

- **The 25 pre-existing test failures** (recorded 2026-11-06; detail in
  `harness/experiments/r3/IMPLEMENTATION_LOG.md`, the test-file fix entry):
  - 12 need `transformers`;
  - the rest are outdated recorded expectations, or need npm packages not installed (`etag`) or checkpoints.

  Triage into environment gaps (clean skips) versus real drift (re-record only if the change was intended). None is
  R3-related.
- **TypeScript type-test files** (`test-d/`, `*.test-d.ts`) are not test files to
  `prism.scanner.entrypoint_gate.is_test_file`.
- **The flaky wall-clock test** `test_fuzzy_seed_suggestion.py::test_fuzzy_match_performance_on_real_corpus`
  (a 50 ms threshold).
