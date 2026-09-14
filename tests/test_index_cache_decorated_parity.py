"""G38 regression coverage: `prism.runtime.index_cache`'s warm-path
rehydration of `builder._def_nodes` must resolve decorated definitions
exactly as a cold build does.

Root cause (see `_rehydrate_def_nodes`'s own docstring in
`src/prism/runtime/index_cache.py`): `SymbolInfo.line_range[0]` for a
decorated symbol is its decorator's line (`ConcreteGraphBuilder.
_register_definition` unwraps to the parent `decorated_definition`
node before computing `line_range` - concrete_builder.py:458-463), but
the plain `"definitions"` tree-sitter query used on a warm rebuild
captures the bare `function_definition`/`class_definition` node, whose
own `start_point` is the `def`/`class` keyword's line, not the
decorator's. Before the fix, that line-number mismatch meant the join
always missed for decorated symbols, so `_def_nodes` was silently
never populated for them on any warm/cache-hit build - `def_node()`
returned `None` for every decorated function, method, or class,
despite a cold build resolving the exact same symbol correctly.
"""
from __future__ import annotations

import shutil

from prism.cli import build_pipeline
from prism.runtime.index_cache import index_cache_path

_SYNTHETIC_SOURCE = (
    "def my_decorator(f):\n"
    "    return f\n"
    "\n"
    "\n"
    "class Widget:\n"
    "    def plain_method(self):\n"
    "        return 1\n"
    "\n"
    "    @my_decorator\n"
    "    def decorated_method(self):\n"
    "        return 2\n"
)


def _clear_index_cache(repo_path: str) -> None:
    cache_path = index_cache_path(repo_path)
    if cache_path.exists():
        cache_path.unlink()


def _cold_then_warm(repo_path: str, qualified_names: list[str]) -> None:
    """Build once cold, once warm, assert every given symbol resolves
    to a non-None, identical node both times. Run by each test twice
    (a fresh cold-then-warm pair each time, index cache explicitly
    cleared before each round's cold build) to catch flakiness."""
    for _round in range(2):
        _clear_index_cache(repo_path)
        cold_builder, _ = build_pipeline(repo_path)
        cold_nodes = {qname: cold_builder.def_node(qname) for qname in qualified_names}
        for qname, node in cold_nodes.items():
            assert node is not None, f"cold build: def_node({qname!r}) was None"

        warm_builder, _ = build_pipeline(repo_path)
        warm_nodes = {qname: warm_builder.def_node(qname) for qname in qualified_names}
        for qname, node in warm_nodes.items():
            assert node is not None, f"warm build: def_node({qname!r}) was None"

        for qname in qualified_names:
            cold_text = cold_nodes[qname].text
            warm_text = warm_nodes[qname].text
            assert cold_text == warm_text, (
                f"{qname}: cold and warm def_node text differ - "
                f"cold={cold_text!r} warm={warm_text!r}"
            )


def test_synthetic_decorated_and_plain_methods_survive_warm_rebuild(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(_SYNTHETIC_SOURCE)

    _cold_then_warm(
        str(repo),
        ["svc.Widget.plain_method", "svc.Widget.decorated_method"],
    )


def test_real_django_decorated_symbol_survives_warm_rebuild():
    """The exact symbol that originally surfaced G38: `@requires_tz_
    support`-decorated, in tests/file_storage/tests.py."""
    from benchmarks.corpora.resolver import resolve

    repo_path = str(resolve("django"))
    qname = "tests.file_storage.tests.FileStorageTests.test_file_get_modified_time_timezone"

    _cold_then_warm(repo_path, [qname])
