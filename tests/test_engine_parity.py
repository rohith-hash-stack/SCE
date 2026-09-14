"""Test-practice: cross-engine interface parity. Every engine in
`benchmarks/engines/` implements `AbstractRetrievalEngine`
(`index`/`retrieve` -> `ContextPackage`), and the harness (`benchmarks.
runner`) treats them interchangeably - a sweep runs the same (seed,
budget) through every engine and compares results. This file checks the
contract every engine actually needs to honor for that comparison to be
meaningful, using the four engines directly constructible against a
plain repo/seed with no extra fixtures (`OracleEngine`/`PragmaticOracle`
need a pre-existing oracle-package file or a full `EvaluationTask` and
are out of scope here)."""
from __future__ import annotations

from benchmarks.engines.base import AbstractRetrievalEngine, selected_symbols
from benchmarks.engines.baseline_bfs import BaselineBFSEngine
from benchmarks.engines.baseline_rag import BaselineRAGEngine
from benchmarks.engines.prism_engine import PrismEngine
from benchmarks.engines.prism_engine_cache import PrismEngineCache

_SOURCE = (
    "def leaf_a():\n    return 1\n\n\n"
    "def leaf_b():\n    return 2\n\n\n"
    "def mid(x):\n    return leaf_a() + leaf_b() + x\n\n\n"
    "class Worker:\n"
    "    def __init__(self, x):\n        self.x = x\n\n"
    "    def run(self):\n        return mid(self.x)\n"
)


def _engines(cache_dir):
    return [
        PrismEngine(),
        PrismEngineCache(cache_dir=cache_dir),
        BaselineRAGEngine(),
        BaselineBFSEngine(mode="forward"),
        BaselineBFSEngine(mode="bidirectional"),
    ]


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(_SOURCE)
    return repo


def test_every_engine_subclasses_the_abstract_interface(tmp_path):
    for engine in _engines(tmp_path / "cache"):
        assert isinstance(engine, AbstractRetrievalEngine)


def test_every_engine_reports_seed_and_budget_correctly(tmp_path):
    repo = _repo(tmp_path)
    for engine in _engines(tmp_path / "cache"):
        engine.index(str(repo))
        pkg = engine.retrieve("svc.Worker.run", 2000)
        assert pkg.seed.symbol == "svc.Worker.run", engine.name
        assert pkg.budget.tokens == 2000, engine.name


def test_every_engine_includes_the_queried_seed_in_its_own_selection(tmp_path):
    """A minimal sanity bar every real retrieval engine should clear:
    whatever else it does, it must include the symbol it was actually
    asked about."""
    repo = _repo(tmp_path)
    for engine in _engines(tmp_path / "cache"):
        engine.index(str(repo))
        pkg = engine.retrieve("svc.Worker.run", 2000)
        assert "svc.Worker.run" in selected_symbols(pkg), engine.name


def test_engine_names_are_unique_across_a_harness_sweep(tmp_path):
    """benchmarks.reporting groups results by pkg.engine.name /
    engine.name - a collision would silently merge two engines' results
    in any report. PrismEngineCache is deliberately excluded: its own
    docstring states it shares PrismEngine's name on purpose (a drop-in
    *replacement* in the engine list, never run alongside plain
    PrismEngine in the same sweep) - the two are not meant to coexist,
    so their shared name is by design, not a collision to catch here."""
    engines = [e for e in _engines(tmp_path / "cache") if not isinstance(e, PrismEngineCache)]
    names = [e.name for e in engines]
    assert len(names) == len(set(names)), names


def test_every_engine_produces_a_schema_valid_package_for_multiple_seeds(tmp_path):
    """Pydantic validates ContextPackage at construction time - this
    just confirms every engine actually reaches that construction
    (no exception) for more than one seed, not only the one most
    obviously exercised."""
    repo = _repo(tmp_path)
    for engine in _engines(tmp_path / "cache"):
        engine.index(str(repo))
        for seed in ("svc.leaf_a", "svc.mid", "svc.Worker.run"):
            pkg = engine.retrieve(seed, 2000)
            assert pkg.seed.symbol == seed, (engine.name, seed)


def test_each_engine_is_internally_deterministic_across_repeated_retrieves(tmp_path):
    repo = _repo(tmp_path)
    for engine in _engines(tmp_path / "cache"):
        engine.index(str(repo))
        first = selected_symbols(engine.retrieve("svc.Worker.run", 2000))
        second = selected_symbols(engine.retrieve("svc.Worker.run", 2000))
        assert first == second, engine.name
