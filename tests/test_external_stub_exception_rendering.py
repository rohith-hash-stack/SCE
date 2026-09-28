"""Architectural-audit Category 6: an external (Phase C, `role="external"`)
stub's `ContractExtractor`-computed `thrown_exceptions` was silently
discarded by `_render_signature_text` - an agent generating a call into a
stubbed external dependency saw only the type signature, with no
structured signal that the real function can raise. These tests exercise
the fix directly against real tree-sitter parses (never a real installed
package, unlike `test_external_index.py` - this is a pure rendering
concern, independent of any locator), matching the same synthetic-file
methodology `tests/test_export_registry.py` already uses for JS/TS.

Also covers the connected Category 6/9 follow-up: `_render_signature_text`
used to hardcode Python's `def`/`class` keywords for every language -
these tests confirm TS/JS and Go now render real, syntactically valid
declarations of their own instead.
"""
from __future__ import annotations

from prism.external.index import (
    ExternalSymbolInfo,
    _find_definition,
    _render_signature_text,
    _render_stub_body,
    external_symbol_to_node_entry,
)
from prism.graph.contracts import BehavioralContract, ContractExtractor, Parameter
from prism.parser.tree_sitter_loader import LanguageID, parse_file


def _signature_for(tmp_path, filename: str, source: str, symbol_name: str) -> str:
    path = tmp_path / filename
    path.write_text(source)
    parsed = parse_file(str(path))
    match = _find_definition(parsed, symbol_name)
    assert match is not None, f"{symbol_name!r} not found in {filename}"
    def_node, enclosing_class, qualified_name, kind = match
    contract = ContractExtractor().extract_symbol(def_node, parsed, enclosing_class, qualified_name)
    return _render_signature_text(qualified_name, kind, contract, parsed.language_id)


def test_python_stub_gets_a_raises_comment(tmp_path):
    signature = _signature_for(
        tmp_path,
        "pkg.py",
        "def fetch(url):\n"
        "    if not url:\n"
        "        raise ValueError('empty url')\n"
        "    raise TimeoutError('slow')\n",
        "fetch",
    )
    assert signature.splitlines()[0] == "# Raises: TimeoutError, ValueError"
    assert signature.splitlines()[1] == "def fetch(url):"


def test_python_stub_with_no_raises_is_unchanged(tmp_path):
    """Regression guard: a symbol with nothing to raise must render
    exactly as it did before this fix - no dangling comment, no blank
    leading line."""
    signature = _signature_for(tmp_path, "pkg.py", "def add(a, b):\n    return a + b\n", "add")
    assert signature == "def add(a, b):"


def test_ts_stub_gets_a_single_jsdoc_throws_tag(tmp_path):
    signature = _signature_for(
        tmp_path,
        "pkg.ts",
        "export function fetchIt(url: string): boolean {\n"
        "    if (!url) {\n"
        "        throw new Error('empty url');\n"
        "    }\n"
        "    return true;\n"
        "}\n",
        "fetchIt",
    )
    lines = signature.splitlines()
    assert lines[0] == "/** @throws {Error} */"
    # Category 9 follow-up: TS/JS now render real `declare function` syntax,
    # never Python's `def` keyword.
    assert lines[1] == "declare function fetchIt(url: string): boolean;"


def test_ts_stub_with_multiple_throws_uses_a_jsdoc_block(tmp_path):
    signature = _signature_for(
        tmp_path,
        "pkg.ts",
        "export function validate(x: number): boolean {\n"
        "    if (x < 0) { throw new RangeError('negative'); }\n"
        "    if (x > 100) { throw new TypeError('too big'); }\n"
        "    return true;\n"
        "}\n",
        "validate",
    )
    lines = signature.splitlines()
    assert lines[0] == "/**"
    assert " * @throws {RangeError}" in lines
    assert " * @throws {TypeError}" in lines
    assert lines[-2] == " */"
    assert lines[-1] == "declare function validate(x: number): boolean;"


def test_stub_body_assembly_places_annotation_above_the_declaration(tmp_path):
    """End-to-end through `external_symbol_to_node_entry`'s own body
    assembly (`f"{signature_text}\\n    ..."`) - confirms the multi-line
    annotation composes correctly with the existing stub-body convention,
    not just in isolation."""
    path = tmp_path / "pkg.py"
    path.write_text("def fetch(url):\n    raise TimeoutError('slow')\n")
    parsed = parse_file(str(path))
    match = _find_definition(parsed, "fetch")
    def_node, enclosing_class, qualified_name, kind = match
    contract = ContractExtractor().extract_symbol(def_node, parsed, enclosing_class, qualified_name)
    signature_text = _render_signature_text(qualified_name, kind, contract, parsed.language_id)

    info = ExternalSymbolInfo(
        qualified_name=f"pkg.{qualified_name}",
        module_origin="pkg",
        language=parsed.language_id,
        signature_text=signature_text,
        docstring=None,
        kind=kind,
        file=str(path),
        line=1,
        end_line=2,
    )
    entry = external_symbol_to_node_entry(info)
    assert entry.body == "# Raises: TimeoutError\ndef fetch(url):\n    ..."
    # The metadata boundary (Section 2.3) must still hold - the fix only
    # threads an already-extracted field through rendering, it doesn't
    # widen what gets computed for an external node.
    assert entry.contract is None


# --------------------------------------------------------------------- #
# Category 6/9 follow-up: language-agnostic signature syntax.
# --------------------------------------------------------------------- #
def test_ts_async_function_renders_async_keyword_not_python_async_def(tmp_path):
    signature = _signature_for(
        tmp_path,
        "pkg.ts",
        "export async function useAuth(token: string): Promise<boolean> {\n    return true;\n}\n",
        "useAuth",
    )
    assert signature == "declare async function useAuth(token: string): Promise<boolean>;"


def test_ts_class_stub_renders_declare_class_not_python_class_colon(tmp_path):
    signature = _signature_for(
        tmp_path,
        "pkg.ts",
        "export class Widget {\n    render(): void {}\n}\n",
        "Widget",
    )
    assert signature == "declare class Widget {}"


def test_js_stub_uses_the_same_ts_renderer_as_typescript(tmp_path):
    signature = _signature_for(
        tmp_path,
        "pkg.js",
        "function greet(name) {\n    return `hi ${name}`;\n}\n",
        "greet",
    )
    assert signature == "declare function greet(name);"


def test_go_function_renders_func_keyword_with_space_separated_params(tmp_path):
    signature = _signature_for(
        tmp_path,
        "pkg.go",
        "package pkg\n\nfunc Handle(w string, code int) error {\n\treturn nil\n}\n",
        "Handle",
    )
    assert signature == "func Handle(w string, code int) error"


def test_go_function_stub_body_gets_a_matching_brace_placeholder():
    contract = BehavioralContract(qualified_name="Handle", params=[Parameter("c", "*Context")], return_type="error")
    signature = _render_signature_text("pkg.Handle", "function", contract, LanguageID.GO)
    body = _render_stub_body(signature, "function", LanguageID.GO)
    assert body == "func Handle(c *Context) error {\n\t// ...\n}"


def test_go_struct_stub_body_needs_no_extra_placeholder():
    contract = BehavioralContract(qualified_name="Context")
    signature = _render_signature_text("pkg.Context", "class", contract, LanguageID.GO)
    body = _render_stub_body(signature, "class", LanguageID.GO)
    assert body == signature == "type Context struct {}"


def test_unsupported_language_falls_back_to_a_bare_keyword_free_declaration():
    """A language with no real renderer (e.g. Java/C#, unreachable today -
    no locator exists for either) must never silently default to Python's
    `def`/`class` syntax - that's the exact bug this whole refactor fixes
    for TS/Go. It renders a bare, keyword-less declaration instead."""
    contract = BehavioralContract(qualified_name="x", params=[Parameter("name", "String")], return_type="void")
    signature = _render_signature_text("pkg.Greeter.greet", "method", contract, "java")
    assert signature == "greet(name: String): void"
    assert "def " not in signature
    assert "class " not in signature
