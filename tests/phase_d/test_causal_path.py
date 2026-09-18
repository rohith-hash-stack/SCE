"""Phase D: causal path layer (task-type gating, subsystem scope rule,
sink substance ranking).

Scope note: Commit 1 wires task_type through the real harness path
(benchmarks.engines.prism_engine[_cache] -> prism.surface.build.
build_context_package), fixing Blockers B2 / Issues #37-38 (a T13-style
task previously always got a causal_path, since nothing gated on
task_type anywhere in the harness). Commits 2-3 harden prism.surface.
causal_path itself (the real, already-live algorithm that computes the
path over the already-packed subgraph) with a subsystem scope filter and
substance-overlap sink ranking, fixing Issues #24/#42 (the t02_006 GIS
drift).
"""
from benchmarks.engines.prism_engine_cache import PrismEngineCache
from prism.cli import build_pipeline
from prism.surface.build import build_context_package, causal_path_applies_to_task_type


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
