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

_RESOLUTION_FROM_LABEL = {
    "Full Implementation - L0": 0,
    "Control Skeleton - L1": 1,
    "Contract - L2": 2,
    "Alias - L3": 3,
}


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
                resolution=_RESOLUTION_FROM_LABEL.get(label),
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
                    resolution=_RESOLUTION_FROM_LABEL.get(label),
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
                    resolution=_RESOLUTION_FROM_LABEL.get(label),
                    resolution_label=label,
                    language="python",
                    valid=True,
                )
            )
    return results
