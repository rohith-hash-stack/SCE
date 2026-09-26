"""Phase H (Issue #36): the real, production `PrismEngine` facade.

A thin wrapper around the two real pipeline entry points every actual
caller already uses - `prism.cli.build_pipeline` (indexing) and `prism.
surface.build.build_context_package` (retrieval, the same function
`prism.mcp.server`'s own MCP tools call directly) - rather than a
parallel reimplementation of either. Adds a clean, non-invasive
extension point: a caller can register a callback that observes a
query just before traversal starts, or the resulting `ContextPackage`
just after packing finishes, without any way to mutate what this
engine itself actually does with either.

**Distinct from `benchmarks.engines.prism_engine.PrismEngine`**, an
older, benchmark-harness-only class of the same name - that one exists
purely to give the evaluation harness (`benchmarks/`, `tests/test_*_
parity.py`) a uniform `AbstractRetrievalEngine` interface to compare
Prism against baseline engines under, and is never imported by `prism.
cli` or `prism.mcp.server`. This module is the first `PrismEngine`
actually wired into the real production call path; a real production
hook mechanism belongs here, not grafted onto the benchmark-only class.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Callable

from prism.cli import build_pipeline
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.contracts import BehavioralContract
from prism.packer.candidate_index import CANDIDATE_INDEX_MAX_HOPS, _outgoing_call_names, build_candidate_manifest
from prism.packer.submodular_knapsack import DEFAULT_UPSTREAM_MAX_HOPS
from prism.runtime.contract_cache import compute_or_load_contracts
from prism.surface.build import build_context_package, build_context_package_requested
from prism.surface.models import ContextPackage


@dataclass(frozen=True)
class QueryContext:
    """An immutable snapshot of one `retrieve()` call's own inputs -
    handed to every registered pre-traversal hook as a fresh `copy.
    deepcopy`, never a live reference, so a hook can inspect (log,
    validate, collect metrics on) a query without any way to affect
    what the engine itself goes on to do with it."""

    seed_id: str
    repo_root: str
    budget_tokens: int
    task_type: str | None = None


PreTraversalHook = Callable[[QueryContext], None]
PostPackingHook = Callable[[ContextPackage], None]
#: `(manifest_text, task_prompt) -> requested_symbols` - the caller's own
#: LLM turn, injected rather than called by `PrismEngine` itself. See
#: `PrismEngine.retrieve_two_pass`'s own docstring for why the engine
#: never makes this call directly.
RequestSymbolsCallback = Callable[[str, str], list[str]]


class PrismEngine:
    """Indexes a repository once (`from_repo`/`__init__`), then serves
    `retrieve()` calls against it - the same two-step shape `prism.mcp.
    server`'s own internal `RepoContext` already follows, given a real,
    public, importable name other code (a hook registrant, an embedder)
    can depend on instead of reaching into the MCP server's internals.
    """

    def __init__(
        self,
        builder: ConcreteGraphBuilder,
        repo_root: str,
        contracts: dict[str, BehavioralContract] | None = None,
    ) -> None:
        self._builder = builder
        self._repo_root = repo_root
        self._contracts = contracts if contracts is not None else {}
        self._pre_traversal_hooks: list[PreTraversalHook] = []
        self._post_packing_hooks: list[PostPackingHook] = []

    @classmethod
    def from_repo(cls, repo_root: str) -> "PrismEngine":
        """Convenience constructor: runs the real indexing pipeline
        (`build_pipeline` + `compute_or_load_contracts`, the same two
        calls `benchmarks.engines.prism_engine.PrismEngine.index` makes)
        and wraps the result. Equivalent to calling `build_pipeline`/
        `compute_or_load_contracts` yourself and passing them to
        `__init__` directly - offered for the common case where a
        caller has nothing to reuse from a prior build."""
        builder, _tag_matrix = build_pipeline(repo_root)
        contracts = compute_or_load_contracts(builder, repo_root)
        return cls(builder, repo_root, contracts=contracts)

    @property
    def builder(self) -> ConcreteGraphBuilder:
        return self._builder

    @property
    def repo_root(self) -> str:
        return self._repo_root

    def register_pre_traversal_hook(self, callback: PreTraversalHook) -> None:
        """`callback` runs immediately before `retrieve()` calls into
        `build_context_package`, once per `retrieve()` call, in
        registration order. Receives a `QueryContext` - never this
        engine's own live state."""
        self._pre_traversal_hooks.append(callback)

    def register_post_packing_hook(self, callback: PostPackingHook) -> None:
        """`callback` runs immediately after `retrieve()`'s call into
        `build_context_package` returns, once per `retrieve()` call, in
        registration order. Receives the resulting `ContextPackage` -
        a real deep copy, so mutating it (a hook appending to `pkg.
        nodes`, say - `ContextPackage`'s own pydantic `frozen=True`
        only blocks reassigning a field, not mutating a mutable field's
        contents in place) can never affect the object `retrieve()`
        itself returns to its own caller."""
        self._post_packing_hooks.append(callback)

    def retrieve(
        self,
        seed_id: str,
        budget_tokens: int,
        task_type: str | None = None,
        **kwargs: object,
    ) -> ContextPackage:
        query_ctx = QueryContext(
            seed_id=seed_id, repo_root=self._repo_root, budget_tokens=budget_tokens, task_type=task_type,
        )
        for hook in self._pre_traversal_hooks:
            hook(copy.deepcopy(query_ctx))

        pkg = build_context_package(
            self._builder, seed_id, self._repo_root, budget_tokens,
            contracts=self._contracts, task_type=task_type, **kwargs,
        )

        for hook in self._post_packing_hooks:
            hook(copy.deepcopy(pkg))

        return pkg

    # -- Track 2 (Phase B Two-Pass Engine Integration) -- #
    #
    # Graduated from the noise-reduction spike's Approach A v3 (hop=3 +
    # scope-filtered manifest, `experiment/noise-filtering-spike`'s own
    # `benchmarks/experiments/hydration_loop.py`) once it was proven out
    # live: tsr=0.500 (best of the spike), fpr_gt=0.000, mean tokens 91%
    # below the prior v2 manifest - see `reports/spike_noise_reduction_
    # debrief.md` and `docs/design_formalism.md` Sec 10.5. The two turns
    # below are `prism.packer.candidate_index.build_candidate_manifest`
    # (Turn 1) and `prism.surface.build.build_context_package_requested`
    # (Turn 2), the same split `hydration_loop.py` itself established.

    def build_candidate_manifest(
        self,
        seed_id: str,
        max_hops: float = CANDIDATE_INDEX_MAX_HOPS,
        upstream_max_hops: float = DEFAULT_UPSTREAM_MAX_HOPS,
    ) -> tuple[str, set[str]]:
        """Turn 1: `(manifest_text, candidate_universe)` for `seed_id` -
        every symbol reachable within `max_hops` (default 3, the
        spike's own validated floor) and the scope rule, rendered as
        one compact line each. No LLM call happens here or anywhere
        else in this class - see `retrieve_two_pass`'s own docstring."""
        return build_candidate_manifest(self._builder, seed_id, max_hops=max_hops, upstream_max_hops=upstream_max_hops)

    def retrieve_requested(
        self,
        seed_id: str,
        budget_tokens: int,
        requested_symbols: list[str],
        candidate_universe: set[str],
        task_type: str | None = None,
    ) -> tuple[ContextPackage, list[str]]:
        """Turn 2: hydrates whichever of `requested_symbols` resolve
        against `candidate_universe` (Turn 1's own manifest - a name
        that never appeared there is dropped, not rendered; returned as
        the second, `skipped` element) into a real, rendered
        `ContextPackage`, capped to `budget_tokens` by the same
        `_enforce_render_budget` `retrieve()` itself goes through - the
        seed and its direct causal neighbors are never downgraded to a
        signature-only stub even under a tight budget (`prism.surface.
        build._PROTECTED_DOWNGRADE_ROLES`), the fix for the real
        `django_t02_009` @ budget=2000 failure mode the spike diagnosed.
        Goes through the same pre/post hooks `retrieve()` does, so a
        registered hook sees a two-pass query exactly like any other.

        Direct-callee auto-inclusion (pilot-4 autopsy: `t02_001`'s
        `check_response`, `t02_012`'s `set_script_prefix` - both present
        directly in the seed's own `calls=[...]` line, both visible in
        the manifest, neither requested by Turn 1): the seed's own
        direct 1-hop callees (`_outgoing_call_names`, the exact same
        list the manifest's own seed row already shows) that resolve
        against `candidate_universe` are unioned into the hydrated
        request regardless of what Turn 1 asked for - immediate
        control-flow dependencies of the seed are never a Turn-1
        selection judgment call the way a 2+-hop transitive symbol
        legitimately is. `requested_symbols` itself is never mutated
        (the caller's own list, and `retrieve_two_pass`'s own
        `requested_count` diagnostic, must still reflect what Turn 1
        actually requested) - the union is local to this call's own
        pack.
        """
        query_ctx = QueryContext(
            seed_id=seed_id, repo_root=self._repo_root, budget_tokens=budget_tokens, task_type=task_type,
        )
        for hook in self._pre_traversal_hooks:
            hook(copy.deepcopy(query_ctx))

        direct_callees = set(_outgoing_call_names(self._builder, seed_id)) & candidate_universe
        hydration_symbols = list(requested_symbols)
        seen = set(hydration_symbols)
        for callee in sorted(direct_callees - seen):
            hydration_symbols.append(callee)

        pkg, skipped = build_context_package_requested(
            self._builder, seed_id, self._repo_root, budget_tokens, hydration_symbols, candidate_universe,
            contracts=self._contracts, task_type=task_type,
        )

        for hook in self._post_packing_hooks:
            hook(copy.deepcopy(pkg))

        return pkg, skipped

    def retrieve_two_pass(
        self,
        seed_id: str,
        budget_tokens: int,
        request_symbols: RequestSymbolsCallback,
        task_prompt: str = "",
        task_type: str | None = None,
        max_hops: float = CANDIDATE_INDEX_MAX_HOPS,
        upstream_max_hops: float = DEFAULT_UPSTREAM_MAX_HOPS,
    ) -> tuple[ContextPackage, dict[str, object]]:
        """The first-class two-pass execution path: runs Turn 1
        (`build_candidate_manifest`), hands the manifest and
        `task_prompt` to the caller-supplied `request_symbols` callback,
        then runs Turn 2 (`retrieve_requested`) against whatever it
        returns.

        `request_symbols` is always the caller's own - this engine
        never holds or calls an LLM client itself, the same boundary
        `register_pre_traversal_hook`/`register_post_packing_hook`
        already keep (a hook observes, this callback answers, neither
        one lives inside `prism.engine`). A caller wires it to whatever
        model/prompt shape it owns (`benchmarks.tsr.client.
        OpenAICompatibleClient`'s own `complete()` in the spike/harness
        case, an MCP tool's own client elsewhere) and is responsible for
        parsing that model's response into the `list[str]` this
        callback must return - `prism.packer.submodular_knapsack.
        pack_symbol_context_requested` already treats any name absent
        from the candidate universe as hallucinated and drops it, so a
        permissive parse on the caller's side is safe.

        Returns `(pkg, diagnostics)` - `diagnostics` carries
        `candidate_count`, `requested_count`, and `skipped_hallucinated`
        (the exact names, not just a count), useful for the same kind of
        logging `hydration_loop.py`'s own result rows already captured.
        """
        manifest_text, candidate_universe = self.build_candidate_manifest(
            seed_id, max_hops=max_hops, upstream_max_hops=upstream_max_hops,
        )
        requested_symbols = request_symbols(manifest_text, task_prompt)
        pkg, skipped = self.retrieve_requested(
            seed_id, budget_tokens, requested_symbols, candidate_universe, task_type=task_type,
        )
        diagnostics = {
            "candidate_count": len(candidate_universe),
            "requested_count": len(requested_symbols),
            "skipped_hallucinated": skipped,
        }
        return pkg, diagnostics
