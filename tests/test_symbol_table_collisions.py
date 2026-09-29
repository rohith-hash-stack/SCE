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
from prism.engine import PrismEngine


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


# --------------------------------------------------------------------- #
# Object-literal methods owned by an enclosing function (tRPC's own
# `core/router.ts`: `createRouterFactory` returns `createRouterInner`,
# which returns `{ createCaller(ctx) {...}, getErrorShape(opts) {...} }`)
# must scope to that owning function, not collide with the flat module
# namespace the way `_register_definition`'s class-only ancestor walk
# used to leave them in - a real, live recall gap found running Phase 3's
# own zero-LLM-cost graph-quality spike against the real, pinned tRPC
# corpus (`createCallerFactory`/`getHTTPStatusCodeFromError`, both real
# calls made from inside these two methods, never appeared in either
# method's own candidate manifest before this fix, since the methods
# themselves were mis-scoped to the bare module rather than to the
# function that actually owns/returns the object they live on).
# --------------------------------------------------------------------- #
_TRPC_ROUTER_FACTORY_SOURCE = (
    "function createRouterFactory(config) {\n"
    "  return function createRouterInner(procedures) {\n"
    "    function realCallee() {\n"
    "      return 1;\n"
    "    }\n"
    "    const router = {\n"
    "      createCaller(ctx) {\n"
    "        return realCallee(ctx);\n"
    "      },\n"
    "      getErrorShape(opts) {\n"
    "        return unrelatedHelper(opts);\n"
    "      },\n"
    "    };\n"
    "    return router;\n"
    "  };\n"
    "}\n"
    "\n"
    "function unrelatedHelper(opts) {\n"
    "  return opts;\n"
    "}\n"
)


def test_object_literal_method_owned_by_enclosing_function_gets_scoped_name(tmp_path):
    builder = _build(tmp_path, "router.js", _TRPC_ROUTER_FACTORY_SOURCE)

    create_caller = builder.symbol_table.get("router.createRouterInner.createCaller")
    assert create_caller is not None
    assert create_caller.kind == "method"
    assert create_caller.enclosing_class == "router.createRouterInner"
    assert create_caller.line_range == (7, 9)

    get_error_shape = builder.symbol_table.get("router.createRouterInner.getErrorShape")
    assert get_error_shape is not None
    assert get_error_shape.enclosing_class == "router.createRouterInner"

    # The flat, unscoped names must never exist - that's the exact bug
    # (a real top-level `createCaller`/`getErrorShape` would collide with
    # these; here there happens to be no real collision, but the wrong
    # scope alone was already the problem: real calls made from inside
    # these methods never reached their own candidate manifest, since
    # `iter_scoped_nodes` correctly treats a registered `method_
    # definition` as its own separate scope boundary - correct only once
    # the method itself is registered under its real, owning scope).
    assert "router.createCaller" not in builder.symbol_table
    assert "router.getErrorShape" not in builder.symbol_table

    # A plain top-level function with no object-literal ancestor at all
    # is completely unaffected.
    assert builder.symbol_table.get("router.realCallee") is not None
    assert builder.symbol_table.get("router.unrelatedHelper") is not None


def test_object_literal_method_scoped_symbol_is_independently_seedable(tmp_path):
    """The whole point of the fix: `createCaller`'s own real call
    (`realCallee`) must appear in *its own* candidate manifest once it is
    used as a seed - not silently dropped the way it was before this fix
    scoped the method under its real owning function."""
    builder = _build(tmp_path, "router.js", _TRPC_ROUTER_FACTORY_SOURCE)
    engine = PrismEngine(builder, str(tmp_path / "repo"))
    manifest_text, candidate_universe = engine.build_candidate_manifest(
        "router.createRouterInner.createCaller"
    )
    assert "router.realCallee" in candidate_universe
    assert "calls=[router.realCallee]" in manifest_text


def test_object_literal_owned_by_anonymous_function_falls_back_safely(tmp_path):
    """An IIFE (`(function () { return {...}; })()`) has no derivable
    name at all - neither its own `name:` field nor an enclosing
    `variable_declarator` (its immediate parent is the IIFE's own call
    expression, not an assignment). The fix must never crash chasing a
    name that doesn't exist; it falls back to this table's pre-existing
    flat registration instead of fabricating one."""
    source = (
        "const router = (function () {\n"
        "  return {\n"
        "    handle(req) {\n"
        "      return process(req);\n"
        "    },\n"
        "  };\n"
        "})();\n"
        "\n"
        "function process(req) {\n"
        "  return req;\n"
        "}\n"
    )
    builder = _build(tmp_path, "router.js", source)
    handle = builder.symbol_table.get("router.handle")
    assert handle is not None
    assert handle.enclosing_class is None


def test_plain_top_level_object_literal_is_completely_unaffected(tmp_path):
    """A genuinely top-level object literal (`const api = { foo() {...},
    bar() {...} }`, no enclosing function at all between it and the
    module) never enters the object-literal-scoping path in the first
    place - zero behavior change for this, the overwhelmingly common,
    pre-existing case."""
    source = (
        "const api = {\n"
        "  foo() {\n"
        "    return 1;\n"
        "  },\n"
        "  bar() {\n"
        "    return api.foo();\n"
        "  },\n"
        "};\n"
    )
    builder = _build(tmp_path, "router.js", source)
    assert builder.symbol_table.get("router.foo") is not None
    assert builder.symbol_table.get("router.bar") is not None
    assert builder.symbol_table.get("router.foo").enclosing_class is None
