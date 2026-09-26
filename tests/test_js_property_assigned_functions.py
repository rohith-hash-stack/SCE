"""Tests for property-assigned function/arrow definitions in JS/TS
(`feature/js-property-assignment-symbols`) - `tests/fixtures/js/
property_assigned_functions.js` verifies `obj.prop = function name() {}`,
`obj.prop = function () {}`, `obj.prop = (args) => {}`, and
`exports.foo = function () {}` all register as real symbols with accurate
line ranges, matching the shape express@4.21.0's entire public API
(`app.handle`, `app.use`, `proto.route`, ...) is written in - previously
invisible to the symbol table entirely, since none of it is a
`function_declaration` or `method_definition` (the only two shapes the
JS/TS "definitions" query captured before this fix).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from prism.cli import build_pipeline

FIXTURE = Path(__file__).parent / "fixtures" / "js" / "property_assigned_functions.js"


def _build(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(FIXTURE, repo / "property_assigned_functions.js")
    return build_pipeline(str(repo))


def test_named_function_expression_assigned_to_property_registers(tmp_path) -> None:
    """`app.handle = function handle(req, res) {}` - the internal function
    name already satisfies the pre-existing `name:`-field path; this is a
    regression guard that adding the new query pattern didn't disturb it."""
    builder, _tag_matrix = _build(tmp_path)
    symbol = builder.symbol_table.get("property_assigned_functions.handle")
    assert symbol is not None
    assert symbol.kind == "function"
    assert symbol.line_range == (5, 7)


def test_anonymous_function_expression_assigned_to_property_registers(tmp_path) -> None:
    """`app.use = function (fn) {}` - no internal name at all; the
    registered name must come from the `use` property it was assigned to."""
    builder, _tag_matrix = _build(tmp_path)
    symbol = builder.symbol_table.get("property_assigned_functions.use")
    assert symbol is not None
    assert symbol.kind == "function"
    assert symbol.line_range == (9, 11)


def test_arrow_function_assigned_to_property_registers(tmp_path) -> None:
    """`app.route = (path) => {}` - arrow functions never have an internal
    name in JS's grammar, so this exercises the same property-name
    fallback as the anonymous-function-expression case above."""
    builder, _tag_matrix = _build(tmp_path)
    symbol = builder.symbol_table.get("property_assigned_functions.route")
    assert symbol is not None
    assert symbol.kind == "function"
    assert symbol.line_range == (13, 15)


def test_exports_dot_property_assignment_registers(tmp_path) -> None:
    """`exports.foo = function (a, b) {}` - the CommonJS export idiom is
    structurally identical to the general case (`exports` is just an
    ordinary identifier), so it needs no special-casing of its own."""
    builder, _tag_matrix = _build(tmp_path)
    symbol = builder.symbol_table.get("property_assigned_functions.foo")
    assert symbol is not None
    assert symbol.kind == "function"
    assert symbol.line_range == (17, 19)


def test_line_range_includes_the_assignment_prefix(tmp_path) -> None:
    """The rendered definition should start at `app.handle =`, not at the
    bare `function handle(...)` keyword, so a reader can see what the
    function was actually assigned to."""
    builder, _tag_matrix = _build(tmp_path)
    symbol = builder.symbol_table.get("property_assigned_functions.handle")
    lines = Path(symbol.file).read_text().splitlines()
    assert lines[symbol.line_range[0] - 1].strip() == "app.handle = function handle(req, res) {"


def test_computed_property_assignment_is_not_registered(tmp_path) -> None:
    """`app[method] = function (path) {}` inside a `methods.forEach(...)`
    loop - a *computed* property access (`subscript_expression`, not
    `member_expression`) whose real property name is a runtime loop
    variable, not a static identifier. This is a deliberate non-goal, not
    a silent miss: no symbol should be registered for it at all, under
    any name."""
    builder, _tag_matrix = _build(tmp_path)
    names = builder.symbol_table.all_qualified_names()
    assert not any("dynamicName" in n for n in names)
    # The only symbols this file should produce are the ones explicitly
    # covered by the other tests, plus the class/constructor/normal
    # function below - nothing extra leaked in from the forEach loop.
    assert set(names) == {
        "property_assigned_functions.handle",
        "property_assigned_functions.use",
        "property_assigned_functions.route",
        "property_assigned_functions.foo",
        "property_assigned_functions.normal",
        "property_assigned_functions.Router",
        "property_assigned_functions.Router.constructor",
        "property_assigned_functions.Router.bound",
    }


def test_plain_function_declaration_still_registers(tmp_path) -> None:
    """Regression guard: the pre-existing `function_declaration` path is
    unaffected by the new assignment-expression pattern."""
    builder, _tag_matrix = _build(tmp_path)
    symbol = builder.symbol_table.get("property_assigned_functions.normal")
    assert symbol is not None
    assert symbol.kind == "function"


def test_this_dot_property_assignment_inside_class_attaches_as_method(tmp_path) -> None:
    """`this.bound = function bound() {}` inside a constructor is the same
    AST shape as the module-level cases, but its ancestor chain does cross
    a real `class_declaration` - it should attach as a method of that
    class (`kind="method"`, correct `enclosing_class`), the same way any
    other definition nested in a class body already does."""
    builder, _tag_matrix = _build(tmp_path)
    symbol = builder.symbol_table.get("property_assigned_functions.Router.bound")
    assert symbol is not None
    assert symbol.kind == "method"
    assert symbol.enclosing_class == "property_assigned_functions.Router"


def test_tsx_shares_the_same_property_assignment_pattern(tmp_path) -> None:
    """TSX shares TYPESCRIPT_QUERIES directly - confirm the fix applies
    there too, not just plain .js/.ts files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "widget.tsx").write_text(
        "const widget = {};\nwidget.render = (props) => {\n  return props;\n};\n"
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    symbol = builder.symbol_table.get("widget.render")
    assert symbol is not None
    assert symbol.kind == "function"
