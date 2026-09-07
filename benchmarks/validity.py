"""Syntactic validity checks (HLD evaluation dimension 3): every Python code
block SCE renders into its Markdown context package must still parse. This
is a direct, black-box check against the rendered Markdown text itself (not
against internal compressor state), so it also catches any bug the
Markdown serializer might introduce independent of the compressor.
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
    rendered SCE Markdown package. Validity is filled in as `True`/`None`
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
