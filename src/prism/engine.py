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
from prism.external.index import ExternalSymbolInfo, extract_external_symbol, external_symbol_to_node_entry
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.contracts import BehavioralContract
from prism.packer.candidate_index import CANDIDATE_INDEX_MAX_HOPS, _outgoing_call_names, build_candidate_manifest
from prism.packer.submodular_knapsack import DEFAULT_UPSTREAM_MAX_HOPS, split_budget_for_external
from prism.parser.lang_config import CALL_NODE_TYPE, SELF_TOKEN_TEXT, call_callee_segments, iter_scoped_nodes
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
#:
#: Phase C: a callback may now return either the legacy plain
#: `list[str]` or `(requested_symbols, needs_external_deps)` - see
#: `_normalize_request_result` below. Every callback written before
#: Phase C (every one in `tests/test_two_pass_engine.py`,
#: `benchmarks/run_two_pass_benchmark.py`'s own manual Turn-1 call)
#: already returns the plain form and needs no change.
RequestSymbolsCallback = Callable[[str, str], list[str] | tuple[list[str], bool]]
#: `(external_manifest_text, task_prompt) -> external_requested_symbols`
#: - Turn 2b's own caller-supplied LLM call, exactly the same injection
#: boundary `RequestSymbolsCallback` already establishes for Turn 1.
#: Never invoked when Turn 1 answered `needs_external_deps=False` - see
#: `PrismEngine.retrieve_two_or_three_pass`'s own docstring.
RequestExternalSymbolsCallback = Callable[[str, str], list[str]]


def _normalize_request_result(result: list[str] | tuple[list[str], bool]) -> tuple[list[str], bool]:
    """`RequestSymbolsCallback` backward compatibility (Phase C): a
    legacy callback returning a plain `list[str]` is treated as
    `needs_external_deps=False` - the exact behavior it already had
    before this flag existed, so no existing caller (harness or test)
    needs to change. A Phase-C-aware callback instead returns
    `(requested_symbols, needs_external_deps)`."""
    if isinstance(result, tuple):
        requested_symbols, needs_external_deps = result
        return list(requested_symbols), bool(needs_external_deps)
    return list(result), False


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
        #: Phase C: resolved `ExternalSymbolInfo`s from every
        #: `build_external_candidate_manifest` call this engine instance
        #: has made, keyed by qualified name - so `retrieve_two_or_three_
        #: pass`'s own Turn-3 hydration never re-locates/re-parses a file
        #: `build_external_candidate_manifest` already resolved.
        self._external_symbol_cache: dict[str, ExternalSymbolInfo] = {}

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
        # Phase C: `request_symbols` may now return `(requested_symbols,
        # needs_external_deps)` - `retrieve_two_pass` itself stays a pure
        # two-pass path regardless (a caller wanting the conditional
        # three-pass branch calls `retrieve_two_or_three_pass` instead),
        # so the flag is normalized away here rather than acted on.
        requested_symbols, _needs_external_deps = _normalize_request_result(request_symbols(manifest_text, task_prompt))
        pkg, skipped = self.retrieve_requested(
            seed_id, budget_tokens, requested_symbols, candidate_universe, task_type=task_type,
        )
        diagnostics = {
            "candidate_count": len(candidate_universe),
            "requested_count": len(requested_symbols),
            "skipped_hallucinated": skipped,
        }
        return pkg, diagnostics

    # -- Phase C (external-dependency retrieval) -- #
    #
    # `docs/phase_c_architecture_spec.md` Sections 1 and 3: the
    # conditional three-pass branch and its dedicated external-stub
    # sub-budget, built on Step 1's `prism.external.index` and never
    # invoked at all unless Turn 1 itself asks for it.

    def build_external_candidate_manifest(
        self, turn1_symbols: list[str], root_imports: list[str] | None = None,
    ) -> tuple[str, set[str]]:
        """Turn 2a: every external symbol directly (one-hop) reachable
        from `turn1_symbols`'s own real outgoing call expressions,
        resolved against `root_imports` (Section 2's own Namespace
        Resolution Scope - bounded to the repo's own top-level imported
        packages, never an unbounded installed-package search) via
        `prism.external.index.extract_external_symbol`. No LLM call -
        real static lookup, mirroring `build_candidate_manifest`'s own
        Turn-1 contract.

        Two resolution paths per raw call expression
        (`prism.parser.lang_config.call_callee_segments`), tried in order
        of precision, leaf-only (Section 0's own Non-goal - no external-
        to-external expansion, no deeper receiver-type inference):

          1. The call's own receiver segment names a `root_imports`
             package directly (`orjson.dumps(...)` -> package
             `"orjson"`, symbol `"dumps"`) - the precise case, a real
             per-package correlation, not a guess.
          2. The receiver is a `self`/`this` token
             (`prism.parser.lang_config.SELF_TOKEN_TEXT`) and the leaf
             name has zero real in-repo candidates
             (`GlobalSymbolTable.candidates_for_simple_name` - that
             table's own docstring already calls this shape "an
             ordinary external/builtin reference") - the real `t018`
             case (a Starlette-inherited method Prism's own symbol
             table has no entry for): the bare leaf name is tried
             against every `root_imports` package in order, first
             resolvable match wins.

        A three-or-more-segment receiver chain (`self.router.add_route`)
        is left unresolved rather than guessed at - the same leaf-only
        discipline, not a deeper attribute-chain resolution this method
        doesn't attempt. Every resolved `ExternalSymbolInfo` is cached on
        this engine instance (`self._external_symbol_cache`, keyed by
        qualified name) so Turn 3's own hydration below never re-locates
        or re-parses a file this step already resolved.
        """
        root_imports = list(root_imports or [])
        if not root_imports:
            return "<external_candidate_index>\n</external_candidate_index>", set()

        self_tokens: set[str] = set()
        for tokens in SELF_TOKEN_TEXT.values():
            self_tokens |= tokens

        resolved: dict[str, ExternalSymbolInfo] = {}
        attempted: set[tuple[str, str]] = set()
        for qname in turn1_symbols:
            info = self._builder.symbol_table.get(qname)
            def_node = self._builder.def_node(qname)
            if info is None or def_node is None:
                continue
            parsed = self._builder.parsed_file(info.file)
            if parsed is None:
                continue
            lang = parsed.language_id
            call_type = CALL_NODE_TYPE.get(lang)
            if call_type is None:
                continue
            for call_node in iter_scoped_nodes(def_node, {call_type}, lang):
                segments = call_callee_segments(call_node, parsed.source, lang)
                if not segments or len(segments) != 2:
                    continue
                receiver, leaf = segments
                candidate_packages: list[str] = []
                if receiver in root_imports:
                    candidate_packages = [receiver]
                elif receiver in self_tokens and not self._builder.symbol_table.candidates_for_simple_name(leaf):
                    candidate_packages = root_imports
                for package_name in candidate_packages:
                    key = (package_name, leaf)
                    if key in attempted:
                        continue
                    attempted.add(key)
                    ext_info = extract_external_symbol(package_name, leaf)
                    if ext_info is not None:
                        resolved[ext_info.qualified_name] = ext_info
                        break

        self._external_symbol_cache.update(resolved)
        lines = [f"{info.qualified_name}|external|{info.kind}|{info.signature_text}" for info in sorted(resolved.values(), key=lambda i: i.qualified_name)]
        manifest_text = "<external_candidate_index>\n" + "\n".join(lines) + ("\n" if lines else "") + "</external_candidate_index>"
        return manifest_text, set(resolved.keys())

    def retrieve_two_or_three_pass(
        self,
        seed_id: str,
        budget_tokens: int,
        request_symbols: RequestSymbolsCallback,
        request_external_symbols: RequestExternalSymbolsCallback | None = None,
        root_imports: list[str] | None = None,
        task_prompt: str = "",
        task_type: str | None = None,
        max_hops: float = CANDIDATE_INDEX_MAX_HOPS,
        upstream_max_hops: float = DEFAULT_UPSTREAM_MAX_HOPS,
    ) -> tuple[ContextPackage, dict[str, object]]:
        """The conditional three-pass execution path (`docs/phase_c_
        architecture_spec.md` Section 1). Turn 1 is identical to
        `retrieve_two_pass`'s own (`build_candidate_manifest` + one call
        to `request_symbols`), then branches on the `needs_external_deps`
        half of its answer (`_normalize_request_result` - a legacy
        `list[str]`-returning callback always takes the `False` branch):

        `needs_external_deps=False`: **zero overhead** - runs exactly the
        same standard two-pass path `retrieve_two_pass` does (the full
        `budget_tokens`, no sub-budget split), and `request_external_
        symbols`/`build_external_candidate_manifest` are never called at
        all. Every `external_*` diagnostic is reported as its own empty/
        zero value, matching a caller with no external dependency
        support (Section 1.3's own "unchanged, for a caller with no
        external_index" guarantee).

        `needs_external_deps=True`: reserves a dedicated external
        sub-budget up front (`split_budget_for_external`) so external
        stubs can never evict or outbid an internal node - the internal
        Turn 2 hydration (`retrieve_requested`, unmodified) runs against
        `internal_budget` only. Turn 2a (`build_external_candidate_
        manifest`, no LLM call) resolves external candidates reachable
        from the seed plus whatever Turn 1 requested; Turn 2b calls the
        caller-supplied `request_external_symbols` **only when that
        candidate set is non-empty** (an empty manifest has nothing to
        select from - no wasted LLM call). Turn 3 resolves each
        requested name against `self._external_symbol_cache` (populated
        by Turn 2a - no second parse), admits it as a real
        `role="external"` `NodeEntry` while its running cost still fits
        `external_budget`, and appends every admitted node to the
        internally-packed `ContextPackage` (`model_copy`, since
        `ContextPackage` is frozen). A requested name absent from the
        cache (hallucinated) or that no longer fits the remaining
        external budget is reported in `external_skipped_hallucinated`,
        never silently dropped.

        `request_external_symbols` is required whenever Turn 1 itself
        answers `needs_external_deps=True` - a caller wiring Phase C in
        at all is expected to always pass it; raises `ValueError`
        immediately rather than silently treating a missing Turn-2b
        callback as "no external symbols requested" (a real caller
        contract violation, not a legitimate degrade path - `root_imports`
        being empty/`None` is the legitimate "nothing to resolve" case,
        already handled by `build_external_candidate_manifest` itself).

        Returns `(pkg, diagnostics)` - `diagnostics` carries every key
        `retrieve_two_pass` already returns, plus `needs_external_deps`,
        `external_candidate_count`, `external_requested_count`, and
        `external_skipped_hallucinated`.

        Hook timing note: `register_post_packing_hook` callbacks fire
        exactly once, inside the internal `retrieve_requested` call above
        - on the three-pass branch they see the internally-packed
        `ContextPackage` *before* Turn 3 appends external nodes, not the
        final combined package this method returns. Widening that
        contract (a second hook pass over the combined package, or
        deferring the existing one) is not needed for Step 2's own scope
        and is left for whichever later step first has a real hook
        consumer that cares about seeing external nodes.
        """
        manifest_text, candidate_universe = self.build_candidate_manifest(
            seed_id, max_hops=max_hops, upstream_max_hops=upstream_max_hops,
        )
        requested_symbols, needs_external_deps = _normalize_request_result(request_symbols(manifest_text, task_prompt))

        if not needs_external_deps:
            pkg, skipped = self.retrieve_requested(
                seed_id, budget_tokens, requested_symbols, candidate_universe, task_type=task_type,
            )
            diagnostics = {
                "candidate_count": len(candidate_universe),
                "requested_count": len(requested_symbols),
                "skipped_hallucinated": skipped,
                "needs_external_deps": False,
                "external_candidate_count": 0,
                "external_requested_count": 0,
                "external_skipped_hallucinated": [],
            }
            return pkg, diagnostics

        if request_external_symbols is None:
            raise ValueError(
                "request_symbols answered needs_external_deps=True but no request_external_symbols "
                "callback was supplied to retrieve_two_or_three_pass"
            )

        internal_budget, external_budget = split_budget_for_external(budget_tokens)
        pkg, skipped = self.retrieve_requested(
            seed_id, internal_budget, requested_symbols, candidate_universe, task_type=task_type,
        )

        turn1_symbols = sorted({seed_id, *requested_symbols})
        external_manifest_text, external_candidate_universe = self.build_external_candidate_manifest(
            turn1_symbols, root_imports=root_imports,
        )
        if external_candidate_universe:
            external_requested_symbols = list(request_external_symbols(external_manifest_text, task_prompt))
        else:
            external_requested_symbols = []

        external_skipped: list[str] = []
        external_nodes = []
        running_cost = 0
        for name in external_requested_symbols:
            info = self._external_symbol_cache.get(name)
            if info is None:
                external_skipped.append(name)
                continue
            node = external_symbol_to_node_entry(info)
            if running_cost + node.cost > external_budget:
                external_skipped.append(name)
                continue
            running_cost += node.cost
            external_nodes.append(node)

        pkg = pkg.model_copy(update={"nodes": [*pkg.nodes, *external_nodes]})

        diagnostics = {
            "candidate_count": len(candidate_universe),
            "requested_count": len(requested_symbols),
            "skipped_hallucinated": skipped,
            "needs_external_deps": True,
            "external_candidate_count": len(external_candidate_universe),
            "external_requested_count": len(external_requested_symbols),
            "external_skipped_hallucinated": external_skipped,
        }
        return pkg, diagnostics
