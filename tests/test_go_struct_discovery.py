"""Regression tests for a Go AST blind spot in Phase C's external-stub
discovery: `prism.external.index._iter_definitions` walked
`CLASS_NODE_TYPES`-listed nodes by checking `child.child_by_field_name(
"name")` directly on the matched node - true for every other language's
class-shaped node, but not Go's `type_declaration`, which carries no
`name`/`body` field of its own at all (the real name lives on its nested
`type_spec` child). Every Go struct - single-form (`type Foo struct {}`)
or grouped (`type ( Foo struct {} \n Bar struct {} )`) - was silently
dropped from Phase C's external-stub discovery, with no error raised
anywhere: `_find_definition("Context")` against a real Go file simply
returned `None`, as if the struct didn't exist.

Confirmed, while investigating, that `prism.graph.concrete_builder`'s own
struct registration (the main in-repo indexing path) was never affected -
`ConcreteGraphBuilder._collect_definitions_in_file` resolves Go structs
through `prism.parser.queries.GO_QUERIES`'s tree-sitter query, which
already captures the `type_spec` node directly (not the outer
`type_declaration`) as `@def.class`, so `type_spec.child_by_field_name(
"name")` already worked there. The bug was confined to this module's own,
separately hand-rolled `_iter_definitions` walk, which does not use that
query system.
"""
from __future__ import annotations

from prism.external.index import _find_definition
from prism.graph.contracts import ContractExtractor
from prism.parser.tree_sitter_loader import parse_source


def test_single_go_struct_is_discovered_with_kind_class():
    parsed = parse_source(
        "pkg.go",
        b"package pkg\n\ntype Context struct {\n\tName string\n}\n",
    )
    match = _find_definition(parsed, "Context")
    assert match is not None
    def_node, enclosing_class, qualified_name, kind = match
    assert qualified_name == "Context"
    assert kind == "class"
    assert enclosing_class is None


def test_single_go_struct_has_a_real_non_degenerate_source_range():
    source = b"package pkg\n\ntype Context struct {\n\tName string\n}\n"
    parsed = parse_source("pkg.go", source)
    def_node, _, _, _ = _find_definition(parsed, "Context")
    # The struct's own span (`Context struct {...}`), not the whole file
    # and not a fabricated single-line placeholder.
    assert def_node.start_point[0] == 2  # "type Context struct {" (0-indexed)
    assert def_node.end_point[0] == 4  # the closing "}"
    assert def_node.start_point[0] < def_node.end_point[0]


def test_grouped_go_struct_declaration_discovers_every_member():
    """`type (\\n Foo struct {}\\n Bar struct {}\\n)` wraps multiple
    `type_spec` children under one `type_declaration` node - both members
    must be independently discoverable, each with its own real source
    range, not just the first."""
    source = (
        b"package pkg\n\n"
        b"type (\n"
        b"\tFoo struct {\n\t\tA int\n\t}\n"
        b"\tBar struct {\n\t\tB int\n\t}\n"
        b")\n"
    )
    parsed = parse_source("pkg.go", source)

    foo_match = _find_definition(parsed, "Foo")
    bar_match = _find_definition(parsed, "Bar")
    assert foo_match is not None
    assert bar_match is not None

    foo_node, _, foo_qname, foo_kind = foo_match
    bar_node, _, bar_qname, bar_kind = bar_match
    assert (foo_qname, foo_kind) == ("Foo", "class")
    assert (bar_qname, bar_kind) == ("Bar", "class")
    # Each member's range covers only its own struct body, not the whole
    # `type (...)` group and not each other's.
    assert foo_node.start_point != bar_node.start_point
    assert foo_node.end_point[0] < bar_node.start_point[0]


def test_go_struct_contract_extraction_succeeds_with_no_params():
    """A class-kind `def_node` (here, the `type_spec`) must extract
    cleanly through `ContractExtractor` exactly as any other class-kind
    node does - no params, no return type, no crash from a node shape
    `_extract_params`/`_extract_return_type` don't expect."""
    parsed = parse_source("pkg.go", b"package pkg\n\ntype Context struct {\n\tName string\n}\n")
    def_node, enclosing_class, qualified_name, kind = _find_definition(parsed, "Context")
    contract = ContractExtractor().extract_symbol(def_node, parsed, enclosing_class, qualified_name)
    assert contract.params == []
    assert contract.return_type is None


def test_go_type_alias_is_not_discovered_as_a_struct():
    """`type UserID int` is a plain alias, not a struct - matching
    `GO_QUERIES["definitions"]`'s own `(struct_type)`-gated query, this
    must never register as a class-kind symbol here either."""
    parsed = parse_source("pkg.go", b"package pkg\n\ntype UserID int\n")
    assert _find_definition(parsed, "UserID") is None


def test_go_interface_is_not_discovered_as_a_struct():
    """`type Reader interface {...}` is an interface, not a struct - same
    `(struct_type)` gate, same reasoning as the type-alias case above."""
    parsed = parse_source("pkg.go", b"package pkg\n\ntype Reader interface {\n\tRead() error\n}\n")
    assert _find_definition(parsed, "Reader") is None


def test_go_function_discovery_is_unaffected_by_the_struct_fix():
    """Regression guard: the new Go-specific branch in `_iter_definitions`
    must not disturb ordinary Go function discovery, which shares the
    same walk."""
    parsed = parse_source(
        "pkg.go",
        b"package pkg\n\nfunc Handle(w string, code int) error {\n\treturn nil\n}\n",
    )
    match = _find_definition(parsed, "Handle")
    assert match is not None
    _, _, qualified_name, kind = match
    assert (qualified_name, kind) == ("Handle", "function")
