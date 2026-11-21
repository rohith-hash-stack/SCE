# Known defects in the first golden recording (2026-11-10)

**What this recording is:** the outputs record PRISM's behaviour **as it is today**, wrong answers included
(`GOLDEN_TESTS_PLAN.md` §2).
- **The recording:** commit "Tests: first golden recording", with the exact tokenizer (tiktoken cl100k, vendored).
- **When a fix lands:** the goldens change on the lines below, and the entry is removed. Any other golden change is a
  regression to explain.

| # | Defect | Golden lines that are wrong today | Correct output after the fix | Fix |
|---|---|---|---|---|
| K1 | **D1:** a bare call resolves to a method | `py_repo/prism_digest/d1_bare_caller.txt` L3 and `py_repo/prism_change_context/d1_bare_caller.txt` L25, L32: `set_value(...) -> one of: src.shelf.store.Store.set_value, …`. `ts_repo/prism_digest/d1_bare_caller.txt` L3 and `ts_repo/prism_change_context/d1_bare_caller.txt` L25, L32: `setEntry(...) -> one of: src.cache.Cache.setEntry` | the receiver-less call is never a method. The JS call is unresolved ("`setEntry` defined nowhere, not imported"); the Python call lists only `src.shelf.values.set_value`, as a possible target (it is not imported) | A1 |
| K2 | **D2:** an unresolved call embedded in an expression is dropped | `py_repo/prism_digest/d2_embedded_unresolved.txt` L3 (no `missing_helper`); `ts_repo/prism_digest/d2_embedded_unresolved.txt` L3 (no `timeoutAfter`), L7 (no `undefinedThing`) | each line also lists the unresolved name ("`missing_helper()`: defined nowhere, not imported") | A3 (parsed-code call detection) |
| K3 | **Manager-proxied calls** are not linked and not reported (`KNOWN_LIMITATIONS.md`) | `py_repo/prism_blast_radius/manager_proxied_get.txt` (0 callers); `py_repo/prism_change_context/manager_proxied_get.txt` §4 ("No callers") | `services.find_book` listed as a caller, or a "not followed (manager proxy)" line | deferred (`KNOWN_LIMITATIONS.md`) |
| K5 | **The break check against the committed version (V2) is not exercised** | `py_repo/prism_change_context/worktree_edited_price.txt` §4 lists `checkout.total` (since the K4 fix) with "passes 2 positional arguments", but no break against the committed signature. `ts_repo/prism_change_context/worktree_edited_fetch_user.txt` §4 lists `loadProfile` with "passes 2 positional arguments", but no break against the committed signature | `loadProfile`: "passes 2 positional arguments; `fetchUser` now takes 1 (an object)" [fails]; `checkout.total`: "does not pass `currency`, now required" | A2 |
| K6 | **N1:** near duplicates are not detected | `py_repo/prism_new_function_context/beside_near_duplicates.txt` lists `sum_masses` as a same-file sibling only | the duplicate pair is named as candidates with evidence | B1 (pinned as "not detected" until it lands) |

**Not defects** (checked in this recording):
- `ts_repo`'s barrel, namespace, default-export and override callers are linked: `clean` 3, `sum` 2, `Rect.area` 1.
- The `async` arrow function `countWords` is parsed, with its signature and `Promise<number>` return.

**Fixed:**
- **K4** (2026-11-10, `src/prism/graph/import_roots.py`; `harness/experiments/r4/K4_DIAGNOSIS.md`): absolute imports of
  a package outside the repository root were left unresolved and marked external.
  - **What it changed** in `py_repo` (19 outputs; `ts_repo` unchanged):
    - `parse` gains `cli.main` (through the re-export), `cli.check` (through the alias), `plugins.load_csv` and
      `tests.test_parse.test_parse_two_lines`;
    - `price` gains `checkout.total`;
    - `normalize` gains `tests.helpers.clean`, and the hop-3 callers through `parse`;
    - the `pyproject.toml` entry point resolves ("1 registered entry points");
    - `prism_ask`'s ranks shift with the new edges.

