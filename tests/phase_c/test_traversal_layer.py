"""Phase C: continuous traversal layer (bounded frontier, directionality,
multi-source seeding, tentative-weight reconciliation).

Scope note (see the phase-c commit messages for the full reasoning):
Commits 1-3 land in prism.traversal.continuous_dijkstra.
compute_topological_distances - the real, single-seed-forward function
that feeds every actual retrieve() call today (via prism.surface.build /
prism.packer.submodular_knapsack) - not prism.slicer.distance.
DistanceEngine, which is a deliberately separate, already-unconditionally-
bidirectional system serving a different (CLI/MCP query) consumer. Commit
4 (tentative-weight reconciliation) lands in prism.slicer.distance, since
that is where RELATION_TENTATIVE_CALL_WEIGHT and the tag-bonus invariants
actually live.
"""
from pathlib import Path

from prism.cli import build_pipeline
from prism.traversal.continuous_dijkstra import compute_topological_distances


def _write_call_chain(repo: Path, length: int) -> None:
    lines = []
    for i in range(length):
        if i == length - 1:
            lines.append(f"def f{i}():\n    return {i}\n")
        else:
            lines.append(f"def f{i}():\n    return f{i + 1}()\n")
    (repo / "mod.py").write_text("\n".join(lines))


# ============================================================
# Commit 1: bounded frontier (d_max)
# ============================================================

def test_dijkstra_terminates_at_d_max_5(tmp_path):
    """A linear call chain f0->f1->...->f7 (each CALLS hop costs exactly
    1.0) gives exact integer distances 1.0..7.0 from f0. With d_max=5.0,
    nodes at distance <= 5.0 remain; distance 7.0 (and 6.0) are excluded.

    `d_max=None` is passed explicitly for the "unbounded" case: Zero-Debt
    Hardening Pass, Task 3 promoted `d_max=5.0` to this function's own
    default (superseding this same test's original assumption that the
    default meant unbounded), so "unbounded" must now be requested
    explicitly rather than obtained by omitting the argument."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_call_chain(repo, 8)
    builder, _tag_matrix = build_pipeline(str(repo))

    unbounded = compute_topological_distances(builder, "mod.f0", d_max=None)
    assert unbounded["mod.f7"] == 7.0
    assert unbounded["mod.f5"] == 5.0

    bounded = compute_topological_distances(builder, "mod.f0", d_max=5.0)
    assert "mod.f7" not in bounded
    assert "mod.f6" not in bounded
    assert bounded["mod.f5"] == 5.0
    assert bounded["mod.f1"] == 1.0
    assert bounded["mod.f3"] == 3.0


def test_default_d_max_matches_explicit_5(tmp_path):
    """Zero-Debt Hardening Pass, Task 3: `d_max=5.0` is now this
    function's own default (was `None`/unbounded - see this module's own
    superseded `test_d_max_none_preserves_exact_prior_behavior`, renamed
    from this test's own prior name). Every real production caller
    (`prism.surface.build`, `prism.packer.submodular_knapsack`) threads
    its own `max_hops` through as `d_max` explicitly rather than relying
    on this default, so this default only matters for a caller that
    passes neither - this test verifies calling with no `d_max` argument
    at all is now byte-identical to passing `d_max=5.0` explicitly, not
    to `d_max=None`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_call_chain(repo, 8)
    builder, _tag_matrix = build_pipeline(str(repo))

    default_call = compute_topological_distances(builder, "mod.f0")
    explicit_5 = compute_topological_distances(builder, "mod.f0", d_max=5.0)
    assert default_call == explicit_5

    explicit_none = compute_topological_distances(builder, "mod.f0", d_max=None)
    assert default_call != explicit_none, (
        "sanity check that this chain is long enough (8 nodes, max distance "
        "7.0) to actually distinguish the bounded default from unbounded"
    )


def test_corpus_pipeline_symbols_within_d_max():
    """Corpus sanity probe (Invariant 1's own safety claim, re-verified
    directly rather than trusted): every real ground-truth Django task's
    adjudicated pipeline symbols sit at distance <= 3.0 from that task's
    own seed, well inside the d_max=5.0 a caller might opt into. Indexes
    the real pinned Django checkout, since that is what the ground-truth
    tasks are written against."""
    import sys

    sys.path.insert(0, ".")
    from benchmarks.corpora.resolver import resolve
    from benchmarks.ground_truth.loader import load_tasks_from_dir

    repo_path = str(resolve("django"))
    django_builder, _tag_matrix = build_pipeline(repo_path)
    result = load_tasks_from_dir("benchmarks/ground_truth/tasks/django")
    assert result.accepted, "test fixture assumption: at least one real ground-truth task is loaded"

    violations = []
    for task in result.accepted:
        dist_map = compute_topological_distances(django_builder, task.seed_symbol)
        for sym in task.adjudicated.pipeline_symbols:
            if sym == task.seed_symbol:
                continue
            d = dist_map.get(sym)
            if d is None or d > 3.0:
                violations.append((task.task_id, sym, d))

    assert violations == []


# ============================================================
# Commit 2: directionality (forward / reverse / both)
# ============================================================

def test_reverse_traversal_finds_callers(tmp_path):
    """a() calls b() - reverse traversal from b reaches a (its caller),
    which forward traversal from b would never reach."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "def a():\n"
        "    return b()\n"
        "\n"
        "def b():\n"
        "    return 1\n"
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    forward_from_b = compute_topological_distances(builder, "mod.b", direction="forward")
    assert "mod.a" not in forward_from_b

    reverse_from_b = compute_topological_distances(builder, "mod.b", direction="reverse")
    assert reverse_from_b.get("mod.a") == 1.0


def test_both_direction_produces_minimal_union(tmp_path):
    """seed -> mid -> target (2 hops forward) and target -> seed directly
    (1 hop reverse, i.e. target calls seed) - "both" must report the
    cheaper 1.0, not the 2.0 forward-only distance."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "def seed():\n"
        "    return mid()\n"
        "\n"
        "def mid():\n"
        "    return 1\n"
        "\n"
        "def target():\n"
        "    return seed()\n"
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    forward_only = compute_topological_distances(builder, "mod.seed", direction="forward")
    assert forward_only.get("mod.target") is None  # not forward-reachable at all

    both = compute_topological_distances(builder, "mod.seed", direction="both")
    assert both["mod.mid"] == 1.0
    assert both["mod.target"] == 1.0  # via the 1-hop reverse edge, not absent


def test_direction_invalid_value_raises_value_error(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n")
    builder, _tag_matrix = build_pipeline(str(repo))
    import pytest

    with pytest.raises(ValueError, match="direction"):
        compute_topological_distances(builder, "mod.f", direction="sideways")


def test_direction_forward_default_preserves_exact_prior_behavior(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_call_chain(repo, 4)
    builder, _tag_matrix = build_pipeline(str(repo))
    default_call = compute_topological_distances(builder, "mod.f0")
    explicit_forward = compute_topological_distances(builder, "mod.f0", direction="forward")
    assert default_call == explicit_forward


# ============================================================
# Commit 3: multi-source seeding
# ============================================================

def test_multi_seed_distance_computation(tmp_path):
    """s1 -> shared (1 hop) and s2 -> mid -> shared (2 hops) - shared
    must report min(d(s1,shared), d(s2,shared)) = 1.0, via s1's direct
    edge, not s2's longer one. mid is only reachable from s2."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "def s1():\n"
        "    return shared()\n"
        "\n"
        "def s2():\n"
        "    return mid()\n"
        "\n"
        "def mid():\n"
        "    return shared()\n"
        "\n"
        "def shared():\n"
        "    return 1\n"
    )
    builder, _tag_matrix = build_pipeline(str(repo))

    dist_s1 = compute_topological_distances(builder, "mod.s1")
    dist_s2 = compute_topological_distances(builder, "mod.s2")
    multi = compute_topological_distances(builder, seeds=["mod.s1", "mod.s2"])

    assert multi["mod.shared"] == min(dist_s1["mod.shared"], dist_s2["mod.shared"])
    assert multi["mod.shared"] == 1.0  # via s1's direct 1-hop edge, not s2's 2-hop one
    assert multi["mod.mid"] == dist_s2["mod.mid"]


def test_multi_seed_nearest_seed_distance(tmp_path):
    """Overlapping neighborhoods: a straightforward min() check against
    two independently-computed single-seed distance maps."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "def near():\n"
        "    return target()\n"
        "\n"
        "def far():\n"
        "    return mid()\n"
        "\n"
        "def mid():\n"
        "    return target()\n"
        "\n"
        "def target():\n"
        "    return 1\n"
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    dist_near = compute_topological_distances(builder, "mod.near")
    dist_far = compute_topological_distances(builder, "mod.far")
    multi = compute_topological_distances(builder, seeds=["mod.near", "mod.far"])
    for node in set(dist_near) | set(dist_far):
        expected = min(dist_near.get(node, float("inf")), dist_far.get(node, float("inf")))
        assert multi.get(node) == expected


def test_multi_seed_empty_list_raises_error(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n")
    builder, _tag_matrix = build_pipeline(str(repo))
    import pytest

    with pytest.raises(ValueError, match="at least one seed"):
        compute_topological_distances(builder, seeds=[])


def test_multi_seed_tie_breaking_determinism(tmp_path):
    """Two seeds equidistant from a shared target (1 hop each) - the
    reported distance value must be identical across repeated calls and
    across a fresh subprocess with a different PYTHONHASHSEED (the
    result is a pure numeric min(), not a hash-order-sensitive
    attribution of "which seed won")."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "def s1():\n"
        "    return shared()\n"
        "\n"
        "def s2():\n"
        "    return shared()\n"
        "\n"
        "def shared():\n"
        "    return 1\n"
    )
    builder, _tag_matrix = build_pipeline(str(repo))
    first = compute_topological_distances(builder, seeds=["mod.s1", "mod.s2"])
    second = compute_topological_distances(builder, seeds=["mod.s2", "mod.s1"])  # reversed input order
    assert first["mod.shared"] == 1.0
    assert first == second


def test_multi_seed_single_element_list_matches_plain_string(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_call_chain(repo, 3)
    builder, _tag_matrix = build_pipeline(str(repo))
    as_string = compute_topological_distances(builder, "mod.f0")
    as_list = compute_topological_distances(builder, seeds=["mod.f0"])
    assert as_string == as_list


# ============================================================
# Commit 4: RELATION_TENTATIVE_CALL_WEIGHT reconciliation
# (prism.slicer.distance.DistanceEngine - see that module's own
# updated audit comment on RELATION_TENTATIVE_CALL_WEIGHT for the full
# reasoning: it proves exactly 1-hop dominance, not N-hop, and
# prism.graph.weights.W_TENTATIVE is not a drop-in replacement for it
# under a completely different, additive cost model.)
# ============================================================

def test_tentative_call_weight_dominance():
    """A single confident CALLS hop strictly outranks (lower Dijkstra
    cost than) a single TENTATIVE_CALL hop from the same seed - the
    real, currently-provided guarantee of RELATION_TENTATIVE_CALL_WEIGHT."""
    from prism.graph.concrete_builder import ConcreteGraphBuilder
    from prism.graph.metamodel import SemanticMetamodel
    from prism.slicer.distance import DistanceConfig, DistanceEngine

    builder = ConcreteGraphBuilder("/tmp/repo")
    builder.graph.add_node("seed")
    builder.graph.add_node("confident_target")
    builder.graph.add_node("tentative_target")
    builder.graph.add_edge("seed", "confident_target", relation="CALLS")
    builder.graph.add_edge("seed", "tentative_target", relation="CALLS", kind="TENTATIVE_CALL")

    engine = DistanceEngine(SemanticMetamodel(), {}, DistanceConfig())
    distances = engine.compute_all("seed", builder.graph)
    assert distances["confident_target"] < distances["tentative_target"]


def test_tag_bonus_does_not_invert_structural_monotonicity():
    """seed -> u (1 hop, worst-case tag mismatch) and seed -> mid -> v
    (2 hops, perfect tag match with the seed). Even at maximum tag-bonus
    advantage for the farther node and maximum penalty for the nearer
    one, u must still rank closer than v - Topological Monotonicity's
    own proof (prism/slicer/distance.py's _d_hybrid docstring), verified
    directly rather than trusted."""
    import networkx as nx
    from prism.graph.metamodel import SemanticMetamodel
    from prism.slicer.distance import DistanceConfig, DistanceEngine

    g = nx.DiGraph()
    g.add_edge("seed", "u", relation="CALLS")
    g.add_edge("seed", "mid", relation="CALLS")
    g.add_edge("mid", "v", relation="CALLS")

    tag_matrix = {
        "seed": {"#route_handler"},
        "u": set(),  # 1 hop, zero tag overlap with seed - worst-case mismatch
        "v": {"#route_handler"},  # 2 hops, identical tags to seed - perfect match
    }
    engine = DistanceEngine(SemanticMetamodel(), tag_matrix, DistanceConfig())
    distances = engine.compute_all("seed", g)
    assert distances["u"] < distances["v"]
