"""Phase D: causal path layer (task-type gating, subsystem scope rule,
sink substance ranking).

Scope note: Commit 1 wires task_type through the real harness path
(benchmarks.engines.prism_engine[_cache] -> prism.surface.build.
build_context_package), fixing Blockers B2 / Issues #37-38 (a T13-style
task previously always got a causal_path, since nothing gated on
task_type anywhere in the harness). Commits 2-3 hardern prism.surface.
causal_path itself (the real, already-live algorithm that computes the
path over the already-packed subgraph) with a subsystem scope filter and
substance-overlap sink ranking, fixing Issues #24/#42 (the t02_006 GIS
drift).
"""
from dataclasses import dataclass

from benchmarks.engines.prism_engine_cache import PrismEngineCache
from prism.cli import build_pipeline
from prism.semantics.bitmask import FeatureBit
from prism.surface.build import build_context_package, causal_path_applies_to_task_type
from prism.surface.causal_path import compute_causal_path_stages


def _output_kind(mask: int) -> str:
    return "command" if mask & int(FeatureBit.OUTPUT_COMMAND) else "query"


@dataclass
class _FakeSymbol:
    """The 3 attributes prism.surface.causal_path's symbol_info lookup
    actually reads (.module, .file, .line_range) - a minimal stand-in
    for the real SymbolInfo, so these tests control module/substance
    directly instead of depending on real AST-based tag detection."""
    module: str
    file: str = "f.py"
    line_range: tuple = (1, 2)


# ============================================================
# Commit 1: task-type gating
# ============================================================

def test_causal_path_applies_to_task_type_unit():
    """Direct unit coverage of the gating predicate itself, independent
    of the full retrieval pipeline - real EvaluationTask.task_type
    values only (benchmarks.ground_truth.schema's own literal enum)."""
    assert causal_path_applies_to_task_type(None) is True
    assert causal_path_applies_to_task_type("chain") is True
    assert causal_path_applies_to_task_type("debug") is True
    assert causal_path_applies_to_task_type("blast") is False
    assert causal_path_applies_to_task_type("architecture") is False
    assert causal_path_applies_to_task_type("redundancy") is False


def test_causal_path_bypassed_for_t13_tasks(tmp_path):
    """retrieve() with task_type="blast" (T13's own real task_type
    value) yields no causal_path - via the real harness engine
    (PrismEngineCache), not build_context_package called directly, so
    this exercises the actual wiring runner.py depends on."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def seed():\n    return helper()\n\ndef helper():\n    return 1\n")
    engine = PrismEngineCache()
    engine.index(str(repo))
    for task_type in ("blast", "architecture", "redundancy"):
        pkg = engine.retrieve("mod.seed", 4000, task_type=task_type)
        assert pkg.causal_path is None, f"task_type={task_type!r} should bypass causal_path"


def test_causal_path_included_for_t02_tasks(tmp_path):
    """retrieve() with task_type="debug"/"chain" (T02's own real values)
    still computes a causal_path normally."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def seed():\n    return helper()\n\ndef helper():\n    return 1\n")
    engine = PrismEngineCache()
    engine.index(str(repo))
    for task_type in ("debug", "chain"):
        pkg = engine.retrieve("mod.seed", 4000, task_type=task_type)
        assert pkg.causal_path is not None, f"task_type={task_type!r} should compute causal_path"
        assert pkg.causal_path.stages[0].symbol == "mod.seed"


def test_causal_path_task_type_none_is_unaffected(tmp_path):
    """task_type=None (every pre-Phase-D caller, or an ad-hoc MCP query
    with no benchmark-task context) must be byte-equivalent to not
    passing task_type at all - no accidental behavior change."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def seed():\n    return helper()\n\ndef helper():\n    return 1\n")
    builder, _tag_matrix = build_pipeline(str(repo))
    default_call = build_context_package(builder, "mod.seed", str(repo), 4000)
    explicit_none = build_context_package(builder, "mod.seed", str(repo), 4000, task_type=None)
    assert (default_call.causal_path is None) == (explicit_none.causal_path is None)
    assert default_call.causal_path is not None


def test_explicit_include_causal_path_overrides_task_type(tmp_path):
    """include_causal_path=True explicitly always wins over task_type,
    exactly as it already won over the env var - task_type is only ever
    consulted when include_causal_path is left None."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def seed():\n    return helper()\n\ndef helper():\n    return 1\n")
    builder, _tag_matrix = build_pipeline(str(repo))
    pkg = build_context_package(builder, "mod.seed", str(repo), 4000, include_causal_path=True, task_type="blast")
    assert pkg.causal_path is not None


# ============================================================
# Commits 2-3: subsystem scope rule + sink substance ranking
# (direct unit tests of compute_causal_path_stages, with a fake
# symbol_info lookup so module/substance are controlled directly
# rather than depending on real AST-based tag detection)
# ============================================================

def test_scope_rule_blocks_cross_namespace_without_substance():
    """seed (pkg.a.b) -> mid (pkg.c.d), disjoint substance, disjoint
    module prefix at depth 3 - the edge is out of scope, so mid (and
    anything only reachable through it) never appears in the path."""
    symbols = {
        "pkg.a.b.seed": _FakeSymbol(module="pkg.a.b"),
        "pkg.c.d.mid": _FakeSymbol(module="pkg.c.d"),
    }
    feature_masks = {
        "pkg.a.b.seed": 0,
        "pkg.c.d.mid": int(FeatureBit.SINK_NETWORK_IO),  # a real sink, but unreachable in-scope
    }
    edges = [("pkg.a.b.seed", "pkg.c.d.mid")]
    path, truncated = compute_causal_path_stages(
        "pkg.a.b.seed", {"pkg.a.b.seed", "pkg.c.d.mid"}, edges, {}, feature_masks, _output_kind, symbols.get
    )
    assert path == ["pkg.a.b.seed"]
    assert truncated is False


def test_scope_rule_permits_cross_namespace_with_substance():
    """Same disjoint-module shape as above, but seed and mid now share
    a substance tag (#auth_required's own real underlying bit here is
    SINK_DATABASE_IO, standing in for "the two symbols tag the same
    real-world purpose despite living in different packages") - the
    cross-package edge is permitted, and mid (a real sink) is reached."""
    symbols = {
        "pkg.a.b.seed": _FakeSymbol(module="pkg.a.b"),
        "pkg.c.d.mid": _FakeSymbol(module="pkg.c.d"),
    }
    shared_substance = int(FeatureBit.SINK_DATABASE_IO)
    feature_masks = {
        "pkg.a.b.seed": shared_substance,
        "pkg.c.d.mid": shared_substance,
    }
    edges = [("pkg.a.b.seed", "pkg.c.d.mid")]
    path, truncated = compute_causal_path_stages(
        "pkg.a.b.seed", {"pkg.a.b.seed", "pkg.c.d.mid"}, edges, {}, feature_masks, _output_kind, symbols.get
    )
    assert path == ["pkg.a.b.seed", "pkg.c.d.mid"]
    assert truncated is False


def test_empty_causal_path_when_no_in_scope_chain():
    """seed has a real graph edge to a cross-subsystem, substance-
    disjoint node and nothing else - once that edge is filtered out,
    there is no in-scope chain at all, so the path is just the seed
    alone (the closest a "must carry at least one stage" causal_path
    can come to Invariant 3's own "emit an empty path" wording - see
    prism.surface.causal_path.compute_causal_path_stages's own
    docstring for why)."""
    symbols = {
        "pkg.a.seed": _FakeSymbol(module="pkg.a"),
        "pkg.z.unrelated": _FakeSymbol(module="pkg.z"),
    }
    feature_masks = {"pkg.a.seed": 0, "pkg.z.unrelated": 0}
    edges = [("pkg.a.seed", "pkg.z.unrelated")]
    path, truncated = compute_causal_path_stages(
        "pkg.a.seed", {"pkg.a.seed", "pkg.z.unrelated"}, edges, {}, feature_masks, _output_kind, symbols.get
    )
    assert path == ["pkg.a.seed"]
    assert truncated is False


def test_sink_ranking_prefers_substance_overlap():
    """Two reachable sinks: near_disjoint is 1 hop away but shares no
    substance with the seed; far_matching is 2 hops away but shares the
    seed's own substance tag. Invariant 3's ranking (substance overlap
    DESC, then distance ASC) must pick far_matching, not the closer
    hop-count-only winner the pre-Phase-D algorithm would have chosen."""
    seed_substance = int(FeatureBit.SINK_DATABASE_IO)
    symbols = {
        "pkg.a.seed": _FakeSymbol(module="pkg.a"),
        "pkg.a.near_disjoint": _FakeSymbol(module="pkg.a"),
        "pkg.a.mid": _FakeSymbol(module="pkg.a"),
        "pkg.a.far_matching": _FakeSymbol(module="pkg.a"),
    }
    feature_masks = {
        "pkg.a.seed": seed_substance,
        "pkg.a.near_disjoint": int(FeatureBit.SINK_NETWORK_IO),  # a sink, 1 hop, no overlap with seed
        "pkg.a.mid": 0,
        "pkg.a.far_matching": seed_substance,  # a sink, 2 hops, shares the seed's own substance
    }
    edges = [
        ("pkg.a.seed", "pkg.a.near_disjoint"),
        ("pkg.a.seed", "pkg.a.mid"),
        ("pkg.a.mid", "pkg.a.far_matching"),
    ]
    dist_w_map = {"pkg.a.near_disjoint": 1.0, "pkg.a.mid": 1.0, "pkg.a.far_matching": 2.0}
    path, truncated = compute_causal_path_stages(
        "pkg.a.seed",
        {"pkg.a.seed", "pkg.a.near_disjoint", "pkg.a.mid", "pkg.a.far_matching"},
        edges,
        dist_w_map,
        feature_masks,
        _output_kind,
        symbols.get,
    )
    assert path == ["pkg.a.seed", "pkg.a.mid", "pkg.a.far_matching"]
    assert truncated is False


# ============================================================
# Real-corpus regression check: the t02_006 GIS-drift bug itself
# (Investigation A finding: get_user -> constant_time_compare ->
# force_bytes -> GEOSGeometryBase._from_wkt, a real bug this phase
# exists to fix)
# ============================================================

def test_t02_006_gis_drift_blocked():
    """The real django_t02_006 seed's causal_path must never wander into
    django.contrib.gis - confirmed directly against the real pinned
    Django corpus, not a synthetic stand-in, since the original bug was
    only ever observed against the real repo's own graph shape."""
    import sys

    sys.path.insert(0, ".")
    from benchmarks.corpora.resolver import resolve

    repo_path = str(resolve("django"))
    engine = PrismEngineCache()
    engine.index(repo_path)
    pkg = engine.retrieve("django.contrib.auth.middleware.get_user", 4000, task_type="debug")

    assert pkg.causal_path is not None
    gis_stages = [s for s in pkg.causal_path.stages if ".gis." in s.symbol]
    assert gis_stages == [], f"causal_path drifted into GIS: {gis_stages}"
