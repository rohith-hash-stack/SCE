"""Tests for Item 21 (second post-implementation audit): Semantic Tags
vs. Topological Distance formalization -
`prism.slicer.semantic_topology_score`.

Covers the primitives (`topological_decay`, `jaccard_similarity`,
`semantic_topology_score`) directly, a hypothesis-driven proof that
distance strictly dominates the tag bonus whenever the closer candidate's
own hop distance is within this codebase's actual operating range
(`0..MAX_DOMINANT_HOP_DISTANCE`), and a direct, concrete demonstration of
the honest counterexample the module's own docstring derives (the
audit's literal "distance strictly dominates" claim is *not*
unconditionally true for its own alpha=0.20 constant - it fails, and
keeps failing, for every closer-candidate hop distance >= 10).
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from prism.slicer.distance import DEFAULT_MAX_HOPS
from prism.slicer.semantic_topology_score import (
    ALPHA,
    MAX_DOMINANT_HOP_DISTANCE,
    jaccard_similarity,
    semantic_topology_score,
    topological_decay,
)

_TAG_UNIVERSE = ["#auth_guard", "#db_write", "#db_read", "#external_io", "#entrypoint"]
_tag_set = st.sets(st.sampled_from(_TAG_UNIVERSE), max_size=len(_TAG_UNIVERSE))


# --------------------------------------------------------------------- #
# topological_decay
# --------------------------------------------------------------------- #
def test_topological_decay_at_zero_is_one():
    assert topological_decay(0) == 1.0


def test_topological_decay_is_strictly_decreasing():
    values = [topological_decay(d) for d in range(20)]
    assert all(values[i] > values[i + 1] for i in range(len(values) - 1))


def test_topological_decay_matches_the_literal_formula():
    for d in range(10):
        assert topological_decay(d) == 1.0 / (1.0 + d) ** 2


# --------------------------------------------------------------------- #
# jaccard_similarity
# --------------------------------------------------------------------- #
def test_jaccard_of_identical_nonempty_sets_is_one():
    assert jaccard_similarity({"#auth_guard", "#db_write"}, {"#auth_guard", "#db_write"}) == 1.0


def test_jaccard_of_disjoint_nonempty_sets_is_zero():
    assert jaccard_similarity({"#auth_guard"}, {"#db_write"}) == 0.0


def test_jaccard_of_both_empty_is_zero_not_one():
    """Deliberate: no tags is no signal, not a perfect match - see the
    module's own docstring for why this differs from the mathematically
    common Jaccard(empty, empty) = 1 convention."""
    assert jaccard_similarity(set(), set()) == 0.0


def test_jaccard_partial_overlap():
    assert jaccard_similarity({"#a", "#b", "#c"}, {"#b", "#c", "#d"}) == 2 / 4


def test_jaccard_is_always_in_unit_interval():
    for a in ({"#x"}, {"#x", "#y"}, set(), {"#a", "#b", "#c"}):
        for b in ({"#x"}, {"#y", "#z"}, set(), {"#b"}):
            value = jaccard_similarity(a, b)
            assert 0.0 <= value <= 1.0


# --------------------------------------------------------------------- #
# semantic_topology_score composition
# --------------------------------------------------------------------- #
def test_score_at_zero_hops_perfect_match_is_the_maximum_possible():
    score = semantic_topology_score(0, {"#auth_guard"}, {"#auth_guard"})
    assert score == 1.0 * (1.0 + ALPHA)


def test_score_bonus_never_exceeds_one_plus_alpha_multiple():
    for d in range(15):
        max_score = semantic_topology_score(d, {"#a"}, {"#a"})
        min_score_same_hop = semantic_topology_score(d, {"#a"}, {"#b"})
        assert max_score == topological_decay(d) * (1 + ALPHA)
        assert min_score_same_hop == topological_decay(d)
        assert max_score > min_score_same_hop


# --------------------------------------------------------------------- #
# The dominance proof (Item 21's own literal claim), verified precisely
# --------------------------------------------------------------------- #
def test_max_dominant_hop_distance_is_nine_for_alpha_point_two():
    """Exact, derived (not hand-copied) from the module's own docstring
    algebra: ((2+d)/(1+d))**2 > 1+alpha solved at alpha=0.20 gives
    d ~= 9.48, so the largest integer closer-candidate hop distance where
    dominance is guaranteed against every farther candidate is 9."""
    assert MAX_DOMINANT_HOP_DISTANCE == 9


@given(
    dist_u=st.integers(min_value=0, max_value=MAX_DOMINANT_HOP_DISTANCE),
    gap=st.integers(min_value=1, max_value=40),
    tags_s=_tag_set,
    tags_u=_tag_set,
    tags_v=_tag_set,
)
@settings(max_examples=300, deadline=None)
def test_distance_dominates_tag_bonus_when_the_closer_candidate_is_in_range(dist_u, gap, tags_s, tags_u, tags_v):
    """Whenever the *closer* candidate u's own hop distance is at most
    MAX_DOMINANT_HOP_DISTANCE, it outscores *any* farther candidate v
    (any gap, any tag overlap on either side) - the property the audit
    calls "distance strictly dominates tag bonus", proven here over its
    actually-valid domain rather than asserted unconditionally.
    """
    dist_v = dist_u + gap
    score_u = semantic_topology_score(dist_u, tags_s, tags_u)
    score_v = semantic_topology_score(dist_v, tags_s, tags_v)
    assert score_u > score_v, (
        f"dist_u={dist_u} dist_v={dist_v} gap={gap} tags_u={tags_u} tags_v={tags_v}: "
        f"score_u={score_u} !> score_v={score_v}"
    )


def test_dominance_range_covers_this_codebases_actual_hop_horizon():
    """`DistanceEngine`'s own `DEFAULT_MAX_HOPS` is the real horizon hop
    counts are normalized against everywhere in this codebase - every
    closer-candidate distance up to (but not including) that horizon must
    fall inside the proven dominance range for the formalization to be
    actually load-bearing here, independent of the abstract proof above.
    """
    assert MAX_DOMINANT_HOP_DISTANCE >= DEFAULT_MAX_HOPS - 1


def test_honest_counterexample_at_a_ten_hop_closer_distance_alpha_point_two():
    """The concrete falsification of the audit's own unconditional
    "distance strictly dominates" claim, at its own literal alpha=0.20 -
    a v eleven hops away with a perfect tag match outscores a u ten hops
    away with zero tag overlap, even though u is strictly closer.
    Documented, not hidden: see this test module's own docstring and
    `semantic_topology_score.py`'s docstring for the full derivation.
    """
    score_u_closer_no_tags = semantic_topology_score(10, {"#a"}, {"#b"})
    score_v_farther_perfect_tags = semantic_topology_score(11, {"#a"}, {"#a"})
    assert score_v_farther_perfect_tags > score_u_closer_no_tags


def test_the_failure_never_recovers_for_larger_closer_distances():
    """Once dominance fails at d=10, it fails for every larger d too -
    the ratio this is derived from is monotonically decreasing (proven in
    the module docstring), so there is no "recovery" further out."""
    for d in (10, 20, 50, 200):
        score_u_closer_no_tags = semantic_topology_score(d, {"#a"}, {"#b"})
        score_v_one_hop_farther_perfect_tags = semantic_topology_score(d + 1, {"#a"}, {"#a"})
        assert score_v_one_hop_farther_perfect_tags > score_u_closer_no_tags, f"unexpectedly held at d={d}"


def test_dominance_still_holds_at_the_nine_hop_boundary_case():
    score_u_closer_no_tags = semantic_topology_score(9, {"#a"}, {"#b"})
    score_v_farther_perfect_tags = semantic_topology_score(10, {"#a"}, {"#a"})
    assert score_u_closer_no_tags > score_v_farther_perfect_tags
