# Golden fixture `py_repo` (Python)

PRISM's golden tests index a temporary copy of this repository (`GOLDEN_TESTS_PLAN.md` §1). It is never indexed in
place. The harness copies it, runs `git init`, commits everything except `worktree_overlay/`, then copies
`worktree_overlay/` over the copy as an uncommitted edit.

| Element | Where | Exercises |
|---|---|---|
| a re-export (`from .core.parse import parse`), imported both ways | `src/shelf/__init__.py`; `cli.py`, `tests/test_parse.py` | T2 callers through re-exports; T5 import sites |
| an alias import and a call through it | `cli.py` (`parse as p`, `check`) | T2 through an alias |
| a base class, two overriding subclasses, a call on the base type | `exporters.py` (`Exporter.export`, `export_all`) | T4 overrides; virtual dispatch |
| a decorator registration into a registry dict; a CLI entry point | `registry.py`, `plugins/csv_plugin.py`; `pyproject.toml` `[project.scripts]` | T6 registrations |
| a `.pyi` stub for one function | `core/fmt.pyi` (`render`) | T7 type declarations |
| a docs page naming two functions | `docs/usage.md` (`parse`, `render`) | T9 docs |
| tests calling three functions directly and one through a helper | `tests/` (`parse`, `render`, `export_all`; `normalize` via `tests/helpers.py`) | T8 tests reaching a function; `include_tests=False` |
| **the D1 trap** | `values.py` `set_value`, `store.py` `Store.set_value`, `settings.py` bare `set_value(...)` with no import | Q1: the bare call must not resolve to the method |
| **the D2 trap** | `build.py` `assemble`: `return build(missing_helper(x))` | Q2: the unresolved call is listed, not dropped |
| a working-tree edit adding a required parameter; an unchanged caller | `worktree_overlay/src/shelf/pricing.py` over `pricing.py`; caller `checkout.py` | V2: the break check against the committed version |
| a near-duplicate pair (same body, identifiers renamed) | `dupes.py` (`total_weight`, `sum_masses`) | N1 (pinned as "not detected" until B1 lands) |
| raises, empty returns, state changes | `core/parse.py` `parse` (raises `ValueError`, returns `[]`), `store.py` `Store.set_value`, `models/query.py` `QuerySet.get` | T1, T3 |
| **manager-proxied calls** (Django's shape: manager methods made by `from_queryset`) | `models/query.py`, `models/book.py` (`objects = Manager()`), `services.py` `Book.objects.get(pk=pk)` | T2, Q2, L1 (`KNOWN_LIMITATIONS.md`): expected today, silent on the manager call |

Runs as a package: `PYTHONPATH=src:. pytest tests` (4 tests).
