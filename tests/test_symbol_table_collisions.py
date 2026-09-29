"""Option C (`reports/symbol_table_collision_spike.md`, `epic/engine-
hardening-and-consolidation`): `GlobalSymbolTable.add` no longer lets a
genuine in-module simple-name collision silently overwrite an earlier,
unrelated real definition. Root cause: `ConcreteGraphBuilder._register_
definition`'s ancestor walk only ever detects an enclosing *class*, not
an enclosing *function* - a function nested inside another function
falls through to the same bare `module.name` qualified-name shape a
real top-level function gets, so two same-named functions (one
top-level, one buried in a closure, or both nested) collide on the
exact same dict key.

These tests reproduce both the general cross-language shape (a minimal
synthetic Python fixture, matching the spike's own live reproduction)
and the real, motivating instance found in the Express pilot
(`lib.router.param`: the router's own real, public registration
function vs. an unrelated private closure of the same name nested
inside `process_params`) as a synthetic JS fixture mirroring the real
shape exactly.
"""
from __future__ import annotations

from prism.cli import build_pipeline


def _build(tmp_path, filename: str, source: str):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / filename).write_text(source)
    builder, _tags = build_pipeline(str(repo))
    return builder


# --------------------------------------------------------------------- #
# General, cross-language shape (Python).
# --------------------------------------------------------------------- #
_PY_COLLISION_SOURCE = (
    "def real_api(name, fn):\n"
    "    \"\"\"The real, public top-level function.\"\"\"\n"
    "    return dispatch(name, fn)\n"
    "\n\n"
    "def dispatch(name, fn):\n"
    "    return fn(name)\n"
    "\n\n"
    "def process(items):\n"
    "    def real_api(err):\n"
    "        \"\"\"An unrelated nested closure that happens to share a name.\"\"\"\n"
    "        return err\n"
    "    return real_api(items[0])\n"
)


def test_python_nested_closure_no_longer_overwrites_the_top_level_function(tmp_path):
    builder = _build(tmp_path, "mod.py", _PY_COLLISION_SOURCE)

    primary = builder.symbol_table.get("mod.real_api")
    assert primary is not None
    # The real, public top-level function is lines 1-3 - `get()` on the
    # bare name must return *this* one, not the nested closure that used
    # to silently win by registering last.
    assert primary.line_range == (1, 3)

    collisions = builder.symbol_table.collisions_for("mod.real_api")
    assert len(collisions) == 2
    assert collisions[0].line_range == (1, 3)
    # The nested closure (lines 11-13) is real and separately preserved,
    # not dropped - findable under its own `#2` key.
    nested = builder.symbol_table.get("mod.real_api#2")
    assert nested is not None
    assert nested.line_range == (11, 13)
    assert nested in collisions


def test_python_non_colliding_names_are_completely_unaffected(tmp_path):
    """Zero behavior change for the overwhelming majority case: two
    functions with genuinely different names never touch the collision
    path, `collisions_for` returns exactly the one real symbol, and no
    `#N`-suffixed key is ever created."""
    source = "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n"
    builder = _build(tmp_path, "mod.py", source)

    assert builder.symbol_table.get("mod.alpha") is not None
    assert builder.symbol_table.get("mod.beta") is not None
    assert builder.symbol_table.collisions_for("mod.alpha") == [builder.symbol_table.get("mod.alpha")]
    assert builder.symbol_table.collisions == {}
    assert "mod.alpha#2" not in builder.symbol_table


# --------------------------------------------------------------------- #
# The real, motivating instance: Express's own lib.router.param.
# --------------------------------------------------------------------- #
_JS_ROUTER_PARAM_SOURCE = (
    "proto.param = function param(name, fn) {\n"
    "  var params = this._params;\n"
    "  for (var i = 0; i < params.length; ++i) {\n"
    "    var ret = params[i](name, fn);\n"
    "    if (ret) fn = ret;\n"
    "  }\n"
    "  (this.params[name] = this.params[name] || []).push(fn);\n"
    "  return this;\n"
    "};\n"
    "\n"
    "proto.process_params = function process_params(layer, called, req, res, done) {\n"
    "  var params = this.params;\n"
    "  var keys = layer.keys;\n"
    "\n"
    "  function param(err) {\n"
    "    if (err) return done(err);\n"
    "    return done();\n"
    "  }\n"
    "\n"
    "  function paramCallback(err) {\n"
    "    return param(err);\n"
    "  }\n"
    "\n"
    "  param();\n"
    "};\n"
)


def test_express_router_param_collision_reproduced_and_both_retained(tmp_path):
    """The exact real shape `express_t02_013_param_registration.yaml`
    found: `proto.param` (the router's own real, public registration
    API) and an unrelated private `param(err)` closure nested inside
    `proto.process_params` both register as bare `router.param` before
    this fix - the closure, processed later, silently won. Both real
    functions must now be independently retrievable."""
    builder = _build(tmp_path, "router.js", _JS_ROUTER_PARAM_SOURCE)

    primary = builder.symbol_table.get("router.param")
    assert primary is not None
    # `proto.param` is the real, public API - lines 1-9.
    assert primary.line_range == (1, 9)

    collisions = builder.symbol_table.collisions_for("router.param")
    assert len(collisions) == 2

    nested = builder.symbol_table.get("router.param#2")
    assert nested is not None
    # The nested `param(err)` closure inside `process_params` - lines 15-18.
    assert nested.line_range == (15, 18)

    # `process_params` itself is unaffected - a real, separate, non-
    # colliding symbol, exactly as it is in the real Express source.
    assert builder.symbol_table.get("router.process_params") is not None
    assert builder.symbol_table.collisions_for("router.process_params") == [
        builder.symbol_table.get("router.process_params")
    ]


def test_express_router_param_def_nodes_and_graph_nodes_stay_in_sync_with_the_symbol_table(tmp_path):
    """The collision fix must keep `ConcreteGraphBuilder._def_nodes` and
    the real `networkx` graph node set consistent with which key the
    symbol table actually stored each definition under - a caller asking
    for `router.param#2`'s own AST node or graph attributes must not
    silently get `router.param`'s (or nothing at all)."""
    builder = _build(tmp_path, "router.js", _JS_ROUTER_PARAM_SOURCE)

    assert builder.def_node("router.param") is not None
    assert builder.def_node("router.param#2") is not None
    assert builder.def_node("router.param") is not builder.def_node("router.param#2")

    assert "router.param" in builder.graph
    assert "router.param#2" in builder.graph
    assert builder.graph.nodes["router.param"]["line_range"] == (1, 9)
    assert builder.graph.nodes["router.param#2"]["line_range"] == (15, 18)


# --------------------------------------------------------------------- #
# Polysemy Disambiguation sees both real candidates now, not just one.
# --------------------------------------------------------------------- #
def test_candidates_for_simple_name_includes_every_real_collision_sibling(tmp_path):
    builder = _build(tmp_path, "router.js", _JS_ROUTER_PARAM_SOURCE)
    candidates = builder.symbol_table.candidates_for_simple_name("param")
    qualified_names = {c.qualified_name for c in candidates}
    assert qualified_names == {"router.param", "router.param#2"}
