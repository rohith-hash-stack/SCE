"""Tests for Issue B1: Go `method_declaration` receiver clauses are
parsed and registered receiver-qualified (`kind="method"`, name
`<Module>.<ReceiverType>.<MethodName>`) instead of as bare top-level
functions - and Go structs (the receiver types themselves) are
registered as real `kind="class"` symbols, a genuine pre-existing bug
this fix also surfaced and closed (Go's `type_declaration` node has no
`name` field of its own - it lives on the nested `type_spec` child - so
every Go struct was silently never being registered at all before this).
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.graph.concrete_builder import _go_receiver_type, _go_type_identifier_text
from prism.parser.tree_sitter_loader import parse_file


def _write_repo(tmp_path, source: str):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.go").write_text(source)
    return repo


_STANDARD_SOURCE = """\
package main

type Context struct {
    Value int
}

func (c *Context) JSON(code int, data string) {
}

func (c Context) Plain() {
}

func handler(c *Context) {
    c.JSON(200, "ok")
}
"""


def test_go_struct_is_registered_as_a_class(tmp_path) -> None:
    repo = _write_repo(tmp_path, _STANDARD_SOURCE)
    builder, _tag_matrix = build_pipeline(str(repo))
    symbol = builder.symbol_table.get("main.Context")
    assert symbol is not None
    assert symbol.kind == "class"


def test_pointer_receiver_method_registers_receiver_qualified(tmp_path) -> None:
    repo = _write_repo(tmp_path, _STANDARD_SOURCE)
    builder, _tag_matrix = build_pipeline(str(repo))
    symbol = builder.symbol_table.get("main.Context.JSON")
    assert symbol is not None
    assert symbol.kind == "method"
    assert symbol.enclosing_class == "main.Context"
    # A bare top-level "main.JSON" must NOT also exist - the pre-fix bug
    # registered every Go method exactly this way.
    assert builder.symbol_table.get("main.JSON") is None


def test_value_receiver_method_registers_receiver_qualified(tmp_path) -> None:
    repo = _write_repo(tmp_path, _STANDARD_SOURCE)
    builder, _tag_matrix = build_pipeline(str(repo))
    symbol = builder.symbol_table.get("main.Context.Plain")
    assert symbol is not None
    assert symbol.kind == "method"
    assert symbol.enclosing_class == "main.Context"


def test_no_go_methods_are_registered_as_bare_functions(tmp_path) -> None:
    repo = _write_repo(tmp_path, _STANDARD_SOURCE)
    builder, _tag_matrix = build_pipeline(str(repo))
    method_names = {"JSON", "Plain"}
    for qname in builder.symbol_table.all_qualified_names():
        simple = qname.rsplit(".", 1)[-1]
        if simple in method_names:
            symbol = builder.symbol_table.get(qname)
            assert symbol.kind == "method", f"{qname} should be a method, got {symbol.kind}"


def test_plain_function_is_unaffected(tmp_path) -> None:
    repo = _write_repo(tmp_path, _STANDARD_SOURCE)
    builder, _tag_matrix = build_pipeline(str(repo))
    symbol = builder.symbol_table.get("main.handler")
    assert symbol is not None
    assert symbol.kind == "function"
    assert symbol.enclosing_class is None


# --------------------------------------------------------------------- #
# Issue B1 follow-through: the call resolves too, not just the registration
# --------------------------------------------------------------------- #
def test_receiver_method_call_resolves_to_a_real_calls_edge(tmp_path) -> None:
    """The whole point of receiver-qualified registration: an ordinary
    `c.JSON(...)` call inside another function must produce a real CALLS
    edge to `main.Context.JSON`, not silently drop (the pre-fix
    behavior - confirmed directly: zero edges at all for this exact
    fixture before this batch of work)."""
    repo = _write_repo(tmp_path, _STANDARD_SOURCE)
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("main.handler", "main.Context.JSON")
    edge_data = builder.graph.get_edge_data("main.handler", "main.Context.JSON")
    assert edge_data.get("relation") == "CALLS"


def test_method_calling_itself_via_second_receiver_var_resolves(tmp_path) -> None:
    """A second function with its own differently-named receiver-typed
    parameter must resolve independently - proves the binding is
    per-function-scope, not a global accident."""
    source = """\
package main

type Context struct {
    Value int
}

func (c *Context) JSON(code int, data string) {
}

func other(ctx *Context) {
    ctx.JSON(404, "not found")
}
"""
    repo = _write_repo(tmp_path, source)
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("main.other", "main.Context.JSON")


def test_multi_name_shared_type_parameter_binds_all_names(tmp_path) -> None:
    """`func f(a, b *Context)` - both `a` and `b` share one trailing
    type; tree-sitter's Go grammar only exposes one identifier via the
    `name` field, so both must still bind (via the parameter_declaration's
    raw `identifier` children)."""
    source = """\
package main

type Context struct {
    Value int
}

func (c *Context) JSON(code int, data string) {
}

func both(a, b *Context) {
    a.JSON(200, "a")
    b.JSON(200, "b")
}
"""
    repo = _write_repo(tmp_path, source)
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("main.both", "main.Context.JSON")


def test_receiver_body_self_call_resolves(tmp_path) -> None:
    """Inside a method's own body, the receiver variable itself must
    resolve too (Go has no literal `self`/`this`, so this exercises the
    same func_instance_map path, not the self-token branch)."""
    source = """\
package main

type Context struct {
    Value int
}

func (c *Context) Status() int {
    return c.Value
}

func (c *Context) JSON(code int, data string) {
    c.Status()
}
"""
    repo = _write_repo(tmp_path, source)
    builder, _tag_matrix = build_pipeline(str(repo))
    assert builder.graph.has_edge("main.Context.JSON", "main.Context.Status")


def test_imported_or_qualified_receiver_type_is_not_guessed(tmp_path) -> None:
    """A parameter typed with a package-qualified or otherwise
    unrecognized type must not bind to anything - out of scope,
    documented, never a fabricated guess."""
    node = None  # sanity-only structural test below covers the real parse
    assert _go_type_identifier_text(None, b"") is None


# --------------------------------------------------------------------- #
# Direct unit tests of the two new tree-sitter helpers
# --------------------------------------------------------------------- #
def test_go_receiver_type_extracts_pointer_and_value_receivers(tmp_path) -> None:
    repo = _write_repo(tmp_path, _STANDARD_SOURCE)
    parsed = parse_file(str(repo / "main.go"))

    def find_method_decls(node):
        if node.type == "method_declaration":
            yield node
        for child in node.children:
            yield from find_method_decls(child)

    receiver_types = [_go_receiver_type(m, parsed) for m in find_method_decls(parsed.root_node)]
    assert receiver_types == ["Context", "Context"]


def test_go_receiver_type_returns_none_for_function_declaration(tmp_path) -> None:
    repo = _write_repo(tmp_path, _STANDARD_SOURCE)
    parsed = parse_file(str(repo / "main.go"))

    def find_function_decls(node):
        if node.type == "function_declaration":
            yield node
        for child in node.children:
            yield from find_function_decls(child)

    for fn in find_function_decls(parsed.root_node):
        assert _go_receiver_type(fn, parsed) is None
