"""JS/TS functions bound by declaration (`const f = () => {}`) are indexed.

Before: a module-level `const f = () => {}` was an `attribute` (not
searchable, no callers), `export const f = ...` had no symbol at all, and a
nested `const` closure was invisible, so every call to them was lost - most
of Hono's API, and a share of zod's and tRPC's.
"""
from __future__ import annotations

import pytest

from prism.cli import build_pipeline
from prism.graph.entry_points import find_entry_points
from prism.summaries.cards import build_card

M_TS = """\
const helper = (x: number) => x * 2
/** Doubles then adds. */
export const exported = (x: number) => helper(x)
const expr = function inner(y: number) { return helper(y) }
export function decl(z: number) { return exported(z) + expr(z) }
export default (a: number) => a
const handlers = { onSave: (v: string) => v }
function outer() { const nested = () => 1; return nested() }
const a = 1, multi = () => a
export let gen = function* () { yield 1 }
export const typed: (n: number) => number = async (n) => n
"""
USE_TS = "import { exported } from './m'\nexport const run = async () => exported(3)\n"
M_JS = "const plain = function () { return 1 }\nmodule.exports.caller = () => plain()\n"


@pytest.fixture()
def root(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/m.ts").write_text(M_TS)
    (tmp_path / "src/use.ts").write_text(USE_TS)
    (tmp_path / "src/c.js").write_text(M_JS)
    return tmp_path


def _symbols(builder):
    return {s.qualified_name: s for s in builder.symbol_table}


def _calls(builder):
    return {(u, v) for u, v, d in builder.graph.edges(data=True) if d.get("relation", "CALLS") == "CALLS"}


def test_const_bound_functions_are_functions_with_statement_line_ranges(root):
    b = build_pipeline(str(root), use_cache=False)[0]
    sym = _symbols(b)
    expected = {"helper": (1, 1), "exported": (3, 3), "expr": (4, 4), "nested": (8, 8), "multi": (9, 9),
                "gen": (10, 10), "typed": (11, 11)}
    for name, lines in expected.items():
        s = sym[f"src.m.{name}"]
        assert (s.kind, s.line_range) == ("function", lines), name
        assert b.def_node(s.qualified_name) is not None
    assert "src.m.inner" not in sym  # the binding name wins over the expression's own name
    assert sym["src.m.a"].kind == "attribute" and sym["src.m.handlers"].kind == "attribute"  # non-function values stay
    assert sym["src.m.nested"].nested_in_function
    assert not any(q.startswith("src.m.default") or q.endswith(".onSave") for q in sym)  # unnamed / object literal
    assert sym["src.c.plain"].kind == "function"  # JavaScript too


def test_calls_to_and_from_const_bound_functions_resolve(root):
    b = build_pipeline(str(root), use_cache=False)[0]
    assert {("src.m.decl", "src.m.exported"), ("src.m.exported", "src.m.helper"), ("src.m.expr", "src.m.helper"),
            ("src.m.outer", "src.m.nested"), ("src.use.run", "src.m.exported")} <= _calls(b)  # incl. across an import


def test_the_export_statement_starts_the_line_range_and_carries_the_jsdoc(root):
    b = build_pipeline(str(root), use_cache=False)[0]
    card = build_card(b, {}, find_entry_points(b), "src.m.exported", axes=False)["card"]
    assert "Docstring: Doubles then adds." in card
    assert "Signature & Type Hints: (x: number)" in card


def test_a_cached_rebuild_restores_every_definition_node(root):
    cold = build_pipeline(str(root), use_cache=False)[0]
    build_pipeline(str(root))  # writes the index cache
    warm = build_pipeline(str(root))[0]  # cache hit: def nodes rehydrated by start line
    for q, s in _symbols(cold).items():
        w = warm.symbol_table.get(q)
        assert w is not None and (w.kind, w.line_range) == (s.kind, s.line_range), q
        assert (warm.def_node(q) is None) == (cold.def_node(q) is None), q
        if cold.def_node(q) is not None:
            assert warm.def_node(q).start_byte == cold.def_node(q).start_byte, q
    assert _calls(warm) == _calls(cold)
