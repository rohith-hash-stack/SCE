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
    nodes at distance <= 5.0 remain; distance 7.0 (and 6.0) are excluded."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_call_chain(repo, 8)
    builder, _tag_matrix = build_pipeline(str(repo))

    unbounded = compute_topological_distances(builder, "mod.f0")
    assert unbounded["mod.f7"] == 7.0
    assert unbounded["mod.f5"] == 5.0

    bounded = compute_topological_distances(builder, "mod.f0", d_max=5.0)
    assert "mod.f7" not in bounded
    assert "mod.f6" not in bounded
    assert bounded["mod.f5"] == 5.0
    assert bounded["mod.f1"] == 1.0
    assert bounded["mod.f3"] == 3.0


def test_d_max_none_preserves_exact_prior_behavior(tmp_path):
    """d_max=None (the default, matching every existing real caller)
    must produce byte-identical results to calling with no d_max
    argument at all - no accidental behavior change for
    prism.surface.build/prism.packer.submodular_knapsack/benchmarks.runner."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_call_chain(repo, 5)
    builder, _tag_matrix = build_pipeline(str(repo))

    default_call = compute_topological_distances(builder, "mod.f0")
    explicit_none = compute_topological_distances(builder, "mod.f0", d_max=None)
    assert default_call == explicit_none


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
