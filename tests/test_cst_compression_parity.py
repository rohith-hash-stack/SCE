"""Tests for Item 11 (second post-implementation audit): Cross-Language
CST Compression Parity - `UniversalSlicer.skeletonize` (L1) for
JS/TS/Go must strip comments while retaining call arguments, matching
`compress_python`'s L1 (`ArgPreservingSkeletonizer`, Issue #10).

Before this fix: comments passed through L1 untouched (never classified
as prunable), and a retained call's real arguments were always collapsed
to a placeholder (`doit(1, 2, 3)` -> `doit(/* ... */)`) - both confirmed
directly against a real Go fixture before any change was made.
"""
from __future__ import annotations

from prism.parser.tree_sitter_loader import LanguageID, parse_source
from prism.slicer.universal_slicer import UniversalSlicer

slicer = UniversalSlicer()


def _def_node(language_id: str, source: bytes, function_type: str):
    path = {"go": "x.go", "javascript": "x.js", "typescript": "x.ts"}[language_id]
    parsed = parse_source(path, source)

    def find(node, t):
        if node.type == t:
            return node
        for child in node.children:
            found = find(child, t)
            if found is not None:
                return found
        return None

    return find(parsed.root_node, function_type)


def test_javascript_l1_strips_line_comment_retains_call_args() -> None:
    source = b"""function f(x) {
  // this comment must be stripped
  return helper(x, 42, "hello");
}
"""
    node = _def_node(LanguageID.JAVASCRIPT, source, "function_declaration")
    skeleton = slicer.skeletonize(source, node, LanguageID.JAVASCRIPT)
    assert "this comment must be stripped" not in skeleton
    assert 'helper(x, 42, "hello")' in skeleton


def test_javascript_l1_strips_block_comment() -> None:
    source = b"""function f(x) {
  /* block comment
     spanning lines */
  return helper(x);
}
"""
    node = _def_node(LanguageID.JAVASCRIPT, source, "function_declaration")
    skeleton = slicer.skeletonize(source, node, LanguageID.JAVASCRIPT)
    assert "block comment" not in skeleton
    assert "helper(x)" in skeleton


def test_typescript_l1_strips_comment_retains_call_args() -> None:
    source = b"""function f(x: number): number {
  // strip me
  return helper(x, "literal");
}
"""
    node = _def_node(LanguageID.TYPESCRIPT, source, "function_declaration")
    skeleton = slicer.skeletonize(source, node, LanguageID.TYPESCRIPT)
    assert "strip me" not in skeleton
    assert 'helper(x, "literal")' in skeleton


def test_go_l1_strips_comment_retains_call_args() -> None:
    source = b"""package main

func f(x int) int {
	// this is a comment that should be stripped
	y := helper(x, 42, "hello")
	return y
}
"""
    node = _def_node(LanguageID.GO, source, "function_declaration")
    skeleton = slicer.skeletonize(source, node, LanguageID.GO)
    assert "this is a comment that should be stripped" not in skeleton
    assert 'helper(x, 42, "hello")' in skeleton


def test_go_l1_strips_multiple_comments_in_different_positions() -> None:
    source = b"""package main

func f(x int) int {
	// leading comment
	y := helper(x)
	// trailing comment
	return y
}
"""
    node = _def_node(LanguageID.GO, source, "function_declaration")
    skeleton = slicer.skeletonize(source, node, LanguageID.GO)
    assert "leading comment" not in skeleton
    assert "trailing comment" not in skeleton
    assert "helper(x)" in skeleton


def test_go_l1_output_still_reparses_cleanly_after_comment_stripping() -> None:
    from tests.test_universal_slicer import _reparse_cleanly  # reuse existing helper

    source = b"""package main

func f(x int) int {
	// comment
	y := helper(x, 42)
	if y > 0 {
		// another comment
		return y
	}
	return 0
}
"""
    node = _def_node(LanguageID.GO, source, "function_declaration")
    skeleton = slicer.skeletonize(source, node, LanguageID.GO)
    assert _reparse_cleanly(LanguageID.GO, skeleton) == 0, skeleton
