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

#: Question words naming a physical resource -> capability target.
CAPABILITY_WORDS = {
    "database": "DATABASE_IO", "sql": "DATABASE_IO", "db": "DATABASE_IO",
    "file": "PRIM_FS_READ", "files": "PRIM_FS_READ", "disk": "PRIM_FS_READ", "directory": "PRIM_FS_READ",
    "socket": "PRIM_NET_CONNECT", "network": "PRIM_NET_CONNECT", "connection": "PRIM_NET_CONNECT",
    "subprocess": "PRIM_PROC_SPAWN", "process": "PRIM_PROC_SPAWN",
    "expire": "PRIM_TIME_CLOCK", "expired": "PRIM_TIME_CLOCK", "expiry": "PRIM_TIME_CLOCK", "timeout": "PRIM_TIME_CLOCK",
    "clock": "PRIM_TIME_CLOCK", "random": "PRIM_RAND_PRNG",
}


def rule_intent(question_text: str) -> dict | None:
    from prism.semantics.triad import PHYSICAL_ACTIONS, _words, action_score
    words = set(_words(question_text))
    intent = {}
    action = next((name for name in PHYSICAL_ACTIONS if action_score(words, name)), None)
    if action:
        intent["action"] = action
    capability = next((CAPABILITY_WORDS[w] for w in _words(question_text) if w in CAPABILITY_WORDS), None)
    if capability:
        intent["capability"] = capability
    return intent or None


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
