"""Type 5 (debug) scoring: does the LLM's response identify the exact
causal debug pipeline, in order, as a machine-parseable fenced JSON
array of symbol names - not free prose scored by a first-mention-order
heuristic (`scorer_chain.py`'s own approach), but a *structured* answer
format, so a T02 ("Django Debug") task can be scored deterministically:
no regex heuristics over prose, no second LLM call acting as judge.

Real, deterministic, code-based - pure text analysis, no API call.
"""
from __future__ import annotations

import json
import re

#: The first fenced code block (``` ```json``` or a bare ``` ```) whose
#: contents parse as a JSON array - deliberately permissive about the
#: language tag (a model asked for "a fenced JSON response" doesn't
#: always spell the tag exactly as ```json), strict about everything
#: after that: the fence must contain nothing but one `[...]` array.
_FENCED_BLOCK = re.compile(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", re.IGNORECASE)


def extract_fenced_symbol_list(response_text: str) -> list[str] | None:
    """The first fenced block's contents, parsed as a JSON array of
    strings - or `None` if no fenced block is present at all, or the
    first one present doesn't parse as a JSON array of strings (a real
    "the model didn't follow the requested format" failure, scored
    `0.0` by `score_debug`, never raised).
    """
    match = _FENCED_BLOCK.search(response_text)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        return None
    return parsed


def _normalize(symbol: str) -> str:
    """The bare simple name (matching `scorer_chain`'s own convention -
    a model asked to name a symbol very rarely spells out its full
    `pkg.mod.Class.method` qualified path verbatim)."""
    return symbol.rsplit(".", 1)[-1]


def score_debug(response_text: str, pipeline: list[str]) -> float:
    """`1.0` iff the fenced JSON array's symbols - by full qualified name
    or bare simple name - equal `pipeline` exactly, in the same order;
    `0.0` for a wrong/missing/extra/reordered entry, or a response with
    no valid fenced JSON array at all. `1.0` (vacuously) for an empty
    pipeline paired with an explicit empty array `[]`.
    """
    extracted = extract_fenced_symbol_list(response_text)
    if extracted is None:
        return 0.0
    return 1.0 if [_normalize(s) for s in extracted] == [_normalize(s) for s in pipeline] else 0.0
