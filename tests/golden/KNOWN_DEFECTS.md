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
| K4 | **New, found by this recording: src-layout Python imports are not resolved.** `py_repo` keeps its package in `src/shelf/` and imports it as `shelf…`. PRISM names its modules `src.shelf…`, so no call across modules is linked | `py_repo/prism_blast_radius/parse.txt` and `parse_with_tests.txt` (0 callers; expected `cli.main`, `cli.check` through the alias, `plugins.csv_plugin.load_csv`, and with tests `tests.test_parse.*`); `worktree_edited_price.txt` (0; expected `checkout.total`); `d1_module_function.txt`; `normalize_with_tests.txt` (no test callers, only same-module ones); every `py_repo` change context's §4 that should list a cross-module caller | each cross-module caller listed, through the re-export (`shelf/__init__.py`) and the alias (`parse as p`) | to investigate. Is it src-layout detection (the fixture's `pyproject.toml` declares no package directory), or general? R3a's src-layout repositories (packaging, poetry) are where to check first |
| K5 | **The break check against the committed version (V2) is not exercised** | `py_repo/prism_change_context/worktree_edited_price.txt` (no break: masked by K4, no caller is found). `ts_repo/prism_change_context/worktree_edited_fetch_user.txt` §4 lists `loadProfile` with "passes 2 positional arguments", but no break against the committed signature | `loadProfile`: "passes 2 positional arguments; `fetchUser` now takes 1 (an object)" [fails]; `checkout.total`: "does not pass `currency`, now required" | A2 (and K4 for the Python case) |
| K6 | **N1:** near duplicates are not detected | `py_repo/prism_new_function_context/beside_near_duplicates.txt` lists `sum_masses` as a same-file sibling only | the duplicate pair is named as candidates with evidence | B1 (pinned as "not detected" until it lands) |

**Not defects** (checked in this recording):
- `ts_repo`'s barrel, namespace, default-export and override callers are linked: `clean` 3, `sum` 2, `Rect.area` 1.
- The `async` arrow function `countWords` is parsed, with its signature and `Promise<number>` return.
