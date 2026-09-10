"""Item 21 (second post-implementation audit): Semantic Tags vs.
Topological Distance formalization.

The audit's own literal specification asks for this exact scoring
function to be formalized and proven:

    Score(v|s) = TopologicalDecay(dist(s, v)) * (1 + alpha * JaccardSimilarity(tags(s), tags(v)))
    TopologicalDecay(d) = 1 / (1 + d)**2
    alpha = 0.20

This is a *companion formalization*, not a replacement for the distance
metric `prism.slicer.distance.DistanceEngine` actually uses to rank
candidates (`D_hybrid`, Section 2.1 of `docs/design_formalism.md`) - that
metric already has its own proven Topological Monotonicity invariant
(Invariant #1), backed by a real regression fix (an additive tag term at
full peer weight once outranked a genuine 1-hop callee against a 4-hop
same-tagged sibling - see `DistanceEngine`'s own module docstring), and
swapping the live ranking function for this multiplicative form is a
materially different, much larger change than "formalize this scoring
function" asks for. What follows is that formalization, proven rigorously
- honestly, including where the audit's own literal constants do *not*
give the unconditional dominance property it describes.

**The dominance claim, precisely stated and proven**: for any two
candidates u, v with dist(u) < dist(v), does Score(u) > Score(v) hold for
*every* possible tag overlap - i.e., does one extra hop always cost more
than the maximum possible tag-similarity bonus can ever buy back? The
worst case for this claim, at a fixed dist(u) = d, is u at zero tag
similarity (bonus factor 1, the minimum) against a v at perfect tag
similarity (bonus factor `1 + alpha`, the maximum); among every possible
dist(v) > d, the hardest (smallest-margin) case is the closest one,
dist(v) = d + 1 - TopologicalDecay is strictly decreasing, so any *larger*
gap only shrinks Score(v) further and makes dominance easier, never
harder. So the whole claim, at a fixed d, reduces to whether

    TopologicalDecay(d) > TopologicalDecay(d + 1) * (1 + alpha)   ... (*)

Substituting TopologicalDecay(d) = 1/(1+d)**2 and simplifying:

    ((2+d)/(1+d))**2 > 1 + alpha

The left side is a strictly *decreasing* function of d that approaches 1
as d -> infinity (a polynomial 1/(1+d)**2 decay eventually loses to *any*
fixed multiplicative bonus > 1, for large enough d - the tag term isn't
scaled down by the hop horizon the way `D_hybrid`'s additive
`tag_bonus_safety_margin` deliberately is). Because the left side is
monotonically decreasing in d, once (*) fails at some d it fails for
every larger d too - there is no "recovery". At alpha = 0.20, solving
((2+d)/(1+d))**2 = 1.20 gives d ~= 9.48, so (*) holds for every integer d
in 0..9 and fails starting at d = 10 (checkable exactly:
TopologicalDecay(10) = 1/121 ~= 0.008264, while
TopologicalDecay(11)*1.2 = 1.2/144 ~= 0.008333 - a v eleven hops away
with a perfect tag match edges out a u ten hops away with zero tag
overlap, and the same failure recurs for every d >= 10). **So the
audit's own "distance strictly dominates" claim, taken as an
unconditional statement over all d, is false for its own literal
constants** - a real, checkable fact this module surfaces rather than
asserts away.

What *does* hold, and is the property this codebase actually needs:
whenever the *closer* candidate's own hop distance is at most
`MAX_DOMINANT_HOP_DISTANCE` (9, derived from `ALPHA` below, not hand-
copied), it dominates *every* farther candidate regardless of either
one's tags - and that covers every comparison
`prism.slicer.distance.DistanceEngine` would ever actually need, since
`DEFAULT_MAX_HOPS = 10` is the same horizon hop counts are normalized
against everywhere else in this codebase. See
`tests/test_semantic_topology_score.py` for both the hypothesis-driven
proof over that in-range domain and a direct, concrete demonstration of
the d=10/d=11 counterexample above.
"""
from __future__ import annotations

#: The audit's own literal constant.
ALPHA = 0.20


def _compute_max_dominant_hop_distance(alpha: float) -> int:
    """The largest integer `d` for which `(*)` in this module's own
    docstring - `((2+d)/(1+d))**2 > 1 + alpha` - still holds. Computed
    once from `alpha` (not hand-copied), so a future change to `ALPHA`
    keeps `MAX_DOMINANT_HOP_DISTANCE` honest automatically. Safe to search
    upward one integer at a time: the left side is monotonically
    decreasing in `d` (proven in the module docstring), so the first `d`
    where it fails is the only crossing point there ever is.
    """
    d = 0
    while ((2 + d) / (1 + d)) ** 2 > 1 + alpha:
        d += 1
    return d - 1


#: See this module's own docstring for the full derivation - the largest
#: hop distance a "closer" candidate can be at and still be guaranteed to
#: outscore *any* farther candidate, regardless of tag overlap on either
#: side, at `alpha = ALPHA`.
MAX_DOMINANT_HOP_DISTANCE = _compute_max_dominant_hop_distance(ALPHA)


def topological_decay(dist: float) -> float:
    """`TopologicalDecay(d) = 1 / (1 + d)**2` - strictly decreasing,
    `TopologicalDecay(0) = 1.0`, approaching 0 as `dist` grows without
    bound. `dist` is a topological hop count and is never negative in any
    real caller, but this doesn't itself assert that - it's a pure
    function of whatever `dist` it's given.
    """
    return 1.0 / (1.0 + dist) ** 2


def jaccard_similarity(tags_a: set[str], tags_b: set[str]) -> float:
    """`|A ∩ B| / |A ∪ B|`, in `[0.0, 1.0]`. Two disjoint non-empty sets
    score 0.0 (no similarity); two identical non-empty sets score 1.0
    (perfect similarity). **Both empty is defined as 0.0, not 1.0** -
    deliberately: an untagged node has no similarity *signal* to offer,
    which should be neutral (no bonus), not treated as if it perfectly
    matched the seed. This mirrors `prism.graph.metamodel.
    UNTAGGED_TAG_DISTANCE`'s own established principle in this codebase -
    "no signal" and "confirmed match" are kept distinct - rather than the
    mathematically-common-but-wrong-here convention of defining
    Jaccard(empty, empty) = 1.
    """
    if not tags_a and not tags_b:
        return 0.0
    intersection = len(tags_a & tags_b)
    union = len(tags_a | tags_b)
    return intersection / union


def semantic_topology_score(dist: float, tags_a: set[str], tags_b: set[str], alpha: float = ALPHA) -> float:
    """`Score(v|s) = TopologicalDecay(dist) * (1 + alpha * JaccardSimilarity(tags_a, tags_b))` -
    the audit's own literal formula, `alpha` defaulting to its own
    literal constant (`ALPHA = 0.20`).
    """
    return topological_decay(dist) * (1.0 + alpha * jaccard_similarity(tags_a, tags_b))
