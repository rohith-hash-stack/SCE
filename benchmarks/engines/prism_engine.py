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
        # use_cache=False (experiment/noise-filtering-spike only, not a
        # production change): prism.runtime.index_cache's whole-pipeline
        # cache (build_pipeline's own use_cache=True default) was found,
        # empirically, to return genuinely inconsistent (builder,
        # tag_matrix) results across separate process invocations of the
        # identical pinned corpus - a real seed's own reachable candidate
        # count varied 409 vs 402 across repeated runs with no other
        # variable changed (ruled out: file-discovery order - discover_
        # files already sorts; PYTHONHASHSEED - fixing it to 0 did not
        # stabilize the result; concurrent-process cache races - the
        # instability reproduced across strictly sequential runs too).
        # 3 consecutive use_cache=False runs agreed exactly (409, 409,
        # 409); this is the same class of bug as the SymbolInfo.role
        # cache-serialization gap fixed in 07ff0cd, a different instance
        # in the same caching layer - flagged as a follow-up production
        # issue, not fixed here (out of scope for this spike branch,
        # which touches no prism.* production code). Every approach in
        # this spike depends on a deterministic graph, so this is set
        # here, in the shared benchmark engine, not just in Approach A's
        # own code.
        self._builder, _tag_matrix = build_pipeline(repo_path, use_cache=False)
        self._contracts = compute_or_load_contracts(self._builder, repo_path)

    def retrieve(self, seed_symbol: str, budget_tokens: int, task_type: str | None = None) -> ContextPackage:
        if self._builder is None or self._repo_root is None:
            raise RuntimeError("PrismEngine.retrieve called before index()")
        return build_context_package(
            self._builder,
            seed_symbol,
            self._repo_root,
            budget_tokens,
            contracts=self._contracts,
            max_hops=self._max_hops,
            task_type=task_type,
        )
