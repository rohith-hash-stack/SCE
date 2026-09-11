"""Type 4 (architecture) scoring: does the LLM's response correctly
identify the structural/architectural symbols a domain expert marked as
relevant (`reference_symbols`) when asked to describe a seed's role in
the codebase's structure? A real, deterministic, code-based ("rubric" -
not a second LLM call acting as judge) heuristic: pure text analysis, no
API call, no sandboxed execution of any kind.

**Scope note**: T14 ("Architecture/Structure Analysis") is scored here
as a reference-symbol identification rubric, not as behavior-preserving
transformation graded by running a real test suite against a generated
diff - that second interpretation would require sandboxed, per-repo,
per-language code execution (dependency installation, isolation,
resource/timeout limits, a security review) that is a materially larger,
separate-scope effort this module deliberately does not build.
"""
from __future__ import annotations

#: Recall, not F1: unlike Type 2 (blast), naming *extra* real structural
#: symbols beyond the reference set is not a false positive here - a
#: thorough architecture description naturally mentions more context
#: than the minimal reference set, so only "did it identify the symbols
#: that actually matter" is scored, not "did it avoid naming anything
#: else". `0.60` mirrors this harness's own adjudication-tier threshold
#: (`ground_truth.schema.KAPPA_ADJUDICATION_THRESHOLD`) as a consistent
#: "clearly more right than wrong" bar, not a mathematically derived one.
ARCHITECTURE_RECALL_THRESHOLD = 0.60


def _mentions(text: str, symbol: str) -> bool:
    simple_name = symbol.rsplit(".", 1)[-1]
    return symbol in text or simple_name in text


def extract_mentioned_symbols(response_text: str, reference_symbols: set[str]) -> set[str]:
    """Every symbol from `reference_symbols` that `response_text` actually
    names, by its fully-qualified name or its bare simple name."""
    return {symbol for symbol in reference_symbols if _mentions(response_text, symbol)}


def reference_symbol_recall(response_text: str, reference_symbols: set[str]) -> float:
    """`|mentioned ∩ reference| / |reference|` - `1.0` (vacuously) for an
    empty reference set."""
    if not reference_symbols:
        return 1.0
    mentioned = extract_mentioned_symbols(response_text, reference_symbols)
    return len(mentioned) / len(reference_symbols)


def score_architecture(response_text: str, reference_symbols: set[str]) -> float:
    """`1.0` iff `reference_symbol_recall >= ARCHITECTURE_RECALL_THRESHOLD`,
    else `0.0` - this task type's own binary threshold."""
    return 1.0 if reference_symbol_recall(response_text, reference_symbols) >= ARCHITECTURE_RECALL_THRESHOLD else 0.0
