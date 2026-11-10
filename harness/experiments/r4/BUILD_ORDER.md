# R4 build order: the code work (recorded 2026-11-10; runs in parallel with R3a's remaining sessions)

**Guardrail:** R3a's cell stays pinned to `ab22ff5d` (`kaggle/r3a_cell.py`, `CODE_COMMIT`) until R3a completes.
Everything below lands on the branch head, never on `ab22ff5d`, and is never run on Kaggle until R3a is done.
Item ids are `ROADMAP_R4_R6.md`'s (A = correctness, B = new capabilities, C = protocol, D = families).

## Tracks and order

Four tracks. Within a track, items run in the order listed; across tracks, they can run at the same time, except
where a dependency is named.

| Track | Order | Item | Touches | Depends on | Effort *(roadmap's guess)* |
|---|---|---|---|---|---|
| **1. Test infrastructure** (no `src/` change) | 1 | Suite hygiene: `transformers` skips, fixture caches to `tmp_path`, `.prism/` out of `tests/fixtures/`, the stale xfail checked | `tests/`, `.gitignore` | — | 0.5–1 day |
| | 2 | Golden fixtures written: `tests/fixtures/golden/py_repo/`, `ts_repo/` (`GOLDEN_TESTS_PLAN.md` §1) | `tests/fixtures/golden/` | — | 1–1.5 days |
| | 3 | Golden harness: `tests/golden/` runner, `queries.json`, normalization, `--update-golden`, the stale-file check, skip without the exact tokenizer | `tests/golden/`, `conftest.py` | — | 0.5 day |
| | 4 | **First golden recording** of today's behaviour, with `KNOWN_DEFECTS.md` | `tests/golden/` | **the tokenizer asset** (`kaggle/fetch_tokenizer_cell.py`, your CPU Kaggle run) | 0.5 day |
| | 5 | Cross-feature tests (`GOLDEN_TESTS_PLAN.md` §4) | `tests/golden/` | 4 | 1 day |
| **2. Correctness** (`src/prism/`) | 1 | A2b: `signature()` parses `const f = async (…) =>` | `impact/breaks.py` | track 1 item 4 (so the golden diff shows the fix) | 0.25 day |
| | 2 | A2a: break check against the committed version (`git show HEAD:<path>` as the old text) | `impact/breaks.py`, the change-context caller | 1 | 0.75 day |
| | 3 | A1 (D1): a receiver-less call never resolves to a method; a scored winner is tentative | `graph/concrete_builder.py`, `digest` (`_check_receiver`) | 2 | 0.5 day |
| | 4 | A3 (D2): unresolved calls listed, embedded ones included | `digest` (`_call`) | 3 (same file as A1: sequential) | 0.5 day |
| | 5 | E2: re-verification after A1 (graph edges change): E1–E3, the blast-radius test-code check, dry runs | — | 3, 4 | 2–3 days |
| **3. New tools** (`src/prism/`, new modules) | 1 | B1 / S1: same-behaviour detection (structural match plus normalized-AST fingerprint) | new module, tool registration | — (runs alongside track 2) | 2–3 days |
| | 2 | B3: import-site list (needed by move, delete, rename) | new module | — | 1–1.5 days |
| | 3 | B2 / S3: callee binding check (upstream) | `impact/` (the break check's `bind`, reversed) | track 2 items 3, 4 (correct targets first) | 2 days |
| | 4 | B5 / S5: `prism_check_changes` (S3 + S4 over every function changed against `HEAD`) | new tool | track 2 item 2, track 3 items 2, 3 | 2 days |
| | 5 | B6 (relational summary), B4 (`testcontext` served), B7 (readable formats) | renderers, tool registration | 4 | 1.5 days |
| **4. Protocol** (harness only; built, not run) | 1 | C1: the repeat notice | `harness/experiments/r4/` (a new loop module; `r3/` stays as R3a ran it) | — | 0.5 day |
| | 2 | C2: the last test result pinned in the window | the same module | 1 | 0.25 day |
| | 3 | C3: the neutral workflow line in both arms' system prompt | the same module | 2 | 0.25 day |

**Conflicts:**
- **Same file, sequential:** A1 and A3 (`digest`); A2 and B2 (`impact/breaks.py`). B2 waits for A2 anyway.
- **Tool registration** (the MCP server's tool list) is touched by B1, B4, B5 and B7. The conflicts there are
  one-line merges.

**Every `src/` change** follows `GOLDEN_TESTS_PLAN.md` §3:
- regenerated goldens are committed with the change, with a "golden: … why" line per changed file;
- a golden change on a line `KNOWN_DEFECTS.md` does not name is a regression to explain.

## Blockers

| Blocker | Blocks | Owner |
|---|---|---|
| The tokenizer asset (`kaggle/fetch_tokenizer_cell.py` on CPU Kaggle, then the encoding committed) | track 1 item 4, so all of track 2's commits (each changes goldens) | you: one CPU Kaggle run, no GPU quota |
| R3a's completion | any Kaggle run of this code; the scope decisions (tasks per family, third arm, B5 in R4, request → plan, family order) | R3a |

**If the tokenizer is delayed:** tracks 1 (items 1–3), 3 (items 1–2) and 4 do not need it. Track 2's fixes can be
written and unit-tested, but they are committed only after the first recording, so the recording captures
behaviour before any fix.

## Started 2026-11-10

- **Mining** (`D1`–`D6`): candidate lists only (`harness/experiments/r4/mine_families.py`; Family N via
  `r3/mine.py`). Yield in `r4/MINING_STATUS.md`.
- **Part A workflow:** verified against `ab22ff5d` (`.github/part-a-request`).
- **Next, in order:** track 1 items 1–3; then track 3 items 1–2 and track 4, until the tokenizer asset arrives.
