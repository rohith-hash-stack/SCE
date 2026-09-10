"""Type 1 (chain) scoring: does the LLM's response trace the exact
causal pipeline order in `G*_pipeline`? Pure text analysis - no LLM
judge, no API call - so this is fully unit-testable on its own.
"""
from __future__ import annotations


def _first_occurrence_index(text: str, symbol: str) -> int | None:
    """The earliest position `symbol` (or its bare simple name - an LLM
    response very rarely spells out a fully-qualified `pkg.mod.Class.
    method` path verbatim) appears in `text`, or `None` if neither form
    is mentioned at all."""
    simple_name = symbol.rsplit(".", 1)[-1]
    best: int | None = None
    for candidate in (symbol, simple_name):
        idx = text.find(candidate)
        if idx != -1 and (best is None or idx < best):
            best = idx
    return best


def score_chain(response_text: str, pipeline: list[str]) -> float:
    """`1.0` iff every stage of `pipeline` is mentioned in `response_text`
    *and* their first-mention order strictly matches `pipeline`'s own
    order; `0.0` otherwise (a stage never mentioned at all, or mentioned
    out of order). `1.0` (vacuously) for an empty pipeline.
    """
    if not pipeline:
        return 1.0

    indices: list[int] = []
    for symbol in pipeline:
        idx = _first_occurrence_index(response_text, symbol)
        if idx is None:
            return 0.0
        indices.append(idx)

    return 1.0 if all(indices[i] < indices[i + 1] for i in range(len(indices) - 1)) else 0.0
