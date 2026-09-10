"""Blast-Radius Caller Capture Rate (BCCR) - what fraction of the real
return-binding callers a seed change would actually affect ended up in
the packed set `S_M`. `bccr_direct`/`bccr_transitive` are the same
"fraction captured" formula applied to two different ground-truth sets
(direct - 1-hop - vs. transitive - 2-hop - return-binding callers); which
set to compare against is the caller's own choice (human-annotated
ground truth, or `compute_direct_and_transitive_callers`' own
structurally-derived one below).
"""
from __future__ import annotations

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.packer.blast_radius import compute_upstream_callers


def bccr(selected: set[str], ground_truth_callers: set[str]) -> float:
    """`|S_M ∩ G*_callers| / |G*_callers|` - `1.0` (vacuously) if there
    are no ground-truth callers to capture at all."""
    if not ground_truth_callers:
        return 1.0
    return len(selected & ground_truth_callers) / len(ground_truth_callers)


def bccr_direct(selected: set[str], direct_callers: set[str]) -> float:
    return bccr(selected, direct_callers)


def bccr_transitive(selected: set[str], transitive_callers: set[str]) -> float:
    return bccr(selected, transitive_callers)


def compute_direct_and_transitive_callers(builder: ConcreteGraphBuilder, seed_symbol: str) -> tuple[set[str], set[str]]:
    """A real, structurally-derived (not human-annotated) ground-truth
    pair for `bccr_direct`/`bccr_transitive`, useful when a task has no
    human annotation to compare against: direct return-binding callers
    of `seed_symbol` (`prism.packer.blast_radius.compute_upstream_
    callers`, filtered to `unpacks_return`), and *their* own direct
    return-binding callers in turn (2-hop from the seed) - a caller two
    hops out counts only if it binds the return value of the hop-1
    caller it calls, the same "does this caller actually depend on the
    value, not just fire-and-forget" signal `blast_radius.py` already
    applies at 1 hop, generalized one hop further rather than redefined.
    """
    direct = compute_upstream_callers(builder, seed_symbol)
    direct_binding = {sym for sym, c in direct.items() if c.unpacks_return}

    transitive_binding: set[str] = set()
    for hop1_caller in direct_binding:
        hop2 = compute_upstream_callers(builder, hop1_caller)
        for sym, c in hop2.items():
            if c.unpacks_return and sym != seed_symbol and sym not in direct_binding:
                transitive_binding.add(sym)
    return direct_binding, transitive_binding
