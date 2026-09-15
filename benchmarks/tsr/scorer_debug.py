"""Type 5 (debug) scoring: does the LLM's response identify the exact
causal debug pipeline, in order, as a single structured JSON object -
not free prose scored by a first-mention-order heuristic
(`scorer_chain.py`'s own approach), and not a bare JSON array in a
fenced block (the pre-`fix-parser` contract - see git history), but
`{"reasoning": "...", "pipeline": [{"symbol": "...", "evidence": "..."},
...]}`, matching the prompt contract `benchmarks.runner.
DEBUG_TASK_RESPONSE_CONTRACT` requests. A T02 task is scored
deterministically either way: no regex heuristics over prose, no
second LLM call acting as judge.

Real, deterministic, code-based - pure text analysis, no API call, no
side effects (the caller, `benchmarks.runner.run_evaluation`, is
responsible for logging a raw response that fails to parse - see its
own parse-failure log line - this module stays a pure function so it
can be unit-tested without capturing stderr).
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


def _strip_code_fence(response_text: str) -> str:
    match = _FENCED_BLOCK.search(response_text)
    return match.group(1) if match else response_text


def extract_structured_pipeline(response_text: str) -> list[str] | None:
    """The ordered list of `symbol` strings from a `{"reasoning": ...,
    "pipeline": [{"symbol": ..., "evidence": ...}, ...]}` response - or
    `None` if the response (after stripping a markdown code fence, if
    present) doesn't parse as JSON at all, doesn't parse as an object,
    has no `"pipeline"` key, `"pipeline"` isn't a list, or any entry in
    it isn't an object with a string `"symbol"` field. A real "the model
    didn't follow the requested format" failure, scored `0.0` by
    `score_debug`, never raised.
    """
    candidate = _strip_code_fence(response_text.strip())
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    pipeline = parsed.get("pipeline")
    if not isinstance(pipeline, list):
        return None
    symbols: list[str] = []
    for entry in pipeline:
        if not isinstance(entry, dict) or not isinstance(entry.get("symbol"), str):
            return None
        symbols.append(entry["symbol"])
    return symbols


def _normalize(symbol: str) -> str:
    """The bare simple name (matching `scorer_chain`'s own convention -
    a model asked to name a symbol very rarely spells out its full
    `pkg.mod.Class.method` qualified path verbatim)."""
    return symbol.rsplit(".", 1)[-1]


def score_debug(response_text: str, pipeline: list[str]) -> float:
    """`1.0` iff the structured response's `pipeline` symbols - by full
    qualified name or bare simple name - equal `pipeline` exactly, in
    the same order; `0.0` for a wrong/missing/extra/reordered entry, or
    a response that doesn't parse as the expected structured object at
    all. `1.0` (vacuously) for an empty pipeline paired with an
    explicit empty `"pipeline": []`.
    """
    extracted = extract_structured_pipeline(response_text)
    if extracted is None:
        return 0.0
    return 1.0 if [_normalize(s) for s in extracted] == [_normalize(s) for s in pipeline] else 0.0
