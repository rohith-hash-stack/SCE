"""Phase 1B: the `<causal_path>` envelope block
(`prism.surface.causal_path.compute_causal_path`, wired into
`prism.surface.build.build_context_package` and rendered/parsed by
`prism.surface.renderer`/`prism.surface.parser`).

A T02-style (chain/debug) single-seed retrieval gets a forward causal
chain from its seed to whichever reachable sink (or, absent one, the
seed's own longest forward chain) explains the story - so a model
reading the envelope doesn't have to re-derive call order from the
unordered `<nodes>`/`<edges>` sets itself. A T13-style (blast-radius)
retrieval - upstream callers, not a forward chain - gets no
`<causal_path>` block at all.

Real-corpus tests (marked `@pytest.mark.slow`, matching `tests/
test_prism_selection_regressions.py`'s own convention) reuse that same
module-scoped `prism_engine`/`django_tasks` real-Django-checkout setup;
synthetic-repo tests build a tiny throwaway repo per test, the same
pattern `tests/test_pipeline_preservation.py` already uses.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.corpora.resolver import resolve
from benchmarks.engines.prism_engine_cache import PrismEngineCache
from benchmarks.ground_truth.loader import load_tasks_from_dir
from prism.cli import build_pipeline
from prism.surface.build import build_context_package
from prism.surface.parser import parse_context
from prism.surface.renderer import RenderOptions, render

pytestmark = pytest.mark.slow

TASKS_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "ground_truth" / "tasks" / "django"

#: The 5 real TSR=1.0 tasks (matching `test_prism_selection_regressions.
#: py`'s own `WINNING_TASKS`) - t02_010/011/019/020 are the 4 the task
#: brief names by id; t02_008 is "one other TSR=1.0 task".
ORDER_CHECK_TASKS = [
    "django_t02_008_db_session_load_decode",
    "django_t02_010_i18n_catalog_translation",
    "django_t02_011_permissions_backend_check",
    "django_t02_019_request_get_host_validation",
    "django_t02_020_storage_generate_filename",
]


@pytest.fixture(scope="module")
def django_repo_path() -> str:
    return str(resolve("django"))


@pytest.fixture(scope="module")
def django_tasks(django_repo_path) -> dict:
    result = load_tasks_from_dir(TASKS_DIR)
    assert not result.rejected, f"ground-truth tasks failed to load: {result.rejected}"
    return {t.task_id: t for t in result.accepted}


@pytest.fixture(scope="module")
def prism_engine(django_repo_path) -> PrismEngineCache:
    engine = PrismEngineCache()
    engine.index(django_repo_path)
    return engine


# --------------------------------------------------------------------- #
# 1. Present for T02 (chain/debug) tasks
# --------------------------------------------------------------------- #
def test_causal_path_present_for_t02_tasks(prism_engine, django_tasks):
    """Every T02 (`task_type == "debug"`) task's envelope carries a
    non-empty `<causal_path>` whose first stage is the seed itself,
    entry-rolled."""
    t02_tasks = [t for t in django_tasks.values() if t.task_type == "debug"]
    assert len(t02_tasks) >= 5, "test fixture assumption: several real T02 tasks are loaded"

    for task in t02_tasks[:5]:
        pkg = prism_engine.retrieve(task.seed_symbol, 4000)
        assert pkg.causal_path is not None, f"{task.task_id}: expected a causal_path block"
        assert len(pkg.causal_path.stages) >= 1
        first = pkg.causal_path.stages[0]
        assert first.order == 1
        assert first.symbol == task.seed_symbol
        assert first.role == "entry"
        assert pkg.causal_path.seed == task.seed_symbol
        assert pkg.causal_path.direction == "forward"


# --------------------------------------------------------------------- #
# 2. Absent for T13 (blast) tasks / overview mode
# --------------------------------------------------------------------- #
def test_causal_path_absent_for_t13_tasks(django_repo_path, django_tasks):
    """A blast-radius retrieval (T13's own `task_type == "blast"`) is
    built with `include_causal_path=False` - no `<causal_path>` block in
    the resulting `ContextPackage`, and none in the rendered XML either
    (never an empty element)."""
    builder, _ = build_pipeline(django_repo_path)
    t13_tasks = [t for t in django_tasks.values() if t.task_type == "blast"]
    assert len(t13_tasks) >= 1, "test fixture assumption: at least one real T13 task is loaded"

    for task in t13_tasks[:2]:
        pkg = build_context_package(builder, task.seed_symbol, django_repo_path, 4000, include_causal_path=False)
        assert pkg.causal_path is None
        xml = render(pkg)
        assert "<causal_path" not in xml


# --------------------------------------------------------------------- #
# 3. Order matches the adjudicated pipeline (real TSR=1.0 tasks)
# --------------------------------------------------------------------- #
def test_causal_path_order_matches_adjudicated_pipeline(prism_engine, django_tasks):
    """On the 5 real tasks where Prism's own TSR is 1.0 - the packed
    context already contains the task's true causal pipeline - whichever
    of `adjudicated.pipeline_symbols` the computed `causal_path` also
    names appear in the *same relative order* as the adjudicated
    pipeline itself (a subsequence match, not byte-for-byte equality:
    `compute_causal_path` is a mechanical, sink/longest-chain algorithm
    over the packed subgraph, not a re-implementation of a human
    annotator's own curation - see `prism.surface.causal_path`'s own
    docstring). The path always starts at the seed, which is always
    `pipeline_symbols[0]` too.
    """
    for task_id in ORDER_CHECK_TASKS:
        task = django_tasks[task_id]
        pkg = prism_engine.retrieve(task.seed_symbol, 4000)
        assert pkg.causal_path is not None, task_id

        path_symbols = [s.symbol for s in pkg.causal_path.stages]
        assert path_symbols[0] == task.seed_symbol == task.adjudicated.pipeline_symbols[0], task_id

        pipeline = task.adjudicated.pipeline_symbols
        common_in_path_order = [s for s in path_symbols if s in pipeline]
        expected_order = [s for s in pipeline if s in path_symbols]
        assert common_in_path_order == expected_order, (
            f"{task_id}: causal_path {path_symbols} scrambles the adjudicated "
            f"pipeline order {pipeline}"
        )


# --------------------------------------------------------------------- #
# 4. Deterministic across processes
# --------------------------------------------------------------------- #
def test_causal_path_deterministic_across_processes(prism_engine, django_tasks):
    """Same (seed, budget) against the same indexed graph -> the exact
    same `causal_path` (stage order, symbols, distances, roles,
    truncated flag), run twice - `compute_causal_path`'s own sorted-
    adjacency BFS is what this actually stands on."""
    task = django_tasks["django_t02_015_admin_each_context"]
    pkg1 = prism_engine.retrieve(task.seed_symbol, 4000)
    pkg2 = prism_engine.retrieve(task.seed_symbol, 4000)

    assert pkg1.causal_path is not None
    assert pkg1.causal_path == pkg2.causal_path


# --------------------------------------------------------------------- #
# 5. Max length capped (synthetic 10-stage chain)
# --------------------------------------------------------------------- #
def _linear_chain_repo(tmp_path, prefix: str, n: int):
    repo = tmp_path / f"{prefix}_repo"
    repo.mkdir()
    lines = []
    for i in range(n):
        if i < n - 1:
            lines.append(f"def {prefix}{i}(x):\n    return {prefix}{i + 1}(x)\n")
        else:
            lines.append(f"def {prefix}{i}(x):\n    return x\n")
    (repo / "chain.py").write_text("\n\n".join(lines) + "\n")
    return repo


def test_causal_path_max_length_capped(tmp_path):
    """A real, packed 10-function linear chain with no sink anywhere in
    it (every stage just returns its callee's own return value - a
    Query, never a Command) falls back to "the seed's longest forward
    chain" - capped at 6 stages, `truncated=True`, the first 6 links of
    the real 10-link chain, not an arbitrary subset."""
    repo = _linear_chain_repo(tmp_path, "f", 10)
    builder, _ = build_pipeline(str(repo))
    pkg = build_context_package(builder, "chain.f0", str(repo), target_budget=100_000, max_hops=20.0)

    assert pkg.causal_path is not None
    assert pkg.causal_path.truncated is True
    assert len(pkg.causal_path.stages) == 6
    assert [s.symbol for s in pkg.causal_path.stages] == [f"chain.f{i}" for i in range(6)]
    assert [s.order for s in pkg.causal_path.stages] == [1, 2, 3, 4, 5, 6]
    assert pkg.causal_path.stages[0].role == "entry"
    for s in pkg.causal_path.stages[1:-1]:
        assert s.role == "transform"
    # role is resolved against the *rendered* (post-truncation) path, not
    # the untruncated chain - f5 is the last rendered stage here, and it
    # isn't itself a sink, so it's "return", not "transform"; the real
    # sink/endpoint (f9) never appears at all once truncated away.
    assert pkg.causal_path.stages[-1].role == "return"


# --------------------------------------------------------------------- #
# 6. No-sink fallback (short synthetic chain, no truncation)
# --------------------------------------------------------------------- #
def test_causal_path_no_sink_fallback(tmp_path):
    """A short (3-function) synthetic chain with no substance-sink
    feature and no Command-with-no-successors anywhere: `compute_causal_
    path` finds no sink at all and falls back to the seed's own longest
    forward chain - here, simply the whole chain, untruncated, its last
    stage `role="return"` (not `"sink"`, since it never matched the sink
    definition in the first place)."""
    repo = _linear_chain_repo(tmp_path, "g", 3)
    builder, _ = build_pipeline(str(repo))
    pkg = build_context_package(builder, "chain.g0", str(repo), target_budget=100_000, max_hops=20.0)

    assert pkg.causal_path is not None
    assert pkg.causal_path.truncated is False
    assert [s.symbol for s in pkg.causal_path.stages] == ["chain.g0", "chain.g1", "chain.g2"]
    assert pkg.causal_path.stages[0].role == "entry"
    assert pkg.causal_path.stages[1].role == "transform"
    assert pkg.causal_path.stages[-1].role == "return"


# --------------------------------------------------------------------- #
# 7. Roundtrip
# --------------------------------------------------------------------- #
def test_causal_path_roundtrip(tmp_path):
    """`parse_context(render(pkg)).causal_path == pkg.causal_path` -
    schema_version bumped to 2, rendered between `<metadata>` and
    `<manifest>`, losslessly reconstructed - exactly the same Roundtrip
    Fidelity property `tests/surface/test_renderer_properties.py`
    already holds every other envelope block to."""
    repo = _linear_chain_repo(tmp_path, "h", 4)
    builder, _ = build_pipeline(str(repo))
    pkg = build_context_package(builder, "chain.h0", str(repo), target_budget=100_000, max_hops=20.0)

    assert pkg.causal_path is not None
    xml = render(pkg, RenderOptions(include_timestamp=True, include_run_id=True))
    assert "<causal_path " in xml
    assert xml.index("<causal_path") < xml.index("<manifest")
    assert xml.index("</metadata>") < xml.index("<causal_path")

    roundtripped = parse_context(xml)
    assert roundtripped.causal_path == pkg.causal_path
    assert roundtripped.schema_version == 2
