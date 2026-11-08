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

## 2026-11-06: pilot tasks mined and gated; analysis code; stuck vs unresolved (no model calls)

**Code** (the same for the pilot and for R3a; only the dates differ):
- `mine.py`;
- `gate.py`;
- `analyze.py`;
- `dryrun.py`;
- loop and runner changes.

Pilot artifacts are in `pilot/` (yield, candidates, gate log, attrition, frozen tasks).

**Mining rules** (R3a's own; the pilot window is 2024-10-01 to 2024-11-30, by committer date):
- **Family B:** R1 forward's miner, the suite's type 1:
  - `change_history/mine.py --forced`, pass 1 and pass 2 (production callers in PRISM's index at the parent);
  - `select.py --forced --no-self-callers`'s caller filter;
  - one task per commit;
  - plus at least one changed test file.
- **Family N:** pool_scan's relaxed rule, plus the three things suite type 2's prompt needs: the function's file
  exists at the parent, its class exists, and it has a description of at least 5 words.
  - pool_scan's "non-nested" test was a no-op (`"." not in q.split(".")[-1]`). The miner applies type 2's real
    test.
- **Both families:** backports, cherry-picks and repeats of one diff (`git patch-id`) are dropped first.
  - The first gate drew aiohttp's e80d5854 three times: the commit and its 3.10 and 3.11 backports. Those are not
    independent tasks.
  - 304 of aiohttp's 584 window commits are backports.

**Yield per repository** (`pilot/yield.json`):

| repository | commits in window (kept) | B candidates | N candidates |
|---|---|---|---|
| sqlglot | 190 | 4 | 1 |
| poetry | 81 | 4 | — |
| litestar | 68 | 2 | — |
| aiohttp | 584 (262) | 2 | 0 |
| vitest | 177 | 3 | 0 (2 relaxed; 1 with file and class; 0 with a JSDoc) |
| packaging, multidict, yarl, typer, scrapy, werkzeug, tornado, nitro, h3, execa, unstorage, msw, unplugin | 9-238 each | 0 | 0 (packaging, scrapy, nitro, h3) |
| **total** | 1,769 (1,447) | **15** | **1** |

- pool_scan's cheaper rules, run over the same window, give 42 B and 5 N candidates. Only 4 of those 5 are in Family
  N's pool repositories.
- So the window itself is thin, before any of the miner's extra steps.

**Gate** (`gate.py`; seeded draw, seed 6601; F2P/P2P over 3 runs per state; scoring check through `runner.score`):

| family | candidates | drawn | passed | failed (reason) |
|---|---|---|---|---|
| B | 15 | 15 | **10** (frozen, sha256 `bf87d595…`) | vitest 3 (browser-mode tests, no results in this environment); litestar 1 and poetry 1 (no fail-to-pass test) |
| N | 1 | 1 | **1** (short of 10) | — |

- **Tasks:** sqlglot 4, poetry 3, aiohttp 2, litestar 1 (B); sqlglot 1 (N). All are Python, and every B task is in
  depth stratum "1" (one changed caller).
- **Scoring check:** on every task the developer's patch scores resolved, with file and function F1 1.0, and the
  unchanged start state scores unresolved.

**Fixes the gate found** (each made, then everything re-gated from scratch on one code version):
- **pybuild:**
  - PDM dev-dependency groups (litestar before uv);
  - every optional extra first, then only the test extras (litestar's tests import redis);
  - test extras requested by name when pyproject declares none (sqlglot's `setup.py` dev extra: duckdb);
  - a release that was yanked later is pinned exactly (PEP 592; poetry's cleo 2.2.1).
  - The Python self-check still passes 22 of 22 revisions with these install changes. The yanked retry runs only
    after a failed install, so it cannot change a passing one.
- **gate:**
  - hidden test modules in folders the builder's own list skips are run (litestar's `tests/examples`);
  - fixture-only test changes run the test modules that name the fixture (sqlglot's `qualify_columns.sql`);
  - visible tests are the runnable test files that exist at the parent;
  - "no results in this environment" is reported apart.
- **runner.score:**
  - the agent's files and functions are diffed from a snapshot of the start state;
  - the old base (parent minus the start patch's files) lost callers in the anchor's own file, so the developer's own
    patch scored file F1 0.0;
  - the gold side uses the same code.
- **jsbuild:** a repository whose tests use built output is rebuilt before `run_tests` and before scoring, when the
  tree changed since the last build. Without this, both would test the start state's build.
- **edit_file:** a span is unique only when no second match starts anywhere. `str.count` skips overlapping matches,
  so such a span passed as unique, and the first match was edited.

**Stuck vs unresolved (BUILD_PLAN section 4, criterion 6), wired:**
- The run summary has `done_called` and `stop` (done, stuck, budget or error), plus per-run tool counts.
- The turn log keeps the first 300 characters of each tool output.
- A harness death writes `.error.json`.
- `runner.outcome` gives one of `resolved`, `unresolved`, `stuck`, `budget` or `error`. `analyze.py` adds `missing`,
  and its primary table shows every category per family, version and seed.

**Analysis code** (`analyze.py`, `tests/test_r3_analyze.py`, synthetic runs end to end):
- Wilcoxon, McNemar per seed, Holm, the bootstrap (suite `stats.py`), labels and `invalid`, per-seed results and
  `unstable` flags, scaling (Spearman plus the deepest-vs-shallowest bootstrap), pass@2, and secondaries;
- the pilot criteria and the turn-cap rule;
- the Family B interim boundaries. The first version used α*(t) = 2 − 2Φ(z_{α/2}/√t), which does not give R3-1's
  table. The form 4 − 4Φ(z_{α/4}/√t) reproduces it exactly: 0.0008/0.0247 and 0.0031/0.0490.

**Metrics the analysis surfaced** (flagged; no rule changed):
1. **A cap-hit run is unresolved even when its tree passes** (pre-registration: "they count as unresolved"). The
   primary counts only `done` and passing. The tree's own pass is kept as a diagnostic column (`tree_resolved`),
   never in a test.
2. **"The first turn at which every F2P test passes"** (a pre-registered process measure) cannot be computed. The
   hidden tests are never in the tree during the run, and running them per turn would leak them or add a test run
   per turn. It is not in `analyze.py`.
3. **The file-F1 answer key is every non-test file the commit changed,** changelogs and lockfiles included
   (aiohttp's `CHANGES/*.rst`, poetry's `poetry.lock`), as the pre-registration's "against the merged commit" reads.
   An agent rarely edits those, which lowers file F1 in both versions alike.
4. **Visible tests are the parent's versions.** When the developer's commit changed them, the correct change fails
   some visible tests (pilot-B-08: 2 of 11), in both versions. That is the realistic case, but it can mislead an
   agent.

**Dry run** (`dryrun.py`: the real runner on all 11 pilot tasks, both versions, no model calls):
- **Two scripts per task and version:**
  - the developer's change, replayed through `edit_file` (a deleted file is emptied: the tools cannot delete);
  - `done` at once.
- **Result: 44 of 44 runs ended as expected.** Every replay scored `resolved` with file and function F1 1.0, and
  every immediate `done` scored `unresolved`.
- **`analyze.py --pilot` on those run files:** criteria 1 (no harness error), 3 (tests found and run) and 5
  (scoring path) pass. The outcome split is reported per version and seed.
- **It found a memory leak.** One process kept every finished run's index (PRISM's server cache and the parse cache;
  every run has its own shadow path), and the dry run was killed at 4.2 GB after about 30 runs.
  - `ToolLayer.close()` now frees both.
  - 6 runs in one process now hold at 330-360 MB (before: 547 to 1,348 MB).
  - About 8 MB of growth per run remains. That does not matter for the pilot's 20 runs. **For R3a's 640 runs, the
    Kaggle cell should run each task in its own process** (`runner.py`'s command line does).

**Shortfalls and open points entering the pilot** (reported; nothing substituted):
1. **Family N: 1 pilot task of 10.** The window gives 1 candidate under R3a's rules, and 5 under pool_scan's looser
   rules, only 4 of them in N's pool repositories.
   - Mined as a check, never used: 2024-04-01 to 2024-09-30 adds 1 more (aiohttp).
   - vitest has 4 relaxed candidates there, and none has a JSDoc description.
   - The description requirement (the prompt gives "the signature and the docstring or JSDoc") removes most N
     candidates.
2. **No JS/TS pilot task.**
   - All 3 of vitest's B candidates are browser-mode tests, which produce no results without a browser.
   - No other JS/TS repository gives a B candidate in the window.
   - So pilot criterion 3 (tests run inside the loop in both languages) cannot be met for JS/TS.
   - The earlier window (a check only) has 19 JS/TS B candidates: vitest 11, execa 8.
3. **Depth:** all 10 B tasks are in stratum "1" (one changed caller). The pilot says nothing about deep tasks.
4. **R3a's own sizes are at risk.** This is a projection from the pilot window, to be replaced by mining the main
   window.
   - Family B's candidates come from 5 repositories here.
   - At ~10× the window, and at most 10 per repository, that is about 50 before the gate.
   - vitest's are mostly browser tests.
   - Family N projects to well under 50, so it would move to R3b by amendment R3-2, item 5.

## 2026-11-07: widened pilot gate (amendment R3-6); builders kill a test's process group (no model calls)

**Widened pilot gate** (`pilot/widened/`, gate code `1db22821`, seed 6601):
- 34 Family B candidates: Python 12 (2024-10-01 to 2024-11-30), JS/TS 22 (2024-04-01 to 2024-11-30: vitest 14,
  execa 8; nitro, h3, unstorage, msw, unplugin 0).
- 19 drawn, 10 passed: sqlglot 2, aiohttp 2, poetry 1, execa 5. **5 of the 10 are JS/TS**, so the R3-6 target (at
  least 2) is met. All 5 run under node:test.
- Failures: vitest 7 (6 give no test results in this environment, as browser-mode tests; 1 failed to install);
  litestar 1 and poetry 1 (no fail-to-pass test).
- Every task has exactly 1 changed caller.
- The gate was first run on the code before `1a997fdf` (the snapshot-owner fix), and the container restarted at 9 of
  10. It was re-run from scratch on one version of the code; the partial run is not kept in the repository. The
  re-run gave the same outcome for every candidate the partial run reached (9 passes, the same 9). Only the reason
  text for vitest 48c502fbeb changed ("no runnable hidden test file" became "no test results"). The re-run's 10th
  pass, dce0393b04, had not been reached.

**Leftover test processes** (fixed at `1db22821`):
- execa's IPC tests leave child `node` processes running after the test call. 45 were left during the first widened
  gate, about 3 GB in all.
- The builders' `_run` (both languages use it) now starts each command in its own process group and kills the group
  when the command ends or times out (`tests/test_r3_run_group.py`).
- `memtest.py` now also fails a run that leaves any process in its `/tmp/r3run_*` worktree. Before, it checked only
  that its own child had exited, which would not have caught this.

**Container restarts:** two restarts killed vitest's main-window mining (over 2 hours in one process). It now runs
in 8 quarterly chunks, each its own process, with a done marker per chunk, so a restart loses only the chunks in
flight. The mining rules apply per commit. The one check that spans the window (the same diff by `git patch-id`) is
re-applied over the whole window when the chunks are merged.

## 2026-11-07: 20-run memory test (BUILD_PLAN section 7): fails one check

- Code `ca9b4368`; 20 runs on the widened pilot tasks; `memtest/memtest_2026-11-07.json`.
- Driver +0.4 MB; every child exited; no leftover processes; peak drift at most 3.1%; 20 of 20 runs as expected.
- **Fails "no late growth within a run"**: runs 9 (+186 MB), 16 (+101 MB) and 19 (+167 MB). Each is a single step
  late in the run, flat afterwards (8 to 14 flat samples to the end), and repeated within 3% in the other pass.
- Recorded as a failure. The rule is not changed after seeing the result. Whether to re-specify the check is open.

## 2026-11-07: R3a main window mined and gated (no model calls; `r3a_tasks/`)

**Mining** (2024-12-01 to HEAD, 18 repositories, both families, the pilot's rules):
- Family B: 132 candidates from 16,556 kept commits. Changed callers: 1: 102, 2: 21, 3-4: 7, 5+: 2.
  - By repository: vitest 31, sqlglot 25, scrapy 19, execa 9, h3 9, nitro 8, packaging 7, poetry 6, unstorage 6,
    tornado 3, aiohttp 2, litestar 2, typer 2, multidict 1, unplugin 1, yarl 1, msw 0, werkzeug 0.
- Family N (strict docstring/JSDoc rule): 28 candidates. scrapy 8, sqlglot 5, h3 4, nitro 4, aiohttp 3,
  packaging 3, vitest 1. The other repositories gave none.
- vitest was mined in 8 quarterly chunks after two container restarts. The whole-window repeat check found no
  repeats across chunks, so the chunks equal one run.

**Gate** (seed 5501, target 80, caps B 10 and N 20 per repository):
- **Family B: 66 of 80** (117 drawn). Python 38, JS/TS 28. Changed callers: 1: 52, 2: 10, 3-4: 3, 5+: 1. So 14 of
  66 (21%) have 2 or more.
  - Every candidate except 15 of sqlglot's (left out by its cap) was drawn, so 66 is the most this pool gives.
  - vitest: 0 of 31. All of them give no test results here (browser mode). It held 10 of the 30 mined candidates
    with 2 or more callers.
  - scrapy: 8 of 19 (11 with no fail-to-pass test). poetry 3 of 6. msw and werkzeug gave no candidates.
- **Family N: 23 of 28.** Python 15, JS/TS 8. scrapy 4 of 8; vitest 0 of 1.

**Environment incidents during the B gate:**
- The disk allowance filled (the JS/TS install caches). 5 candidates failed setup in that stretch, 3 of them naming
  ENOSPC. Old scratch checkouts and the global npm/pnpm/pip caches were cleared. The 5 results were removed from
  `gate_B.jsonl` (the earlier file is kept in scratch) and re-gated on the same code: 4 failed for ordinary reasons
  (3 vitest browser mode, 1 execa with no fail-to-pass test), and h3 cad67c8476 passed. No other result was touched:
  a full disk cannot make a candidate pass. Family N's results came before the disk filled.
- The global caches were cleared twice more during the run, each time when no install was running.

**Gate resume bug (bookkeeping only):** on a resume, every earlier pass is preloaded, so a repository already at
its cap (sqlglot) had its earlier draws skipped before they were counted. `attrition_B.json` read sqlglot 0 drawn
and 117 as 107. Fixed in `gate.py`: a commit already gated counts as drawn before the cap check. The outputs were
recomputed from the same `gate_B.jsonl`, with no new gating. The task list is unchanged (sha256 `b796d29e…`
before and after), and Family N's outputs recompute byte-identical.

## 2026-11-07: R3-8 pre-screen: the 8 repositories, chosen before the screen runs

- Named in the decision: **mypy**, **sqlalchemy** (Python); **rollup**, **prettier** (JS/TS).
- Chosen to match (2 Python, 2 JS/TS). Each was picked on a guess about change shape, not a measured yield:
  - **pydantic** (Python): validation, schema and field machinery is shared across many call sites, and signature
    changes there tend to propagate. Pure-Python package, pytest. Risk: pinned `pydantic-core` wheels.
  - **pylint** (Python): checkers call shared `utils` and astroid helpers, so a helper signature change touches
    several checkers in one commit. Pure Python, pytest. Risk: functional tests are data files, as with mypy.
  - **eslint** (JS/TS): rules and the linter share `lib/shared` and `ast-utils` helpers. Mocha, Node only, one
    package.
  - **svelte** (JS/TS): the compiler's phases call shared helpers from many visitors. pnpm, vitest in Node mode for
    the compiler. Risk: tests are sample folders, which the gate maps to their runner file (to be seen).
- Clones are shallow from 2024-11-01, which covers the window and each first commit's parent, to save disk.
- `mine.py`'s `LANGUAGE` gains rollup, prettier, eslint and svelte (metadata only: the task's `language` field).

## 2026-11-07: memory test rerun under amendment R3-7: passes

Code `81ad2e86`; 20 fresh runs; `memtest/memtest_2026-11-07_r3-7.json`. All four clauses pass:
- (a) plateau in 20 of 20 runs, and RSS slope over pad calls at most 0.004 MB per call;
- (b) peak drift at most 1.4%;
- (c) children exited, and the driver grew by 0.5 MB;
- (d) no orphans.
20 of 20 runs ended as expected. The first run's failed result stays in the record (`memtest_2026-11-07.json`).

## 2026-11-07: R3-8 pre-screen result: the week proceeds (3 repositories at or above 40%)

The Family B miner over R3a's main window (2024-12-01 to each clone's HEAD), with the same rules as for the 18,
mined in quarterly chunks (64 jobs, all exited 0) and merged with the whole-window dedupe. No gating.
Outputs: `r3a_tasks/screen/` (`screen.json`, `candidates_B_<repo>.json`, `merge_screen.py`).
Deep = 2 or more changed callers. Reference: 21% gated (14 of 66), 23% mined (30 of 132).

| repo | HEAD | commits kept | mined | 1 | 2 | 3-4 | 5+ | deep | share | ≥40% |
|---|---|---|---|---|---|---|---|---|---|---|
| mypy | 55472c03090a | 1845 | 9 | 5 | 3 | 1 | 0 | 4 | 44% | yes |
| sqlalchemy | 2918faf29c58 | 884 | 1 | 1 | 0 | 0 | 0 | 0 | 0% | no |
| rollup | 73fae6e8977c | 712 | 12 | 6 | 5 | 1 | 0 | 6 | 50% | yes |
| prettier | b09dd90022c8 | 2356 | 14 | 9 | 4 | 0 | 1 | 5 | 36% | no |
| pydantic | b947584cc89e | 1321 | 10 | 6 | 3 | 1 | 0 | 4 | 40% | yes |
| pylint | 79c8d0c9813b | 856 | 5 | 5 | 0 | 0 | 0 | 0 | 0% | no |
| eslint | bdd4b1da21dc | 1257 | 9 | 8 | 0 | 1 | 0 | 1 | 11% | no |
| svelte | 7775f73b7cbe | 1760 | 24 | 16 | 6 | 1 | 1 | 8 | 33% | no |
| all 8 | | 10991 | 84 | 56 | 21 | 5 | 2 | 28 | 33% | |

- The condition is met by mypy, rollup and pydantic, so the week proceeds under R3-8.
- The shares rest on 9 to 12 candidates each; pydantic is exactly at the threshold (4 of 10).
- The screen ran the full main-window miner, so these candidates are the mining for the week. What remains is the
  gate.

## 2026-11-08: R3-8 week: the 4 repositories chosen

Decision (user, 2026-11-08): reading (a) of "chosen from the screen by deep candidates", the top 4 by deep count,
whatever their share. svelte (8), rollup (6), prettier (5), and one of mypy and pydantic (4 each) by a seeded draw.
- Tie-break: `random.Random(5501).choice(sorted(["mypy", "pydantic"]))` gives **pydantic**.
- Pre-committed outcome wording: if gated deep tasks reach 30, the scaling test runs with a power note; if not,
  scaling is reported as "not run — insufficient depth variance after pool expansion". No threshold changes.
- The representativeness caveat of R3-8 stands. Deep tasks selected partly on deep share are not representative of
  real code, and the results' interpretation must say so.
- Pool for the gate: the 132 main-window candidates plus these 4 repositories' 60 (svelte 24, rollup 12,
  prettier 14, pydantic 10). Seed 5501, at most 10 per repository, the used rule, resuming from the 117 records
  already gated.

## 2026-11-08: R3-8 week: gated counts. Family B reaches 80; deep tasks 18, scaling not run

**Result: 18 gated Family B tasks with 2 or more changed callers, below the 30 that R3a's scaling test needs.**
Under R3-8, as pre-committed: scaling is reported as "not run — insufficient depth variance after pool expansion".
The threshold is unchanged.

- Family B is frozen at its committed size of 80 (sha256 `ba4de9ae…`): Python 41, JS/TS 39, 16 repositories, 13
  from used repositories (at most 40).
- Callers, gated: 1: 62, 2: 13, 3-4: 3, 5+: 2. The 14 new tasks: svelte 9 (2 deep), pydantic 3 (1 deep),
  prettier 2 (1 deep, 5+ callers), rollup 0.
- The gate stopped at the 80th pass, on the first day. That is the week's end: the draw had no more room.
- **The ceiling was 28.** With 66 tasks already gated, the target of 80 left room for 14 more, so at most 14 + 14
  deep tasks. 30 could not be reached under the recorded rules. This was reported before the gate ran, and the gate
  ran to 80 as recorded.

Attrition among the 16 new candidates drawn: svelte 9 of 9, pydantic 3 of 3, prettier 2 of 3 (1 with no
fail-to-pass test), rollup 0 of 1.
- **rollup** cannot install here: its `wasm-pack` dev dependency downloads a binary from GitHub releases in a
  postinstall script, and that request fails through this environment's proxy (the client sends a plain GET, which
  the proxy refuses). Its tests also need a Rust native build per revision. Recorded as attrition, like vitest's
  browser mode. Not fixed.
- **svelte builder fix:** its tests are sample folders (`tests/<suite>/samples/<name>/`), run by the suite's
  `test.ts`. The gate had no mapping for them, and a first run failed 17 of 21 svelte candidates with "no runnable
  hidden test file". `gate.py` now maps a sample folder to its suite's runner (browser suites skipped), code
  `037f27b6`. Only svelte and rollup candidates have sample folders, so the 117 earlier records are unaffected.
  The first run's 44 records for the new repositories were discarded and every new candidate was re-gated on the
  fixed code (the discarded file is kept in scratch).
- **Disk:** the builders' download caches filled the disk during the re-gate (170 MB left). The gate was stopped,
  the caches were cleared (they only hold downloaded packages), and the gate resumed. No record names ENOSPC, and a
  full disk cannot make a candidate pass.
- **Attrition bookkeeping fix:** on a resume that stops at `--want`, records after the stopping point in the new
  order were not counted as drawn (`attrition_B.json` read 38 drawn). Fixed in `gate.py`; recomputed from the same
  log, no new gating, task list byte-identical.

The representativeness caveat of R3-8 stands. The interpretation must state that the added repositories were
chosen partly on deep share. Family B's sample is still mostly 1-caller, which is representative of real change.

## 2026-11-08: bug verification on the full R3a pool (before the hash-commit)

Reasonable call, logged: the 11 fixed bugs map to these guards. Each was checked on the full R3a pool: the 16
repositories, and the 80 tasks where the check is per task.

1. Stale index / drift (R3-5): `tests/test_r3_equivalence.py::test_equivalence_e1_e3` (E1, E3), plus
   `equivalence.py --rev` at the first task's parent in each of the 16 repositories. All pass.
2. Parse gate: the same test (E2 a-e, E3), and the same 16-repository run. All pass.
3. `read_file` tab separator: `tests/test_r3_shared_tools_edit.py` (3 tests), plus every gold edit in the
   80-task dry run.
4. `edit_file` whitespace hint: `tests/test_r3_shared_tools_edit.py` (4 tests).
5. `edit_file` overlapping-match uniqueness: `test_overlapping_occurrences_are_not_unique`, plus the dry run (its
   gold edits are widened until unique, overlaps included).
6. Notice outside the 4,000-token cap: E2(a), all 16 repositories.
7. Blast radius test-code leak (`include_tests=False`): 300 seeded production functions per repository (16/16,
   0 listing test code), plus each of the 80 anchors at its own task's start state.
8. jsbuild rebuild when the tree changed: `test_built_output_is_rebuilt_only_when_the_tree_changed`, plus the dry
   run on the 8 JS repositories.
9. `runner.score` start-snapshot base: the dry run on all 80 (PRISM gold resolved, BASELINE empty unresolved).
10. Process-group kill: `tests/test_r3_run_group.py` (2 tests), plus the orphan scan after each of 177 dry-run
    children: 0 orphans.
11. Gate bookkeeping and mapping: `tests/test_r3_sample_runners.py`, plus the attrition recompute (byte-identical
    task list).

Found by the full-pool runs, beyond the 11:
- **Scoring, r3a-B-71 (poetry):** 4 test ids carry the gate's worktree path (a test parametrized with an absolute
  path), so they read as missing in any other worktree, and gold scored unresolved. `runner.id_status` now reads
  the gate's worktree path in an id as the run's own (`a614b0b2`, `tests/test_r3_score_test_ids.py`). The task
  list is unchanged. Gold now resolves.
- **Dry-run script, r3a-B-12 (sqlglot):** the gold script crashed on a submodule bump. It now skips gitlinks
  (`a614b0b2`). This is verification code, not product code.
- **Turn cap in the dry run:** 15 gold scripts hit the 30-turn cap (4 pad calls plus one `edit_file` per hunk, up
  to 70 turns). Rerun with no pad and `--turn-cap 120`, all 17 failed golds resolved. This is a property of the
  one-edit-per-hunk replay, not a product bug. The agent can edit a file in fewer calls.
- **Start state, r3a-B-09 (pydantic) and r3a-B-29 (h3):** the anchor gained overloads in the commit. The index
  keeps a name's first declaration, so the start patch replaced the anchor with a bodiless overload signature (the
  start state had no implementation). Fixed in `mine.start_patch_for` (`overload_group`, `e67e4033`,
  `tests/test_r3_overload_start.py`). `repair_overloads.py` rebuilt both start patches and re-gated them:
  - B-09 passes again: 50 fail-to-pass and 79 pass-to-pass, the same counts; gold resolved at F1 1.0; empty
    unresolved.
  - B-29 fails: no fail-to-pass test. All 28 of its old fail-to-pass tests passed once the body was restored.
    They were failing only because the body was missing, so B-29 is not a valid task.

Not hash-committed: the task list now differs from `ba4de9ae` (B-09's start patch, and B-29 out). How to refill
the 80th slot goes to the user.

Anchor check (bug 7, per task): 80 of 80 run, 79 pass (0 test callers, no error). The one failure is r3a-B-29,
whose anchor is not in the index at the start state: the stub defect above. The first run of the checker was
OOM-killed at task 53 because it held every index in one process. It was resumed with one process per task. This
is a flaw in the checker, not the product (R3 runs are already one process per run).

## 2026-11-08: refill of the 80th slot, paused after 3 failures in a row (reported, per the user's rule)

- Setup:
  - The gate was resumed on a copy of `gate_B.jsonl` with two records changed: B-09's task replaced by its
    repaired version, and B-29's marked failed, with the reason.
  - Same command: seed 5501, want 80, per-repo cap 10, the existing `candidates_B.json`.
  - It replays the 133 records and gates the next candidates in the draw order.
- The next three drawn all failed:
  - rollup fff335fb: install fails (the known `wasm-pack` postinstall behind the proxy);
  - prettier 72cfa18a: no fail-to-pass test;
  - rollup cb8f8153: install fails (same cause).
- The gate was stopped by PID after the third failure, as the user instructed for 3 or more failures in a row.
- 41 eligible candidates remain under the caps: svelte 15, prettier 10, rollup 9, pydantic 7.
  - The next in order is svelte 220b526d.
  - All 9 rollup candidates will fail for the same known cause.

Also added (no gate or task change):
- `pilot_run.py`: the pilot driver, one process per run;
- `analyze --pilot`: criterion 7, "runs that hit 30-turn cap without done". The turn cap is flagged, not changed.

**Refill stop rule, refined by the user (2026-11-08):** "The 3-in-a-row refill stop applies to new or unknown failure
causes. Known attrition — rollup install failures, vitest browser mode, and any previously accepted repo-level
incompatibility — is classified separately and does not trigger the stop. Only three consecutive failures from
unclassified causes pause the refill for review." Under this rule the pause above came from 1 unclassified failure
(prettier 72cfa18a) and 2 known ones (rollup). The refill resumed from svelte 220b526d.

## 2026-11-08: refill landed; R3a task list hash-committed (`70a6aede…`)

- **Refill:**
  - Resumed gate: svelte 220b526d passed (the first candidate after the paused three). It is now r3a-B-26,
    with 2 changed callers.
  - Dry run: PRISM gold resolved in 24 turns, BASELINE empty unresolved, 0 orphans, same protocol as the other 79
    (pad 4, cap 30).
  - Anchor check: in the index, 4 callers, 0 test callers.
- **B-09, repaired:**
  - Dry run: gold resolved in 23 turns, empty unresolved, 0 orphans.
  - Anchor check: passes.
  - Blast radius lists 0 callers for `ModelPrivateAttr.get_default`, before and after the repair. The changed
    caller, `init_private_attributes`, calls it through an attribute the call graph does not resolve. This is a
    property of the product that R3a measures, not a defect. Recorded, not changed.
- **B-09's definition change** (same id, corrected definition):
  - The start patch and prompt now put the commit's overload group (2 `@overload` stubs plus the implementation)
    in place of the parent's `get_default`.
  - The fail-to-pass and pass-to-pass sets are the same counts (50 and 79), re-derived by `gate_one`.
- **Hash-commit:**
  - `r3a_tasks/tasks_B.json` (task-list sha256 `70a6aede…`, file sha256 `9d713867…`), recorded in
    `r3a_tasks/R3A_TASKS_HASH.md` with the caveats: 80 Family B, 18 deep, scaling not run, Family N dropped, and
    78% one-caller.
  - `ba4de9ae` is superseded. Its files are kept with that suffix.
  - `summary.json`'s Family B section was recomputed from the new gate log (137 drawn).

## 2026-11-08: pilot run on Kaggle (code `8ced8a58`, pilot tasks `fe934d16`; `reports/r3/pilot/`)

20 runs (10 Family B pilot tasks × PRISM, BASELINE; seed 42; turn cap 30), one process per run. All exited 0 with
no leftover processes. The JS smoke test passed. Harness checks only; no result is read for a decision.

| Criterion | Result |
|---|---|
| 1. no harness error | **pass** (0 error, 0 missing) |
| 2. valid tool calls | PRISM 108/108, BASELINE 112/112 (0 parse errors) |
| 3. tests found and run, both languages | **pass**: JS 10/10 runs called `run_tests` (24 calls with results), Python 10/10 (34) |
| 4. truncation and caps | PRISM: 20 outputs cut, 46 turns stubbed, 0 stuck, 3 budget. BASELINE: 4, 3, 0, 0 |
| 5. scoring path (gold resolved, empty unresolved) | **pass**, 10/10 |
| 6. done vs stuck | every run ended `done` except 3 PRISM budget stops; 0 stuck |
| 7. runs that hit the 30-turn cap without done | **0 of 20** |

- **Turn cap:** no pilot run reached 30 turns (max 25), so the flag raised by the dry run's gold scripts does not
  recur. The pre-registered rule (R3-1 item 2, R3-2 item 3) computes p90 = 23, plus 5 = **28**; share at 30 = 0.
  Whether R3a uses 28 (the rule) or 30 ("cap stays", the user's 2026-11-08 instruction) goes to the user. Nothing
  is recorded as the cap yet.
- **The 3 PRISM budget stops** (pilot-B-02, B-08, B-09) are the same pattern: from about turn 4, the model repeats one
  identical cycle (`run_tests`, `prism_change_context`, `prism_blast_radius`, same arguments) with 0 edits. The run
  ends at the token budget (~290k cumulative prompt tokens) at turns 23-25, before the turn cap.
  - The tool outputs are sound: failing tests are listed, and the context and blast radius are correct.
  - So this is agent behavior, not a harness fault.
  - No BASELINE run repeats a cycle that way.
  - The stubbing count (46) and outputs cut (20) come from these 3 runs.
  - For loops like this, the budget binds before the cap.
- Several runs in both versions call `done` after 2-5 turns with no edit. Recorded only.
