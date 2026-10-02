"""Cross-engine fairness checks.

Rule 2: every DeliveredContext's items are ranked 1..n, consecutive, with no
gaps and no duplicates. `score()` runs `verify_ranking` on every context.
An arm with no meaningful order ranks alphabetically (`rank_alphabetically`)
and records `build_meta["ranking_method"] = "alphabetical"`.

Rule 3: `iso_token_slice` gives the largest rank-order prefix that fits a
smaller budget, for iso-token comparisons between arms that fill the budget
very differently.
"""
from __future__ import annotations

from dataclasses import replace

from harness.scoring.canonical import DeliveredItem


class RankingViolation(AssertionError):
    pass


def verify_ranking(items: list[DeliveredItem]) -> None:
    """Assert ranks are exactly 1..n."""
    ranks = sorted(it.rank for it in items)
    if ranks != list(range(1, len(items) + 1)):
        raise RankingViolation(f"ranking violated: {ranks}")


def rerank_consecutive(items: list[DeliveredItem]) -> list[DeliveredItem]:
    """Copies of `items`, in their current rank order, renumbered 1..n
    (after an arm drops items, e.g. for budget)."""
    ordered = sorted(items, key=lambda it: it.rank)
    return [replace(it, rank=i) for i, it in enumerate(ordered, start=1)]


def rank_alphabetically(items: list[DeliveredItem], build_meta: dict) -> list[DeliveredItem]:
    ordered = sorted(items, key=lambda it: it.source_id)
    build_meta["ranking_method"] = "alphabetical"
    return [replace(it, rank=i) for i, it in enumerate(ordered, start=1)]


def iso_token_slice(items: list[DeliveredItem], budget: int, tokenizer=None) -> list[DeliveredItem]:
    """Largest prefix, in rank order, whose cumulative tokens <= budget.
    Uses each item's recorded `token_count`; with `tokenizer`, recounts the
    content instead."""
    out, total = [], 0
    for it in sorted(items, key=lambda x: x.rank):
        cost = tokenizer.count(it.content) if tokenizer is not None else it.token_count
        if total + cost > budget:
            break
        out.append(it)
        total += cost
    return out
