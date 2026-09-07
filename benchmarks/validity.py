"""Syntactic validity checks (HLD evaluation dimension 3): every code block
Prism renders into its Markdown context package must still parse. This is a
direct, black-box check against the rendered Markdown text itself (not
against internal compressor state), so it also catches any bug the
Markdown serializer might introduce independent of the compressor.

Python gets an exact `ast.parse` check (`check_python_syntax`). Every other
language Prism renders (JS/TS/Go/Java/C#) gets `check_tree_sitter_syntax`
instead - a real Tree-sitter reparse (`root_node.has_error`), the same
authoritative "is this still valid code" check `tests/test_universal_slicer.py`
and `tests/test_polyglot_enterprise.py` already use, rather than a second
textual approximation.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass

# Matches a "### <symbol> (<resolution label>)" heading immediately
# followed by a fenced code block, capturing the language tag and body.
# `[TARGET]` prefixes the seed's own heading (see serializers/markdown.py).
_CODE_SECTION_RE = re.compile(
    r"^###\s+(?:\[TARGET\]\s+)?(?P<symbol>\S+)\s+\((?P<label>[^)]+)\)\s*\n"
    r"```(?P<lang>[a-zA-Z0-9_+-]*)\n(?P<code>.*?)\n```",
    re.DOTALL | re.MULTILINE,
)

# The label now also carries the symbol's original line range and relative
# file path (e.g. "Full Implementation - L0 - lines 142-168 in
# django/contrib/sessions/base.py" - see serializers/markdown.py), so an
# exact-string dict lookup no longer works; extract the "L<n>" token from
# wherever it appears in the label instead.
_RESOLUTION_FROM_LABEL_RE = re.compile(r"\bL([0-3])\b")


def _resolution_from_label(label: str) -> int | None:
    match = _RESOLUTION_FROM_LABEL_RE.search(label)
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class CodeBlockValidity:
    symbol: str
    resolution: int | None
    resolution_label: str
    language: str
    valid: bool
    error: str | None = None


def extract_code_blocks(markdown_text: str) -> list[CodeBlockValidity]:
    """Locate every `### symbol (label)` + fenced-code-block pair in a
    rendered Prism Markdown package. Validity is filled in as `True`/`None`
    here for non-Python blocks (no applicable check); call
    `check_python_syntax` to actually validate the Python ones.
    """
    blocks: list[CodeBlockValidity] = []
    for match in _CODE_SECTION_RE.finditer(markdown_text):
        label = match.group("label")
        blocks.append(
            CodeBlockValidity(
                symbol=match.group("symbol"),
                resolution=_resolution_from_label(label),
                resolution_label=label,
                language=match.group("lang"),
                valid=True,
            )
        )
    return blocks


def check_python_syntax(markdown_text: str) -> list[CodeBlockValidity]:
    """Validate every Python-fenced code block in `markdown_text` with
    `ast.parse`. Returns one `CodeBlockValidity` per Python block found
    (non-Python blocks, e.g. JS/TS/Go fences, are skipped - there is no
    Python AST to check them against).
    """
    results: list[CodeBlockValidity] = []
    for match in _CODE_SECTION_RE.finditer(markdown_text):
        if match.group("lang") != "python":
            continue
        code = match.group("code")
        label = match.group("label")
        try:
            ast.parse(code)
        except SyntaxError as exc:
            results.append(
                CodeBlockValidity(
                    symbol=match.group("symbol"),
                    resolution=_resolution_from_label(label),
                    resolution_label=label,
                    language="python",
                    valid=False,
                    error=f"{exc.__class__.__name__}: {exc.msg} (line {exc.lineno})",
                )
            )
        else:
            results.append(
                CodeBlockValidity(
                    symbol=match.group("symbol"),
                    resolution=_resolution_from_label(label),
                    resolution_label=label,
                    language="python",
                    valid=True,
                )
            )
    return results


# A rendered block is either a free top-level function/type declaration
# (parses standalone, no wrapping needed) or a class member - a method,
# constructor, or (for Java/C#) an annotated/attributed declaration - which
# is only ever valid nested inside a class body. Java tolerates a bare
# top-level method/constructor regardless (confirmed empirically), but
# JS/TS/C# do not, and a *constructor* specifically is never standalone-
# valid at all in Java or C# either (its name must match its enclosing
# class - the grammar can't make that determination with no class in
# scope). Go is the one exception: its `func (r Receiver) Method()` syntax
# has no notion of a class body to begin with, so wrapping it in one would
# itself be a syntax error - Go blocks are always checked unwrapped only.
#
# Rather than guess which shape a given block is, both are tried: valid if
# EITHER the unwrapped or the class-wrapped reparse comes back clean. A
# genuinely broken block fails both.
_TRY_CLASS_WRAP_LANGUAGES = frozenset({"javascript", "typescript", "tsx", "java", "csharp"})

TREE_SITTER_CHECKED_LANGUAGES = frozenset({"javascript", "typescript", "tsx", "go", "java", "csharp"})


def _count_error_nodes(node) -> int:
    count = 1 if (node.type == "ERROR" or node.is_missing) else 0
    for child in node.children:
        count += _count_error_nodes(child)
    return count


def _tree_sitter_reparses_cleanly(language_id: str, code: str) -> tuple[bool, str | None]:
    from prism.parser.tree_sitter_loader import get_parser

    parser = get_parser(language_id)
    unwrapped_errors = _count_error_nodes(parser.parse(code.encode("utf-8")).root_node)
    if unwrapped_errors == 0:
        return True, None

    if language_id in _TRY_CLASS_WRAP_LANGUAGES:
        wrapped = f"class __PrismBenchmarkWrapper {{\n{code}\n}}"
        wrapped_errors = _count_error_nodes(parser.parse(wrapped.encode("utf-8")).root_node)
        if wrapped_errors == 0:
            return True, None

    return False, f"tree-sitter reparse produced {unwrapped_errors} ERROR/MISSING node(s) (unwrapped)"


def check_tree_sitter_syntax(
    markdown_text: str, languages: frozenset[str] = TREE_SITTER_CHECKED_LANGUAGES
) -> list[CodeBlockValidity]:
    """Validate every non-Python fenced code block in `markdown_text` (JS/TS/
    Go/Java/C#) via a real Tree-sitter reparse. Returns one
    `CodeBlockValidity` per matching block found; blocks in a language not
    in `languages` (Python included - use `check_python_syntax` for that)
    are skipped.
    """
    results: list[CodeBlockValidity] = []
    for match in _CODE_SECTION_RE.finditer(markdown_text):
        lang = match.group("lang")
        if lang not in languages:
            continue
        label = match.group("label")
        valid, error = _tree_sitter_reparses_cleanly(lang, match.group("code"))
        results.append(
            CodeBlockValidity(
                symbol=match.group("symbol"),
                resolution=_resolution_from_label(label),
                resolution_label=label,
                language=lang,
                valid=valid,
                error=error,
            )
        )
    return results
