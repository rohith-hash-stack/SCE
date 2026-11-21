"""Turn-1 triad on the NLQ lexical candidate search: a reproducible
measurement of what the action and capability legs do to subject ranks.

`rule_intent` is a deterministic stand-in for a model's Turn-1 retrieval
intent - NOT a gold annotation and never derived from gold: the action is
the first physical action whose verbs occur in the question text (the
triad's own lexicon), the capability is set only when the question names a
physical resource (`CAPABILITY_WORDS`). Questions it yields no intent for
rank exactly as plain BM25.
"""
from __future__ import annotations

import time

from harness.experiments.query_to_evidence.nlq.lexical import VARIANTS, LexicalIndex
from harness.experiments.query_to_evidence.nlq.lexical_eval import gold_targets

from prism.query.nlq import CAPABILITY_WORDS, rule_intent  # noqa: E402,F401 - moved to the product


def compare_question(ix: LexicalIndex, q: dict, g: dict, intent: dict | None) -> dict:
    """Gold subject ranks, plain BM25 vs triad, per variant, with timing."""
    targets, interps = gold_targets(g)
    row = {"intent": intent, "targets": targets, "variants": {}}
    for v in VARIANTS:
        plain = ix.ranked(q["query"], v)
        t = time.perf_counter()
        triad = ix.ranked(q["query"], v, intent) if intent else plain
        elapsed = time.perf_counter() - t
        pos_p = {n: r + 1 for r, n in enumerate(plain)}
        pos_t = {n: r + 1 for r, n in enumerate(triad)}
        row["variants"][v] = {
            "rank_plain": {t_: pos_p.get(t_) for t_ in targets},
            "rank_triad": {t_: pos_t.get(t_) for t_ in targets},
            "interp_best_plain": [min((pos_p[s] for s in grp if s in pos_p), default=None) for grp in interps],
            "interp_best_triad": [min((pos_t[s] for s in grp if s in pos_t), default=None) for grp in interps],
            "triad_ms": round(elapsed * 1000, 2),
        }
    return row
