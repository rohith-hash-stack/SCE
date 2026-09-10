"""Type 2 (blast) scoring: does the LLM correctly identify exactly the
set of callers affected by a seed change - no more, no less
(Precision/Recall/F1 == 1.0 against `G*_callers`)? Pure text analysis.
"""
from __future__ import annotations


def _mentions(text: str, symbol: str) -> bool:
    simple_name = symbol.rsplit(".", 1)[-1]
    return symbol in text or simple_name in text


def extract_mentioned_symbols(response_text: str, candidate_symbols: set[str]) -> set[str]:
    """Every symbol from `candidate_symbols` (the real universe of names
    that could plausibly have been mentioned - typically every symbol
    the packed context exposed to the LLM) that `response_text` actually
    names, by its fully-qualified name or its bare simple name."""
    return {symbol for symbol in candidate_symbols if _mentions(response_text, symbol)}


def precision_recall_f1(predicted: set[str], ground_truth: set[str]) -> tuple[float, float, float]:
    """`(1.0, 1.0, 1.0)` when both are empty (correctly identifying "no
    callers affected" when there genuinely are none); `(0.0, 0.0, 0.0)`
    when exactly one is empty (a real miss in either direction)."""
    if not predicted and not ground_truth:
        return 1.0, 1.0, 1.0
    if not predicted or not ground_truth:
        return 0.0, 0.0, 0.0
    true_positives = len(predicted & ground_truth)
    precision = true_positives / len(predicted)
    recall = true_positives / len(ground_truth)
    f1 = 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)
    return precision, recall, f1


def score_blast(response_text: str, candidate_symbols: set[str], ground_truth_callers: set[str]) -> float:
    """`1.0` iff the LLM's extracted caller set has `F1 == 1.0` against
    `G*_callers` (exact match, not partial credit - Type 2's own binary
    scoring rule), else `0.0`."""
    predicted = extract_mentioned_symbols(response_text, candidate_symbols)
    _precision, _recall, f1 = precision_recall_f1(predicted, ground_truth_callers)
    return 1.0 if f1 == 1.0 else 0.0
