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

    **The granularity trap** (Track 3, `reports/spike_noise_reduction_
    debrief.md`'s Closing Note): this exact-match contract can't tell
    "more thorough than the adjudicated ground truth expected, but
    still substantively correct" from "wrong" - real, captured example,
    `django_t02_005_model_save_signals` @ budget=4000/seed=42 (`git show
    9af8941:benchmarks/experiments/results/spike_results.json` on
    `experiment/noise-filtering-spike`): the model's real answer named
    both true pipeline stages (`Model.save`, `Model.save_base`) plus two
    more real, legitimate downstream stages (`_save_parents`,
    `_save_table`) a richer two-pass manifest reasonably led it to
    include - this function scores that `0.0`, identically to a genuinely
    wrong answer. `score_debug_causal` below is the fix - kept as a
    separate function rather than a breaking change to this one, since
    single-turn/knapsack-driven callers (where "what's hydrated" and
    "what the model can correctly reason about" are the same thing by
    construction - the granularity trap is specific to two-pass
    hydration's richer manifest) have no `candidate_symbols` universe
    that would make containment scoring meaningfully different from
    exact-match here anyway.
    """
    try:
        extracted = extract_flat_symbols(response_text)
    except ParseError:
        return 0.0
    return 1.0 if [_normalize(s) for s in extracted] == [_normalize(s) for s in pipeline] else 0.0


def _ordered_subsequence_coverage(pipeline: list[str], extracted: list[str]) -> float:
    """`|longest run of `pipeline` matched, in order, against
    `extracted`| / len(pipeline)` - the standard greedy two-pointer "is A
    a subsequence of B" check, generalized to report how much of it was
    found rather than only whether all of it was. The full (score=1.0)
    case only needs the greedy single-pointer version of this check -
    correct and sufficient there - but partial credit needs the real
    longest-common-subsequence length between `pipeline` and
    `extracted`, not a greedy prefix match: a single missing *middle*
    stage (real example, `django_t02_009_queryset_filter_clone` @
    budget=2000 - the model's real answer has every pipeline stage but
    one, in the right relative order, with the one gap in the middle)
    would otherwise stall a greedy pointer at the first unmatched
    element and under-count everything correctly matched afterward -
    confirmed directly against that exact real response before this
    function's first version shipped. Standard O(n*m) DP; `pipeline` is
    always small (a handful of causal stages), so this is cheap.
    `1.0` (vacuously) for an empty pipeline."""
    if not pipeline:
        return 1.0
    n, m = len(pipeline), len(extracted)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if pipeline[i - 1] == extracted[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[n][m] / n


def score_debug_causal(response_text: str, pipeline: list[str], candidate_symbols: set[str]) -> float:
    """Causal sequence containment / subgraph match, Track 3's fix for
    `score_debug`'s own granularity trap (see that function's own
    docstring for the real, captured example this fixes).

    `1.0 iff` the response's `symbols` list contains the entire
    `pipeline` as an ordered (not necessarily contiguous) subsequence
    *and* every symbol named beyond the pipeline itself is a real,
    legitimately-available one - a member of `candidate_symbols` (the
    engine's own retrieved/hydrated candidate universe for this query,
    e.g. `selected_symbols(pkg)` or a two-pass `retrieve_two_pass`
    call's own candidate universe), never an invented name. Partial
    credit (`_ordered_subsequence_coverage`) for an incomplete or
    out-of-order pipeline, still gated at `0.0` by even one genuinely
    hallucinated (neither real pipeline stage nor real candidate)
    symbol - "more thorough than expected" is credited, "makes something
    up" is not, and the two are deliberately not conflated into one
    fuzzy score.

    Matching is by bare simple name throughout (`_normalize`, the same
    convention `score_debug`/`scorer_chain` already use - a model asked
    to name a symbol very rarely spells out its full qualified path
    verbatim), including for `candidate_symbols` membership.

    `0.0` for anything that doesn't parse as the expected flat object -
    same `ParseError`-catching contract as `score_debug`. `1.0`
    (vacuously) for an empty pipeline paired with a response naming only
    real candidates (or nothing at all); a response naming an invented
    symbol against an empty pipeline still scores `0.0` (any hallucination
    disqualifies, independent of pipeline coverage).
    """
    try:
        extracted_raw = extract_flat_symbols(response_text)
    except ParseError:
        return 0.0

    extracted = [_normalize(s) for s in extracted_raw]
    pipeline_n = [_normalize(s) for s in pipeline]
    pipeline_set = set(pipeline_n)
    candidates_n = {_normalize(s) for s in candidate_symbols}

    illegitimate = [s for s in extracted if s not in pipeline_set and s not in candidates_n]
    if illegitimate:
        return 0.0

    return _ordered_subsequence_coverage(pipeline_n, extracted)
