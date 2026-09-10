"""Property-based (hypothesis) invariant tests for Prism v1.1's Causal
Coupling & Four-Axis Semantic Model - see `docs/design_formalism.md`
Section 8 for the formal statement of each property and
`prism.packer.submodular_knapsack`'s own module docstring for the
`beta * delta_max < 1.25` dominance proof Property 1 exercises.
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from prism.cli import build_pipeline
from prism.packer.submodular_knapsack import (
    DEFAULT_BETA,
    DEFAULT_DELTA_MAX,
    DOMINANCE_SAFETY_BOUND,
    MAX_DOMINANT_SEED_DISTANCE,
    compute_candidate_value,
    select_submodular_context,
)
from prism.traversal.causal_weights import causal_edge_weight
from prism.traversal.continuous_dijkstra import compute_topological_distances

_MASK_64 = st.integers(min_value=0, max_value=(1 << 64) - 1)


# --------------------------------------------------------------------- #
# Property 1: Absolute Hop Primacy
# --------------------------------------------------------------------- #
@given(
    dist_a=st.integers(min_value=0, max_value=MAX_DOMINANT_SEED_DISTANCE),
    mask_a=_MASK_64,
    mask_b=_MASK_64,
    covered=_MASK_64,
)
@settings(max_examples=300, deadline=None)
def test_property_1_one_hop_closer_dominates_within_the_proven_seed_distance_range(dist_a, mask_a, mask_b, covered):
    """The spec's own illustrative 1-hop-vs-2-hop case, proven over its
    actually-valid domain: whenever the *closer* candidate's own seed
    distance is within `MAX_DOMINANT_SEED_DISTANCE`, it outscores any
    candidate one hop farther, at zero novel features for the closer one
    and the maximum possible novel-feature boost for the farther one -
    the worst case for the claim."""
    dist_b = dist_a + 1
    value_a = compute_candidate_value(dist_a, mask_a & covered, covered, DEFAULT_BETA, DEFAULT_DELTA_MAX)  # zero novelty
    value_b = compute_candidate_value(dist_b, mask_b | ((1 << 64) - 1), covered, DEFAULT_BETA, DEFAULT_DELTA_MAX)  # max novelty
    assert value_a > value_b, f"dist_a={dist_a} dist_b={dist_b}: value_a={value_a} !> value_b={value_b}"


def test_max_dominant_seed_distance_is_one_at_default_constants():
    """Exact, derived (not hand-copied) from the module's own docstring
    algebra: ((2+d)/(1+d))**2 > 1+beta*delta_max solved at beta=0.10,
    delta_max=10 (threshold 2.0) gives d < sqrt(2)-1 ~= 0.414, so the
    largest integer closer-candidate distance where dominance is
    guaranteed is 1 - a real, narrow range, not the whole 0..6 operating
    horizon `DEFAULT_MAX_HOPS` might suggest."""
    assert MAX_DOMINANT_SEED_DISTANCE == 1


def test_honest_counterexample_at_seed_distance_two():
    """The concrete falsification of an *unconditional* dominance claim
    at the default constants: a 3-hop candidate with maximal novel
    coverage outscores a 2-hop candidate with none, even though the
    2-hop candidate is strictly closer to the seed. Documented, not
    hidden - see this module's own docstring and submodular_knapsack.py's
    own docstring for the full derivation.
    """
    value_closer_no_novelty = compute_candidate_value(2, 0, 0, DEFAULT_BETA, DEFAULT_DELTA_MAX)
    value_farther_max_novelty = compute_candidate_value(3, (1 << 64) - 1, 0, DEFAULT_BETA, DEFAULT_DELTA_MAX)
    assert value_farther_max_novelty > value_closer_no_novelty


def test_beta_delta_max_product_is_within_the_safety_bound():
    assert DEFAULT_BETA * DEFAULT_DELTA_MAX < DOMINANCE_SAFETY_BOUND


def test_select_submodular_context_rejects_an_unsafe_beta_delta_max():
    import networkx as nx

    g = nx.DiGraph()
    g.add_edge("seed", "a")
    try:
        select_submodular_context(g, "seed", 1000, {"a": 1.0}, {}, {"seed": 1, "a": 1}, beta=1.0, delta_max=10)
        assert False, "expected ValueError for beta*delta_max >= DOMINANCE_SAFETY_BOUND"
    except ValueError:
        pass


# --------------------------------------------------------------------- #
# Property 2: Submodularity of Bitmask Coverage
# --------------------------------------------------------------------- #
@given(
    dist=st.floats(min_value=0.0, max_value=10.0, allow_nan=False),
    candidate_mask=_MASK_64,
    s1=_MASK_64,
    extra=_MASK_64,
)
@settings(max_examples=300, deadline=None)
def test_property_2_submodularity_of_bitmask_coverage(dist, candidate_mask, s1, extra):
    """Diminishing returns: for any node `v` and any covered-sets
    `S1 subseteq S2` (`S2 = S1 | extra`), the marginal value of adding
    `v` to the *larger* already-covered set can never exceed its marginal
    value against the smaller one - popcount of `mask & ~(S1|extra)` is
    never more than popcount of `mask & ~S1` for any `extra`.
    """
    s2 = s1 | extra  # S1 subseteq S2 by construction
    value_against_s1 = compute_candidate_value(dist, candidate_mask, s1, DEFAULT_BETA, DEFAULT_DELTA_MAX)
    value_against_s2 = compute_candidate_value(dist, candidate_mask, s2, DEFAULT_BETA, DEFAULT_DELTA_MAX)
    assert value_against_s2 <= value_against_s1, (
        f"dist={dist} candidate_mask={candidate_mask:#x} s1={s1:#x} s2={s2:#x}: "
        f"value(s2)={value_against_s2} > value(s1)={value_against_s1}"
    )


@given(candidate_mask=_MASK_64, s1=_MASK_64, extra=_MASK_64)
@settings(max_examples=300, deadline=None)
def test_property_2_novel_bit_count_is_monotonically_non_increasing(candidate_mask, s1, extra):
    """The raw popcount claim underlying Property 2, isolated from the
    distance-decay/beta scaling around it."""
    s2 = s1 | extra
    novel_s1 = (candidate_mask & ~s1).bit_count()
    novel_s2 = (candidate_mask & ~s2).bit_count()
    assert novel_s2 <= novel_s1


# --------------------------------------------------------------------- #
# Property 3: Positive Distance Monotonicity
# --------------------------------------------------------------------- #
@given(
    data_flow_indicator=st.floats(min_value=0.01, max_value=1.0, allow_nan=False),
    guard_indicator=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
@settings(max_examples=200, deadline=None)
def test_property_3_data_flow_strictly_increases_weight_over_bare_base(data_flow_indicator, guard_indicator):
    """Adding data-flow evidence (`I_dataflow > 0`) must strictly increase
    `W(u, v)` over the bare `w_base` (`I_dataflow = I_guard = 0`) - and
    since `edge_cost = 1/W`, this means it strictly *reduces* the
    Dijkstra hop cost, i.e. `dist_w(u, v)` for a direct edge - real
    causal evidence must never make a path look *farther* than the plain
    structural relation alone.
    """
    from prism.traversal.causal_weights import edge_cost

    bare = causal_edge_weight("CALLS", 0.0, 0.0)
    with_evidence = causal_edge_weight("CALLS", data_flow_indicator, guard_indicator)
    assert with_evidence > bare
    assert edge_cost(with_evidence) < edge_cost(bare)


def test_property_3_data_flow_strictly_reduces_topological_distance(tmp_path):
    """The same property, exercised end to end: a synthetic causal edge
    (backed by real data-flow evidence) gives `store_order` a strictly
    shorter `dist_w` from `parse_order` than the bare structural distance
    (unreachable at all, in this fixture, since there's no direct
    structural edge between two siblings - the causal edge is the *only*
    path, so "strictly shorter" here means "finite instead of infinite/
    unreachable", the strongest possible case of the property)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(
        "def parse_order(raw):\n    return raw\n\n\n"
        "def store_order(data):\n    return data\n\n\n"
        "def process(raw):\n    data = parse_order(raw)\n    return store_order(data)\n"
    )
    builder, _ = build_pipeline(str(repo))
    distances = compute_topological_distances(builder, "x.parse_order")
    assert "x.store_order" in distances
    assert distances["x.store_order"] < float("inf")
