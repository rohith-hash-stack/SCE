# Phase 2 tasks (Phase E; built 2026-11-21)

20 tasks:
- **15 derived** (the user's decision, 2026-11-21): 5 rename, 5 signature change, 5 module move.
- **5 controls:** the existing tasks every arm solved most often in the 600-run re-baseline.

File: `tasks.json` (sha256 in it). Answer keys: `gold/` (never given to a run). Scout and attempts: `scout.json`,
`attempts.json`.

**How the derived tasks were made** (`derive_scout.py`, `derive_tasks.py`, `derive_scoring_check.py`):
- **Targets:** production definitions with a unique name, used from 3–10 other files (at most 30 uses to update).
  The count is the union of PRISM's graphs and the graph-coverage benchmark's own reading.
  - Excluded: examples, docs and scripts, and methods of classes with a base from outside the repository (they may
    override a library's method).
  - Order: grep's precision lowest first, so the targets whose name also appears in many unrelated files come
    first.
- **The answer key** is made mechanically in every dependent file; then the task is validated in the repository's
  own test environment:
  - the test files that use the target pass at the start (3 runs);
  - the answer key passes them, a generated hidden test, and the static completeness check (3 runs, no flaky test);
  - the start fails the check and at least one scored test;
  - end to end, through the runner's own scorer and the done gate: the answer key is resolved and accepted, the
    untouched start is neither.
- **Scoring:**
  - the scored tests: the dependent test files as the answer key leaves them, plus one generated test of the
    change itself;
  - the static check (`derived_checks.py`): no file uses the old name, passes the parameter positionally, or
    imports from the old module, more often than the answer key's tree does.

  The done gate checks the same, and says what is unmet and how many places, never where.
- **One task per family per repository** (a second allowed only for the fifth signature task).

| Task ID | Repository | Family | Target | Dependent files (tests) | grep precision |
|---|---|---|---|---|---|
| p2-rename-01 | werkzeug | rename | `File` | 3 (1) | 27% |
| p2-rename-02 | litestar | rename | `sync_to_thread` | 6 (1) | 3% |
| p2-rename-03 | fastmcp | rename | `OAuth` | 7 (2) | 4% |
| p2-rename-04 | pydantic-ai | rename | `SpanTree.find` | 3 (1) | 6% |
| p2-rename-05 | instructor | rename | `ValidationError` | 3 (1) | 5% |
| p2-signature-01 | werkzeug | signature | `parse_dict_header` | 3 (1) | 60% |
| p2-signature-02 | fastmcp | signature | `Context.get_state` | 4 (2) | 40% |
| p2-signature-03 | pydantic-ai | signature | `check_object_json_schema` | 4 (1) | 80% |
| p2-signature-04 | instructor | signature | `JsonCompleteness.analyze` | 3 (2) | 17% |
| p2-signature-05 | litestar | signature | `Litestar.route_reverse` | 7 (3) | 64% |
| p2-move-01 | scrapy | move | `iter_spider_classes` | 3 (1) | 75% |
| p2-move-02 | litestar | move | `envflag` | 7 (1) | 100% |
| p2-move-03 | fastmcp | move | `with_argv` | 3 (2) | 75% |
| p2-move-04 | pydantic-ai | move | `amazon_model_profile` | 7 (3) | 88% |
| p2-move-05 | instructor | move | `is_async` | 4 (1) | 15% |
| r4s-delete-10 | prettier | delete (control) | the constructor of class `EditorState` | - | - |
| r4s-delete-08 | werkzeug | delete (control) | the function `_get_environ` | - | - |
| r4s-delete-09 | litestar | delete (control) | the function `Dependency` | - | - |
| r4s-feature-02 | litestar | feature (control) |  | - | - |
| r4s-delete-05 | multidict | delete (control) | the function `GenericAlias` | - | - |

"grep precision": of the other files where `git grep -w` finds the name, the share that use the target.
- **Low (3–27%):** most matching files are unrelated, as in four of five renames.
- **High (60–100%):** most signature and move targets. A plain grep finds their files; the work is updating each
  site correctly.

**Limits:**
- **The derived tasks are Python only** (PRISM's reference pass is Python); one control is JS (prettier).
- **Repositories with no derived task:**
  - typer and multidict: no target in range;
  - aiohttp and pydantic: every candidate was rejected, mostly because the repository's own tests fail at the start
    in this environment.
- **Validated locally in this session's environments.** A Kaggle environment may differ (the smoke test checks).
