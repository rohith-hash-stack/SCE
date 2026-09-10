"""v1.1+ Empirical Benchmarking Harness: the Prism v1.1+ engine under
test - the real, already-shipped causal engine
(`prism.packer.submodular_knapsack.pack_symbol_context`), wrapped behind
`AbstractRetrievalEngine`. No separate logic lives here: this module is
intentionally thin, since the whole point of the benchmark is to measure
the real production engine, not a benchmark-specific reimplementation of
it.

Four-Axis Bitmask Knapsack (`prism.semantics`), Continuous Dijkstra
(`prism.traversal.continuous_dijkstra`), Canonical Sinks (`prism.
semantics.canonical_sinks`), and Bidirectional Blast Radius (`prism.
packer.blast_radius`) are all exercised automatically - `prism.surface.
build.build_context_package` already wires every one of them together.

**Stated honestly**: `prism.cache.sqlite_cache`'s file-local v2 cache
exists (`prism.semantics.extractor.compute_feature_masks_cached`) but
neither `build_context_package` nor `pack_symbol_context` currently calls
that cached variant - both call the uncached `compute_feature_masks`
directly - so every `retrieve()` here re-runs full four-axis extraction,
same as production `prism causal-query` does today. Wiring the cache in
is a real, separate improvement to `prism.surface.build`/`prism.packer.
submodular_knapsack` themselves, out of scope for this benchmarking
harness to silently change underneath the engine it's measuring.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.packer.submodular_knapsack import DEFAULT_MAX_HOPS
from prism.runtime.contract_cache import compute_or_load_contracts
from prism.surface.build import build_context_package
from prism.surface.models import ContextPackage

from benchmarks.engines.base import AbstractRetrievalEngine

ENGINE_NAME = "prism_v11"


class PrismEngine(AbstractRetrievalEngine):
    name = ENGINE_NAME

    def __init__(self, max_hops: float = DEFAULT_MAX_HOPS) -> None:
        self._max_hops = max_hops
        self._repo_root: str | None = None
        self._builder: ConcreteGraphBuilder | None = None
        self._contracts = {}

    @classmethod
    def from_builder(
        cls, builder: ConcreteGraphBuilder, repo_root: str, contracts: dict | None = None, max_hops: float = DEFAULT_MAX_HOPS
    ) -> "PrismEngine":
        """Skips `index()`'s own `build_pipeline`/`compute_or_load_
        contracts` re-run for a caller that already has both (an ablation
        sweep re-running `retrieve()` many times over the same corpus
        with only a hyperparameter changed, say) - the *intended* way to
        reuse an already-indexed builder, rather than a caller reaching
        into this class's own private attributes.
        """
        engine = cls(max_hops=max_hops)
        engine._builder = builder
        engine._repo_root = repo_root
        engine._contracts = contracts or {}
        return engine

    def index(self, repo_path: str) -> None:
        self._repo_root = repo_path
        self._builder, _tag_matrix = build_pipeline(repo_path)
        self._contracts = compute_or_load_contracts(self._builder, repo_path)

    def retrieve(self, seed_symbol: str, budget_tokens: int) -> ContextPackage:
        if self._builder is None or self._repo_root is None:
            raise RuntimeError("PrismEngine.retrieve called before index()")
        return build_context_package(
            self._builder, seed_symbol, self._repo_root, budget_tokens, contracts=self._contracts, max_hops=self._max_hops
        )
