"""Graph edge weight reference table and the theoretical tentative-edge
dominance derivation (G44).

**This module is a reference/target derivation, not the live cost model.**
The Dijkstra traversal actually run today (`prism.slicer.distance.
DistanceEngine._weighted_undirected`) prices edges from its own
`RELATION_STRUCTURAL_WEIGHT` dict and discounts a `kind="TENTATIVE_CALL"`
edge by multiplying that structural weight by `RELATION_TENTATIVE_CALL_WEIGHT
= 0.60` (cost ~= 1/0.60 ~= 1.67) - a *relative* discount on top of whatever
base relation weight applies, not the *absolute* floor-value model this
module documents. `EDGE_WEIGHTS`/`W_TENTATIVE` below are not imported by
`prism.slicer.distance` and do not change what a real Dijkstra run prices
any edge at; nothing in `prism.graph.concrete_builder`'s G44 fallback reads
`W_TENTATIVE` either - it emits `kind="TENTATIVE_CALL"` and lets the
existing, already-tested `0.60` multiplier price it (see that module's own
G44 commit for why: a `kind` unrecognized by `distance.py` would silently
default to full CALLS-level (1.0) confidence, which is the exact failure
this reference table's own dominance property exists to prevent).

**Why this table still exists, unwired**: `RELATION_STRUCTURAL_WEIGHT`
mirrors these same relation names (`CALLS`, `EXTENDS`, `IMPLEMENTS`, ...)
with its own values (e.g. `EXTENDS: 0.85`, not `1.0`) tuned against real
corpus behavior; this module is the target algebraic derivation - "what
would a tentative edge need to cost to strictly dominate any structural
path up to `MAX_INHERITANCE_DEPTH` hops" - kept as its own reference so a
future traversal-layer refactor (Phase C) has the real math already
worked out, without this phase silently overwriting a tuned, tested
constant it has no authority to change.
"""

# Reference relation weights for the dominance derivation below. Does NOT
# mirror `prism.slicer.distance.RELATION_STRUCTURAL_WEIGHT` verbatim - that
# table is the real, tuned, already-tested one; this one adds two relations
# (`CONTAINS` for data-flow container edges, `AMBIGUOUS_CHAIN` for G41's
# branching-assignment case) that don't exist there yet, and uses `EXTENDS
# = 1.0` (full confidence) rather than that table's tuned `0.85`, since the
# dominance property only needs the *weakest* relation's cost (IMPLEMENTS,
# 0.8 in both tables) to derive `MAX_EDGE_COST` - the exact EXTENDS value
# doesn't move that maximum either way.
EDGE_WEIGHTS: dict[str, float] = {
    "CALLS": 1.0,
    "EXTENDS": 1.0,
    "CONTAINS": 1.0,
    "IMPLEMENTS": 0.8,
    "AMBIGUOUS_CHAIN": 1.5,
}

MAX_INHERITANCE_DEPTH: int = 10

# Max edge cost derived from the edge weights (cost = 1.0 / weight) -
# 1.25, from IMPLEMENTS (0.8), the weakest (most expensive) relation above.
MAX_EDGE_COST: float = max(1.0 / w for w in EDGE_WEIGHTS.values())

SAFETY_MARGIN: float = 1.20

# The theoretical dominance target: a tentative edge priced at this
# absolute cost would strictly exceed any structural path up to
# MAX_INHERITANCE_DEPTH hops (10 * 1.25 = 12.5 worst case), with a 20%
# safety margin on top. Not consumed by any traversal code today - see
# module docstring.
W_TENTATIVE: float = round(MAX_INHERITANCE_DEPTH * MAX_EDGE_COST * SAFETY_MARGIN, 2)
