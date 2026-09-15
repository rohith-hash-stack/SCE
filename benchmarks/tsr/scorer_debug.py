"""Type 5 (debug) scoring: does the LLM's response identify the exact
causal debug pipeline, in order, as a flat JSON object -
`{"reasoning": "...", "symbols": ["a.b", "a.c", ...]}` - not free prose
scored by a first-mention-order heuristic (`scorer_chain.py`'s own
approach), and not the earlier nested `{"reasoning": ..., "pipeline":
[{"symbol": ..., "evidence": ...}]}` shape (retired - see git history:
Qwen 2.5 7B Instruct Q8_0, the Ollama SLM used for local format-
compliance testing, passed 10/10 on the flat shape but failed at Q4 on
the nested one; `evidence` was never consumed by anything downstream
either way). A T02 task is scored deterministically either way: no
regex heuristics over prose, no second LLM call acting as judge.

Real, deterministic, code-based - pure text analysis, no API call, no
side effects (the caller, `benchmarks.runner.run_evaluation`, is
responsible for logging a raw response that fails to parse - see its
own parse-failure log line).
"""
from __future__ import annotations

import json
import re

#: The first fenced code block (``` ```json``` or a bare ``` ```)
#: present, if any - deliberately permissive about the language tag (a
#: model asked for "a fenced JSON response" doesn't always spell the
#: tag exactly as ```json``). If no fence is present at all, the raw
#: text itself is tried as JSON directly - some models return bare JSON
#: despite being asked to fence it.
_FENCED_BLOCK = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


class ParseError(Exception):
    """A response doesn't parse as the flat `{"reasoning": ...,
    "symbols": [...]}` contract - including the retired nested
    `{"pipeline": [...]}` shape, which is deliberately rejected rather
    than silently accepted, partially parsed, or misread as a
    different field name."""


def _strip_code_fence(response_text: str) -> str:
    match = _FENCED_BLOCK.search(response_text)
    return match.group(1) if match else response_text


def extract_flat_symbols(response_text: str) -> list[str]:
    """The ordered `symbols` list from a `{"reasoning": ..., "symbols":
    [...]}` response - after stripping a markdown code fence, if
    present, falling back to the raw text if none is found (some models
    return bare JSON despite being asked to fence it).

    Raises `ParseError` - never a bare `json.JSONDecodeError`/
    `KeyError`/`TypeError` - for anything that isn't exactly this
    shape: invalid JSON, a non-object root, no `"symbols"` key (the
    retired `{"pipeline": [...]}` shape lands here - a direct `obj
    ["symbols"]` would raise a bare `KeyError` for it, not `ParseError`,
    so the key access is guarded explicitly), `"symbols"` not a list,
    or any entry in it not a string.
    """
    candidate = _strip_code_fence(response_text.strip())
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ParseError(f"response is not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ParseError(f"response is not a JSON object (got {type(obj).__name__})")
    try:
        symbols = obj["symbols"]
    except KeyError as exc:
        raise ParseError('response has no "symbols" key') from exc
    if not isinstance(symbols, list):
        raise ParseError(f'"symbols" is not a list (got {type(symbols).__name__})')
    if not all(isinstance(s, str) for s in symbols):
        raise ParseError('"symbols" contains a non-string entry')
    return symbols


def _normalize(symbol: str) -> str:
    """The bare simple name (matching `scorer_chain`'s own convention -
    a model asked to name a symbol very rarely spells out its full
    `pkg.mod.Class.method` qualified path verbatim)."""
    return symbol.rsplit(".", 1)[-1]


def score_debug(response_text: str, pipeline: list[str]) -> float:
    """`1.0` iff the response's `symbols` list - by full qualified name
    or bare simple name - equals `pipeline` exactly, in the same order;
    `0.0` for a wrong/missing/extra/reordered entry, or a response that
    doesn't parse as the expected flat object at all (a caught
    `ParseError` from `extract_flat_symbols` - this function itself
    never raises, "malformed input scores 0.0" is its whole contract).
    `1.0` (vacuously) for an empty pipeline paired with an explicit
    empty `"symbols": []`.
    """
    try:
        extracted = extract_flat_symbols(response_text)
    except ParseError:
        return 0.0
    return 1.0 if [_normalize(s) for s in extracted] == [_normalize(s) for s in pipeline] else 0.0
