# Phase 2 plan: a complete code graph, and a harness that reports what happened (agreed 2026-11-20)

**Why:** the 600-run 3-arm re-baseline (`reports/r4/rebaseline5_final`) found no detectable difference between the arms.
The forensic audit after it found four independent weaknesses:
1. **Connection coverage:** PRISM's graph is a call-and-inheritance graph. On six Python repositories it records 30% of
   77,487 uses of uniquely named definitions (calls 74%, base classes 86%; imports, `__all__`, decorators, string
   references and all module-level code 0%). Test files hold 49% of the uses and are hidden by `include_tests=False`.
2. **Edit and test feedback (every arm):** an edit that breaks a file's syntax is applied and reported as a normal edit;
   `run_tests` returns only the last 1,200 characters, so the error (delete-05: `IndentationError`) never reached the
   agent. 16 runs ended with a file that does not parse.
3. **Tool behaviour:** `prism_ask` never says "not found"; `prism_read_code` knows only functions and classes;
   `prism_change_context` names a definition's own lines ("lines 14-15") without its enclosing block; repeated
   identical calls; INJECTED's block re-sent every turn (30 runs ended on the token budget).
4. **Agent habits:** `grep`/`read_file` are sticky (two thirds follow one another).

## Decisions (user, 2026-11-20)

1. **Harness feedback fixes (C1–C3) apply to every arm.** The harness treats BASELINE and the PRISM arms the same for
   syntax errors and test logs; the only difference between arms is the PRISM tools.
2. **A breaking edit is applied, with a warning.** The agent must learn to fix its own broken code; the edit is not
   refused.
3. **No PRISM-only 4th arm yet** (revised by the user, 2026-11-20: build its tool-list support in Phase D, so it can be
   run; whether it runs is a pre-registration decision). First show whether the upgraded graph (Phase B) and harder tasks (Phase E) change the
   standard 3-arm comparison; a PRISM-only arm is a Phase 3 question if the data warrants it.
4. **Order:** A → B + C (in parallel) → D → E → F.

## Phase A: measure first (gate for everything else)

| # | Work | Done when |
|---|---|---|
| A1 | The coverage audit as a benchmark in the repository (`harness/experiments/graph_coverage`), then JS/TS and non-code files | numbers per connection kind for the task repositories |
| A2 | Today's numbers committed as the baseline | `harness/experiments/graph_coverage/baseline/` |
| A3 | Regression check: a later PRISM change may not lower any kind's number | in the test suite |

## Phase B: complete connections in PRISM

| # | Connection kinds | Baseline |
|---|---|---|
| B1 | Code outside functions: module-level and class-body code as nodes; conditional definitions (`if/else`, `try/except ImportError`); aliases (`X = Y`) | 0% |
| B2 | Imports, re-exports, `__all__`, wildcard imports | 0% |
| B3 | Type use: annotations, generics, `isinstance`/`issubclass`, `except Foo` | 2–12% |
| B4 | Use as a value: passed as an argument, assigned, stored in a container, returned; decorators | 0–21% |
| B5 | Attribute and property access surfaced as dependents | 2% |
| B6 | String and dynamic references (`getattr`, registries, config and entry points), each with its certainty | 0% |
| B7 | Tests as dependents (their own group) | hidden |
| B8 | Across files and languages: template placeholders, HTML ids and CSS classes used from JS, C extensions, docs | 0% |
| B9 | Sibling copies (functions that change together) | – |
| B10 | Every connection labelled with its kind in `prism_change_context` and `prism_blast_radius` | – |

**Done when:** every kind that can be resolved by reading the code is at ≥ 90% on the Phase A benchmark; dynamic string
references have their own, lower target.

## Phase C: harness feedback (every arm) and PRISM tool fixes

| # | Work | Arms |
|---|---|---|
| C1 | `edit_file` checks syntax after every edit (Python `ast`, JS/TS tree-sitter) and warns when an edit breaks a file that parsed; the edit is applied | all |
| C2 | `run_tests` shows the first real error (collection/import errors, the exception line), not only the output's tail | all |
| C3 | A note when the agent repeats an identical call that returned the same output | all |
| C4 | PRISM's unparsable-file notice on every tool result while a file does not parse | PRISM arms |
| C5 | `prism_ask` says "no symbol named X" when the question names one that does not exist | PRISM arms |
| C6 | `prism_read_code`: module names, module-level variables, the enclosing block | PRISM arms |
| C7 | `prism_change_context`: the enclosing block, not only the definition's lines | PRISM arms |
| C8 | The injected block shortened after the first turns | INJECTED |
| C9 | Code-review scorer: undefined names for JS/TS | scoring |

**Done when:** each fix has tests; a replay of the logged delete-05 failure shows the agent told the file is broken.

## Phase D: PRISM search and read

| # | Work |
|---|---|
| D1 | `prism_search`: text/regex search over the repository, each hit labelled with its kind and enclosing symbol |
| D2 | `prism_read`: any file (tests, templates, configs), always the current bytes, with line numbers |
| D3 | An arm with PRISM tools only (`PRISM_ONLY`): support built; run only under its own pre-registration |

**Built (2026-11-20):**
- **`prism_search` and `prism_read`** (`harness/experiments/r3/tools.py`, `search_labels.py`) are in every PRISM arm's
  tool list (PRISM, INJECTED, PRISM_ONLY).
- **`PRISM_ONLY`** (`harness/experiments/r3/loop.py`) has no `grep` and no `read_file`.
- **Tests:** `tests/test_phase_d.py`.

## Phase E: tasks that test connections

| # | Work |
|---|---|
| E1 | Tasks with many real dependents across files and hops, checked with the Phase A benchmark before use |
| E2 | Families that depend on connections: rename, signature change, move to another module |
| E3 | A few easy tasks kept as a control |

## Phase F: run

Pre-register the new design in `AMENDMENTS.md` (arms, tasks, metrics), smoke test, then the sharded 3-arm run.
