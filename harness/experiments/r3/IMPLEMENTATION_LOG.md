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
- **The full suite** (`pytest tests/`, 52 min): 3,125 passed, 22 failed, 4 errors. The 26 were re-run on the code
  before the fix (`50138edb`'s two source files restored, then put back):
  - **25 fail the same way before the fix, so they are pre-existing:**
    - 12 need the `transformers` package, not installed in this container;
    - the rest compare against recorded values (NLQ ranks, proof states, chunk counts) or need an npm dependency
      (`etag`) that is not installed here.
  - **1** (`test_fuzzy_seed_suggestion.py::test_fuzzy_match_performance_on_real_corpus`) passed before and failed
    after. It is a wall-clock assertion: a fuzzy match on Django under 50 ms. It measured 53.5 ms during the suite
    run, which shared the machine with the E1-E3 runs, and passed 2 of 3 standalone re-runs on the fixed code. The
    fix does not touch fuzzy matching, so this is a timing flake, not a regression.
  - **None of the 26 is caused by the fix.** The 25 pre-existing failures are left as they are: outside this fix's
    scope.
- E1-E3 were re-run with cold builds regenerated after the fix: E1, E2 (a-e, including the blast-radius answer equal
  to the new cold build, with the notice) and E3 pass on werkzeug and unstorage.

**Effect on R3:** R3's `prism_blast_radius` uses `include_tests=False`, so this fix changes R3's output: blast radius
no longer lists test helpers. It is part of the code R3 freezes. It does not affect R1 or R2: both are complete, and
their frozen tasks and contexts are unchanged.

## 2026-11-06: project framing

"2026-11-06 Project framing: single-builder, no external team. 'Team approval' / '[TBD — team]' / 'end of week'
language retired. Design choices recorded in amendments as before; clear reviewer decisions surfaced only when
genuinely needed."

## 2026-11-06: JS/TS builder self-check passes on all 7 repositories, so R3a runs full-scope (R3-5, Revision 4)

**What was run:**
- Command: `python -m harness.experiments.r3.jsbuild selfcheck CLONE REV1 REV2 --cache DIR --scratch DIR`, with code
  `0d75e6e4`.
- Revisions per repository: the earliest commit on or after R3's cutoff (2024-12-01), and HEAD. Together they span
  the task revisions' range.
- Per revision:
  - it installs from the lockfile, with a direct pnpm binary, not corepack;
  - it finds the test files;
  - a named test file passes, with machine-readable output;
  - 3 runs of that file give the same result.
- All 14 revisions, from one run on one version of the code:

| repository | revision | Node | package manager | runner | build in setup | named test file | tests passing | result |
|---|---|---|---|---|---|---|---|---|
| execa | `2aa3b0ce` | 22.22.0 | 10.9.4 | ava | no | `test/methods/override-promise.js` | 1 | pass |
| execa | `HEAD` | 22.22.0 | 10.9.4 | node:test | no | `test/methods/override-promise.js` | 1 | pass |
| h3 | `2aac4f98` | 22.22.0 | 9.7.1 | vitest | no | `test/unit/sse.test.ts` | 2 | pass |
| h3 | `HEAD` | 22.22.0 | 12.3.4 | vitest | no | `test/unit/h3.test.ts` | 1 | pass |
| msw | `48e4c760` | 18.19.0 | 9.14.0 | vitest | no | `src/core/graphql.test.ts` | 1 | pass |
| msw | `HEAD` | 24.22.2 | 11.27.1 | vitest | yes | `src/http/http.test.ts` | 1 | pass |
| nitro | `61e3f881` | 20.20.2 | 9.14.2 | vitest | yes | `test/presets/static.test.ts` | 1 | pass |
| nitro | `HEAD` | 24.22.2 | 11.28.10 | vitest | yes | `test/unit/virtual.test.ts` | 1 | pass |
| unplugin | `1b697de1` | 22.22.0 | 9.14.4 | vitest | no | `test/unit-tests/resolve-id/resolve-id.test.ts` | 5 | pass |
| unplugin | `HEAD` | 22.22.0 | 12.14.0 | vitest | yes | `test/unit-tests/unloader/index.test.ts` | 4 | pass |
| unstorage | `61fb931f` | 22.22.0 | 9.14.4 | vitest | no | `test/drivers/memory.test.ts` | 15 | pass |
| unstorage | `HEAD` | 22.22.0 | 11.28.6 | vitest | no | `test/drivers/overlay.test.ts` | 15 | pass |
| vitest | `721a5b84` | 22.22.0 | 9.12.3 | vitest | yes | `test/core/test/timers-node.test.ts` | 125 | pass |
| vitest | `HEAD` | 22.22.0 | 11.28.6 | vitest | yes | `test/unit/test/timers-node.test.ts` | 220 | pass |

**Failures on the way.** Every one was a builder bug, none structural, so none counted toward Revision 4's stop rule:
- **h3 HEAD:** picked pnpm 9 from lockfile format 9.0.
  - Fix: read the pnpm version the lockfile records (`packageManagerDependencies`); fall back to newer majors on
    `ERR_PNPM_BROKEN_LOCKFILE`.
- **msw early:** this container's `NODE_OPTIONS=--use-openssl-ca` is rejected by Node 18's worker threads.
  - Fix: drop worker-incompatible flags from the child environment. The proxy CA stays trusted through
    `NODE_EXTRA_CA_CERTS`.
  - Kaggle does not set this variable.
- **msw HEAD and nitro early:** the tests import build output (`./lib/vite`, and `nitropack/core` by the package's
  own name).
  - Fix: detect "could not resolve" and "failed to load url" errors in the full output and in the report's messages,
    then build in setup.
- **nitro early:** `better-sqlite3@8.7.0` has no prebuilt binary for Node 22, and its source does not compile
  against it.
  - Fix: on a native-module build failure, with Node not pinned exactly, retry with the newest satisfying release of
    older LTS majors. Node 20 was used.
- **vitest early:** run from the repository root, monorepo test folders lost their own vitest config.
  - Fix: run each file from the nearest folder with its own vitest, vite or jest config.
- **nitro HEAD (in a later run):** a named test inside `test/vite/vitest-fixture/`, a sample project, varied between
  runs.
  - Fix: skip any fixture-named folder.

**Still open for the Kaggle cell:**
- installing Node versions and the pnpm binaries in the cell, as here, through the same `jsbuild` functions;
- a per-repository install cache.

The pnpm store already caches between revisions here.

## 2026-11-06: both environment builders pass on R3's whole pool (36 of 36, one version of the code: `a09967d4`)

**Per repository, both the earliest post-cutoff revision and HEAD:**
- install;
- find the tests;
- a named test file passes, with machine-readable per-test output;
- 3 runs give the same result.

| builder | repositories | revisions passing |
|---|---|---|
| Python (`pybuild`) | sqlglot, poetry, litestar, packaging, multidict, yarl, typer, scrapy, aiohttp, werkzeug, tornado | 22 of 22 |
| JS/TS (`jsbuild`) | h3, unstorage, execa, unplugin, msw, nitro, vitest | 14 of 14 |

**What the Python builder needed** (each found by a failing revision, none structural):
- dependencies resolved as of the commit date when there is no lockfile (`uv --exclude-newer`): scrapy's `w3lib`;
- Poetry's test and dev groups (poetry's `httpretty`);
- git dependencies on a branch pinned to the branch's commit as of that date, kept through a `--no-deps` reinstall
  (poetry's `poetry-core`);
- C extensions built first, with a pure-Python retry (multidict's C-extension test variants);
- the repository's own pytest `addopts`, minus coverage and parallelism options:
  - aiohttp's `-p no:aiohttp` prevents a double plugin registration;
  - a TOML-list `addopts` is used token for token (packaging's `-m "not property"`).

**The JS/TS builder now resolves Node from the release index only:** the newest 22.x where the spec allows it,
otherwise the newest satisfying LTS release. It never uses the machine's own Node, so this container and Kaggle
resolve every revision identically. That is why these runs show Node 22.23.4 where the earlier ones showed the
system's 22.22.0. It also works with no Node installed (checked).

**`run_tests`** (`shared_tools.SharedTools`) runs through whichever builder fits the repository (`make_env`). Both
were in place before the shared tools were built (BUILD_PLAN, section 6, item 1).

## 2026-11-06: the qwen notice check ran on Kaggle (results at 2bbf531b)

- **JS/TS smoke test on Kaggle (install only):** h3 at `f0e17808` **passes**: Node 22.23.4 from the release index,
  pnpm 12.3.4, install in 4.1 s. No new structural problem.
- **Notice:** acted on in 5 of 5 runs (R3-5, results table).
- **All 5 runs hit the 30-turn cap without `done`** (~200k of the 300k budget, ~2 minutes each). Replaying seed 42's
  calls locally against the real tools shows why:
  - turn 3: `edit_file` `old="except ValueError:"` is refused because it occurs twice in the file (correct behaviour);
  - turns 4–30: the model reads lines 159–163, then sends
    `old="except ValueError:\n              return default"`, with 14 spaces. The file has 12. `read_file` prints
    `f"{i:>5}  {line}"`, so the model took the gutter's two separator spaces as part of the indentation. Every later
    edit fails "not found"; the model widens the read by one line each time and repeats.
- **Shared-tool defect, not a model result.** Both versions run the same `read_file` and `edit_file`, so parity holds.
  It would still cost resolved runs and turns in both arms, so it is fixed before the pilot.
- **Seeds:** 4 of the 5 seeds produced byte-identical 30-turn trajectories at temperature 0.2. Seed 3003 diverged at
  turn 4. Sampling does vary; the model is very peaked on this task.

## 2026-11-06: shared-tool fix for the edit loop (follow-up to 4ccc16a5; no amendment)

This is a shared-tool bug, not a change to PRISM's behaviour. Both versions get the fix equally. No rule or
hash-committed file changes. The entry above is accurate as written: the replay, the 14-vs-12 spaces, ~200k tokens and
~2 minutes per run.

- **`read_file`:** each line is now `{n}\t{line}` (was `f"{n:>5}  {line}"`). Indentation is spaces and the separator is
  one tab, so the prefix cannot be read as indentation. `read_file` is the only tool that renders line numbers. `grep`
  keeps git grep's `path:n:line`, whose separator is a colon.
- **`edit_file`:** when the text is not found but matches exactly once apart from leading and trailing whitespace
  (line by line; the first line may end a file line and the last may begin one), the error says so, gives the line,
  and says the line-number prefix (the number and a tab) is not part of the file. It does not apply the edit. With no
  whitespace match, or with more than one, the error is unchanged.
- **Regression test** (`tests/test_r3_shared_tools_edit.py`, 7 tests):
  - It uses a file shaped like werkzeug's `headers.py` at 2adaad1a: `return default` on line 163 and a second
    `except ValueError:`.
  - It reads lines 159–163 and counts line 163's indentation as the model did: drop the number, and one tab if there
    is one.
  - It then sends the edit.
  - With the tab: 12 spaces, and the edit succeeds.
  - With the old gutter: 14 spaces, and the edit is "not found".
  - Put back the old gutter, and the two tab tests fail (checked). With the fix, all 7 pass.
- **Replay on real werkzeug at 2adaad1a** with the fixed tools:
  - turn 3 is still refused (2 occurrences);
  - turn 4 shows `163\t            return default`;
  - the Kaggle run's 14-space edit gets the new error naming line 162;
  - the 12-space edit succeeds.
- **Seed variance:** At temp 0.2, qwen2.5-coder:14b produced identical turns on 4/5 seeds for the notice-check task.
  Two-seed results should be read knowing the model is nearly deterministic on shallow tasks; variance is expected to
  come from task depth, which is what the scaling test measures.
- **Before the pilot, not now:** the pilot logs `done` explicitly per run, per arm. A run that reaches the 30-turn cap
  without `done` is a named failure condition in the analysis ("stuck"), not silently "unresolved". This tells "PRISM
  didn't help" apart from "the agent got stuck". (The run summary already records `end`: `done`, `turn_cap` or
  `budget`; the analysis and the pilot report must carry it.)
- **Next:** re-run the notice check on Kaggle (5 calls, same setup). The criterion is whether the agent reaches `done`
  in all 5 runs. If not, there is a second bug: it is reported, not piloted around.

## 2026-11-06: notice-check re-run after the shared-tool fix: the fix works (results at af5240ac)

Code c60381da, the same setup and revisions, results in `reports/r3/notice_check_2/`.

- **Criterion met:** all 5 runs end with `done` (`ended_with_done: 5`, `stuck_at_turn_cap: 0`). That is 6 turns each,
  ~26k tokens of the 300k budget, ~25 s of model time. Before the fix it was 30 turns, ~200k tokens and ~2 minutes per
  run.
- **Notice:** shown on turn 1 in all 5 runs, and the next call is `read_file` on the named file (5 of 5, as before).
- **Same sequence in every run:**
  1. `prism_change_context`;
  2. `read_file` 122–163;
  3. `edit_file` on `except ValueError:`, refused because it occurs twice;
  4. `read_file` around lines 160–163;
  5. `edit_file` with the 12-space indent, which succeeds at line 162;
  6. `done`.
  The whitespace hint was never needed: the tab separator alone fixed it.
- **Final diff checked by replaying each run's calls** (seeds 42, 2002, 3003, 4004; 1001 is byte-identical to 42):
  `Headers.get` now has `except (ValueError, TypeError):`, and `getlist`'s `except ValueError:` is untouched. That is
  the requested change and nothing else.
- **Observation, not a criterion:** no run repaired the line the check broke in `Headers.__repr__` (line ~603). The
  task does not ask for it. The agent read only lines 122–163, so the final tree still does not parse. The notice
  works as a pointer to the file; it does not make the agent look for the break. Pilot tasks start from a clean
  tree, so this matters only for the agent's own broken edits mid-run. The pilot's per-turn logs (parse gate, notice
  turns) will show whether agents leave their own breaks behind.
- **Seeds:** 42 and 1001 are byte-identical over all 6 turns. 2002, 3003 and 4004 differ only in turn 4's read range
  and in the wording of the `done` summary. This is consistent with the seed-variance note above.
- **Status:** the harness is ready for the pilot apart from the items BUILD_PLAN lists before it: pilot task mining
  and gating for both families, the analysis code, and the explicit `done`/stuck reporting (section 4, item 6).

## 2026-11-06: two notes entering the pilot (log entries only; no amendment)

**1. Open question for the pilot: the planted-break case.** Notice check 2 verified notice → read →
complete-scoped-task (5/5). It did not test notice → read → repair-agent-generated-break. The planted break in
Headers.__repr__ was outside the task scope, and the agent correctly left it alone in all 5 runs. The pilot's per-turn
logs and the stuck-vs-unresolved distinction will show whether an agent-generated break is repaired, stalls, or counts
as unresolved. Genuine open question entering the pilot, not a covered case.

**2. Cost picture for R3a.** Real turn counts were 6 per run on notice-check tasks, against the earlier 15–25 estimate.
If R3 tasks average 6–12 turns, R3a's call count is likely ~2–3× lower than the ~10–17k pre-run estimate. The pilot's
turn distribution will give the real number; do not re-plan on this yet, but note it for the R3a cost forecast.
