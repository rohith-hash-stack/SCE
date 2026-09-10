"""Type 3 (redundancy) scoring: does the LLM isolate the seed's own
behavior without conflating it with a near-duplicate sibling (one of
`orthogonal_neighbors`)? A real, deterministic, code-based ("programmatic
rubric" per the spec - not a second LLM call acting as judge) heuristic:
pure text analysis, no API call.
"""
from __future__ import annotations

import re

#: Phrases that assert the seed and a sibling are the same/interchangeable -
#: exactly the conflation this task type exists to catch. Deliberately a
#: curated, conservative list (false negatives - a real conflation phrased
#: unusually - are less harmful to this benchmark than false positives -
#: flagging ordinary prose as conflation).
_CONFLATION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (r"\bsame as\b", r"\bidentical to\b", r"\bduplicate of\b", r"\bequivalent to\b", r"\bno different from\b", r"\binterchangeable with\b")
]


def _mentions(text: str, symbol: str) -> bool:
    simple_name = symbol.rsplit(".", 1)[-1]
    return symbol in text or simple_name in text


def rubric_score(response_text: str, seed_symbol: str, orthogonal_neighbors: set[str]) -> int:
    """+1 for actually discussing the seed by name; -1 if the response
    also mentions a sibling *and* uses a conflation phrase anywhere (a
    real, if necessarily heuristic, "did it blur the seed together with
    a near-duplicate" signal). Not clamped to `[0, 1]` - `score_
    redundancy` is what binarizes it.
    """
    score = 0
    if _mentions(response_text, seed_symbol):
        score += 1

    mentions_a_sibling = any(_mentions(response_text, neighbor) for neighbor in orthogonal_neighbors)
    if mentions_a_sibling and any(pattern.search(response_text) for pattern in _CONFLATION_PATTERNS):
        score -= 1

    return score


def score_redundancy(response_text: str, seed_symbol: str, orthogonal_neighbors: set[str]) -> float:
    """`1.0` iff `rubric_score >= 1`, else `0.0` - the spec's own binary
    threshold."""
    return 1.0 if rubric_score(response_text, seed_symbol, orthogonal_neighbors) >= 1 else 0.0
