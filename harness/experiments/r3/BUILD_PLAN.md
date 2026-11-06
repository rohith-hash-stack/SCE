# R3a build plan (after the hash-commit `e711bcfb`; working notes, not part of the pre-registration)

The rules are in `README.md`, hash-committed. Anything here that would change a rule goes into a new dated amendment
file before the first R3a call. "Days" are working days.

## 1. Environment builders (critical path)

**Python:** `harness/experiments/suite/execute.py` already builds a `uv` environment per repository and revision and
runs selected tests. To add:
- per-test JUnit XML;
- three runs per test for the flake check;
- a cache keyed by repository and lockfile hash.

Estimate: 1 day.

**JavaScript/TypeScript (`jsenv.py`):**
- **One-week clock started 2026-11-05.** It passes its own check when, on all 7 repositories, it installs, finds the
  tests, and runs a named test at two revisions.

| component | state | estimate |
|---|---|---|
| detection: package manager from the lockfile or `packageManager`; Node version from .nvmrc, .node-version, volta or engines; build script; test runner via the `test` script chain (Bun skipped); workspaces | **done**: all 7 detect at HEAD | — |
| Node version: resolve an engines range to a concrete version; install it (a Node tarball, or corepack for pnpm/yarn) | next | 0.5 |
| install: `pnpm install --frozen-lockfile`, `yarn --frozen-lockfile` / `yarn install --immutable`, `npm ci`; no lockfile (execa): `npm install --before=<commit date>` | next | 0.5 |
| build step: `build` only when the tests import the built output | | 0.5 |
| selected tests with machine-readable output: vitest `--reporter=json`; jest `--json`; mocha `--reporter json`; node:test `--test-reporter=tap`; tap and ava TAP | | 1 |
| flake check (3 runs) and a per-repository, per-lockfile cache | | 0.5 |
| self-check on the 7 repositories at two revisions each | | 0.5 |

- **What detection found at HEAD:**
  - 6 of 7 repositories are pnpm monorepos on vitest (vitest, nitro, h3, unstorage, msw, unplugin);
  - execa uses npm and `node --test`, with no lockfile;
  - the pnpm versions pinned are 11.x (the container has 10.28), so corepack is used.
- Detection runs per task revision, not only at HEAD, because lockfiles and runners change over time.

## 2. Agent harness

| component | builds on | estimate |
|---|---|---|
| loop: one model call per turn returning one tool call or `done`; JSON tool-call format and parser; turn cap | `change_history.run.ollama` | 1.5 |
| context window rule: 20,480 tokens per call; keep the system prompt, the task and the last turns that fit; older tool outputs become one-line stubs; identical in both versions | Qwen2.5 tokenizer | 0.5 |
| token accounting: 300,000 input+output per run (the run ends as if `done`); 4,000-token cap on every tool's output | | 0.5 |
| shared tools: `read_file`, `list_dir`, `grep`, `edit_file`, `run_tests` (failures only, never which tests are hidden), `done` | the builders above | 1 |
| baseline: `related_code` (BM25 over the repository's functions, as code) | R1's `pad.Repo.bm25` | 0.25 |
| PRISM: `prism_change_context`, `prism_blast_radius`, `prism_ask` | existing tool code in `src/prism/mcp/server.py` | 0.5 |
| PRISM: `prism_new_function_context`, `prism_digest` (v2) | `prism.impact.newcode`, `prism.summaries.digest.DigestBuilder` (library code exists, no tool yet) | 0.5 |
| logging from the start, per version: every turn's tokens, truncation of tool outputs and answers, budget and turn-cap hits, the end reason | | 0.5 |
| resume: one result file per run (task, version, seed), pushed every 10 minutes; a rerun skips finished runs | R1/R2 cell | 0.5 |
| scoring: resolved (Family B: every F2P and P2P test; Family N: every hidden test and every test that passed at the start); diff F1 of files and functions; Family N reuse and invented calls (scorer v2); process measures; pass@2 | suite scorers | 1 |
| analysis: per family, Wilcoxon and McNemar per seed, Holm across B and N, Family B interim look at 40/80 (boundaries in amendment R3-1), per-seed and unstable flags, labels | R2's `analyze.py` | 1 |
| Kaggle cell: Ollama 0.34.0, uv, Node via corepack; environments built on Kaggle; hash checks against the frozen manifest | R2's cell | 1 |

~~**One harness detail to fix in a dated amendment before the first R3a call:** when the agent has edited files, PRISM's
tools answer from the **current** tree. The index is rebuilt lazily on the next PRISM call after an edit, and
`related_code` re-indexes the same way. Both versions see their own edits, symmetric.~~ → replaced (2026-11-06):

**Edit handling per Amendment R3-5:**
- Before each index-backed tool call, run a `(size, mtime)` then content-hash drift probe against the files last
  indexed. Reindex only if something moved.
- A file that does not parse is gated: its index content stays at the last parsable version, and index-backed tools
  refuse to serve it as current.
- Equivalence tests E1-E3 must pass before the harness is frozen.
- `read_file` is not gated.
- **The fairness rule is a harness requirement, not just a PRISM behaviour:**
  - the harness must not retry a gated call in a way that lets the agent skip `read_file`;
  - if the agent ignores the notice and asks again, it gets the same notice, and must read the file directly.
- The baseline's `related_code` is index-backed and follows the same probe and gate (R3-5).
- Added cost: 3-4 days (R3-5, "Build cost"). The harness strand is about 12-13 days in all.

## 3. Tasks

- Mining over the 18 Family B repositories and the 7 Family N repositories:
  - **Family B:** R1 forward's miner, signature or contract change plus a test change, post-cutoff. Start state: the
    parent plus the developer's change to the anchor only.
  - **Family N:** relaxed rule, at most 20 per repository, target 80.
- Gating, which needs both builders: F2P/P2P (Family B) or hidden tests failing then passing (Family N), each test
  3 times.
- Gate attrition reported per repository; freeze with sha256.
- Estimate: 2-3 days, most of it environment time.

## 4. Pilot (after the builders and the harness; not run yet)

- **Tasks:** 20 runs, 10 per family, on the pilot-only tasks: commits dated 2024-10-01 to 2024-11-30 from the same
  repositories, gated the same way, drawn with seed 6601, never reported as results.
- **Versions:** both versions at seed 42, 5 tasks per family each.
- **Runtime:** Kaggle, the same model and setup as R1 and R2.
- **The turn cap (amendment R3-1, item 2, as resolved by R3-2):**
  - the 90th percentile of turns used in the 20 runs, both versions pooled, plus 5 turns, rounded up, at most 30;
  - if more than 20% of pilot runs reach 30 turns, the cap stays 30;
  - recorded as a dated line before any main-run call.
- **Pass criteria (harness checks; no result is read):**
  1. all 20 runs end without a harness error;
  2. the model's turns parse as one valid tool call, a high share in both versions (the share is reported). A
     format failure is fixed in the harness, and the pilot is re-run.
  3. tests are found and run inside the loop in both languages;
  4. truncation and budget or turn-cap hits are logged per version;
  5. the scoring path, end to end on synthetic answers: the developer's patch scores resolved on every pilot task,
     and an empty patch scores unresolved.

## 5. Critical path

- **Builders (Python 1 day; JS/TS up to 5 days by the one-week rule) → gating and task freeze (2-3 days) → pilot →
  turn cap recorded → R3a.**
- The agent harness (about 9 days of components) runs in parallel with the builders and gating. It is the longer
  strand, so in practice it sets the date of the pilot.
- If the JS/TS builder misses its check by 2026-11-12:
  - round 1 is Python-only;
  - if Python-only Family B is below 50 after the gate, R3a is reported as not run (the stop condition).
