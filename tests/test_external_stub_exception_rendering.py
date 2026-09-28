"""Architectural-audit Category 6: an external (Phase C, `role="external"`)
stub's `ContractExtractor`-computed `thrown_exceptions` was silently
discarded by `_render_signature_text` - an agent generating a call into a
stubbed external dependency saw only the type signature, with no
structured signal that the real function can raise. These tests exercise
the fix directly against real tree-sitter parses (never a real installed
package, unlike `test_external_index.py` - this is a pure rendering
concern, independent of any locator), matching the same synthetic-file
methodology `tests/test_export_registry.py` already uses for JS/TS.
"""
from __future__ import annotations

from prism.external.index import _find_definition, _render_signature_text
from prism.graph.contracts import ContractExtractor
from prism.parser.tree_sitter_loader import parse_file


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
    assert lines[1].startswith("def fetchIt")  # see the connected finding: signature body is Python-syntax today


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
    assert lines[-1].startswith("def validate")


def test_stub_body_assembly_places_annotation_above_the_declaration(tmp_path):
    """End-to-end through `external_symbol_to_node_entry`'s own body
    assembly (`f"{signature_text}\\n    ..."`) - confirms the multi-line
    annotation composes correctly with the existing stub-body convention,
    not just in isolation."""
    from prism.external.index import ExternalSymbolInfo, external_symbol_to_node_entry

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
