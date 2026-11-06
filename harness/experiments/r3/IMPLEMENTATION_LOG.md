# R3 implementation log

Dated entries for product fixes and harness decisions that do not change R3's experiment design. Design changes are
amendments (`AMENDMENT_*.md`); the pre-registration is hash-committed at `e711bcfb`.

## 2026-11-06: test-file detection bug fixed (blast radius listed test code with `include_tests=False`)

"2026-11-06 Test-file detection bug fixed. blast_radius with include_tests=False was still listing callers from some
test file patterns (e.g. test/drivers/*.test.ts in unstorage). Added missing patterns: `spec/` and `specs/`
directories; `.mjs`, `.cjs`, `.mts` and `.cts` forms of `*.test.*` and `*.spec.*`; and the walk now treats any
symbol defined in a test file as test code. Confirmed on 7 TS + 3 Python repos. E1–E3 re-run and pass after fix.
R3's blast_radius uses include_tests=False — an explicit override in `harness/experiments/r3/tools.py`
(the MCP tool's default is True)."

**Root cause:**
- The file patterns were not the failure: `test/drivers/*.test.ts` already matched `is_test_file`.
- Blast radius's walk (`prism.packer.candidate_index._upstream_walk`) excluded only `SymbolRole.VERIFICATION`
  symbols.
- A helper in a test file whose name and body are not test-shaped (`additionalTests` in unstorage's driver tests)
  keeps an implementation role, so it passed the filter.

**Patterns before the fix** (`prism.scanner.entrypoint_gate.TEST_SEGMENT_PATTERNS`, which `_in_test_file` and role
classification use):
- directories `test/`, `tests/`, `__tests__/`, `testing/`;
- `test.py` and `tests.py`, `conftest.py`, `test_*.py`, `*_test.py`, `*_test.go`;
- `*.{test,spec}.{ts,tsx,js,jsx}`.

**The fix** (only what was missing; no refactor; no interface or parameter change):
- `candidate_index.py`: a new `_is_test_code` (a verification-role symbol, or any symbol in a test file), used for the
  seed check and for the caller filter in `_upstream_walk`.
- `entrypoint_gate.py`:
  - added `(^|/)specs?/`;
  - extended the extensions to `ts|tsx|js|jsx|mjs|cjs|mts|cts`.

**Measured** (300 seeded production functions per repository, seed 5501; blast radius with `include_tests=False`):

| repository | language | before: functions whose blast radius lists test code (test callers listed) | after |
|---|---|---|---|
| vitest | TS | 18 (41) | 0 (0) |
| nitro | TS | 43 (236) | 0 (0) |
| h3 | TS | 123 (1,061) | 0 (0) |
| execa | TS | 1 (1) | 0 (0) |
| unstorage | TS | 90 (394) | 0 (0) |
| msw | TS | 5 (7) | 0 (0) |
| unplugin | TS | 19 of 179 (19) | 0 (0) |
| werkzeug | Python | 23 (280) | 0 (0) |
| aiohttp | Python | 85 (3,864) | 0 (0) |
| loguru (held-out) | Python | 107 of 247 (5,685) | 0 (0) |

- **Every repository had the bug.**
- The added patterns (`spec/`, `.test.mjs` and the like) match no file in these 10 repositories. They close the
  standard list but did not cause the observed leak.

**Remaining gap, outside the requested list and not changed:**
- TypeScript type tests (tsd and `expectTypeOf`): execa's `test-d/` directory and `*.test-d.ts` files are not test
  files to `is_test_file`.
- `prism.query.nlq` already excludes `*.test-d.ts` from search only.

**Checks:**
- 159 PRISM tests in the affected areas pass.
- The full suite was running when this entry was committed; its result is added below when it finishes.
- E1-E3 were re-run with cold builds regenerated after the fix: E1, E2 (a-e, including the blast-radius answer equal
  to the new cold build, with the notice) and E3 pass on werkzeug and unstorage.

**Effect on R3:** R3's `prism_blast_radius` uses `include_tests=False`, so this fix changes R3's output: blast radius
no longer lists test helpers. It is part of the code R3 freezes. It does not affect R1 or R2: both are complete, and
their frozen tasks and contexts are unchanged.
