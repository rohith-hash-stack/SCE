"""Stage 4: the Constrained Context Knapsack (HLD section 2.3 / 4.3).

Greedily packs the seed symbol (pinned at L0) plus its nearest neighbors -
ordered by `D_hybrid` - into a token budget, downgrading resolution (L1 ->
L2 -> L3) for any candidate that doesn't fit until the budget is exhausted.

A candidate reached via a `confidence="CONFIRMED_RUNTIME"` edge (see
`prism.runtime.reconciler` - a real execution actually traversed it, not
just static inference) is preferentially packed over an equal-or-lesser-
priority unexercised static candidate: `D_hybrid` itself already discounts
a confirmed edge's hop cost (`DistanceEngine.compute_all`), and `pack`
below breaks any remaining tie explicitly via
`DistanceEngine.confirmed_runtime_reachable`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import networkx as nx

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.contracts import BehavioralContract
from prism.slicer.compressor import (
    DYNAMIC_EDGE_SENTINEL_RESOLUTION,
    INFALLIBLE_SIGNATURE_RESOLUTION,
    UNRESOLVED_POLYMORPHIC_RESOLUTION,
    ASTCompressor,
    CompressionContext,
    is_infallible,
    render_dynamic_edge_sentinel,
    render_infallible_signature,
    render_unresolved_polymorphic,
)
from prism.slicer.distance import DistanceEngine, architectural_path
from prism.slicer.tokenizer import count_tokens

#: `--query-type` values `prism.cli`'s `query` command accepts (see that
#: module). Fallibility-based pruning (below) is active unconditionally -
#: a token budget is always present when packing at all, satisfying the
#: spec's own "when operating under a budget constraint **or** when
#: --query-type bug_localization is specified" as an always-true first
#: clause - but `bug_localization` is still accepted and threaded through
#: explicitly, both to document intent at the call site and as the hook
#: a future, more aggressive bug-localization-specific heuristic would
#: extend rather than needing to invent this plumbing from scratch.
QUERY_TYPE_GENERAL = "general"
QUERY_TYPE_BUG_LOCALIZATION = "bug_localization"

# Illustrative resolution weights used only to report a deterministic
# "preserved semantics" figure: how much of the theoretically-available
# detail (every reachable candidate rendered at full L0) survived
# compression and the token budget. Not part of the HLD's formal knapsack
# objective - purely a reporting metric for the serialized output.
RESOLUTION_WEIGHT = {0: 1.0, 1: 0.7, 2: 0.4, 3: 0.15}

# Issues #11/#13: real BPE token counts (`prism.slicer.tokenizer`,
# preferring tiktoken's cl100k_base encoding, degrading to a deterministic
# regex approximation only if that encoding table can't be loaded) instead
# of a flat word-count heuristic. The heuristic this replaced
# (`len(text.split()) * 2.6`) was measured against a real punctuation-aware
# tokenizer across four different packed contexts and still drifted
# systematically - source code tokenizes far denser than prose (BPE splits
# most punctuation, brackets, and operators into their own tokens), and
# that density itself varies by language (Python vs. Go vs. near-binary
# formats like minified JSON), so no single fixed per-word ratio could
# ever track it closely. `estimate_tokens` keeps its name (every call site
# across this module, `serializers.markdown`, and `slicer.blueprint`
# already calls it) but its body is now real, not proxy, token counting.
def estimate_tokens(text: str) -> float:
    return float(count_tokens(text))


_RESOLUTION_LABELS_FOR_WRAPPING = {0: "L0", 1: "L1", 2: "L2", 3: "L3"}


def _wrapping_overhead_tokens(
    symbol: str, relative_path: str, resolution: int = 0, line_range: tuple[int, int] = (0, 0), is_seed: bool = False
) -> float:
    """Real (not approximated) token cost of the Markdown heading +
    fenced-code-block wrapper `serializers.markdown.render_markdown` puts
    around one packed item's bare `content` - a `### <symbol>
    (<label>)`/`### [TARGET] <symbol> (<label>)` heading plus a fenced
    code block. Without this, the packer's running total only ever counts
    the code itself and silently diverges from the size of the document
    it's actually producing - a gap that's tiny for one item but compounds
    badly once dozens or hundreds of small (L2/L3) items are packed,
    exactly what happens against a real, densely-connected repository.

    Every field this needs (the item's own real `line_range`/
    `relative_path`/resolution/seed-ness) is already known at every call
    site below by the time cost is computed, so - unlike the word-count-
    heuristic version this replaces, which used a "99999-99999" stand-in
    line range purely because it was cheaper to write - there's no reason
    left not to render the *real* heading text and count its *real* BPE
    tokens. This still doesn't attempt the YAML-contract-block-vs-code-
    fence branching `render_markdown` itself does (importing that logic
    back here would be circular - `serializers.markdown` already imports
    `PackResult` from this module) - a code fence is assumed, which is the
    larger of the two wrapper shapes, keeping this a conservative
    (never-under-counts-the-wrapper) approximation of that one remaining
    piece rather than a byte-exact one.
    """
    label = _RESOLUTION_LABELS_FOR_WRAPPING.get(resolution, "L0")
    start, end = line_range
    full_label = f"{label} - lines {start}-{end} in {relative_path}"
    prefix = "[TARGET] " if is_seed else ""
    heading = f"### {prefix}{symbol} ({full_label})"
    return estimate_tokens(f"{heading}\n```python\n```\n")


@dataclass
class PackedItem:
    symbol: str
    resolution: int
    content: str
    language_id: str = "python"
    # The symbol's real, original location - unaffected by however much
    # `content` itself was compressed/skeletonized - so a reader can always
    # ground a packed item back to an exact place in the real source, even
    # at L1-L3 where the rendered text is no longer a literal slice.
    line_range: tuple[int, int] = (0, 0)
    relative_path: str = ""


@dataclass
class PackResult:
    seed: str
    budget: int
    allocated_tokens: float
    items: list[PackedItem] = field(default_factory=list)
    architectural_path: list[tuple[int, str | None, str]] = field(default_factory=list)
    preserved_semantics: float = 0.0
    # Adaptive Compact Scaffolding: True when the seed's own call-chain
    # neighborhood is small enough that Prism's normal scaffolding (the
    # Architectural Path diagram, per-item line-range/path headers,
    # multi-line contract blocks) would cost more tokens than it's worth -
    # see `ContextKnapsackPacker._detect_compact_mode`. The serializer
    # (`prism.serializers.markdown`) reads this to render a denser document.
    compact: bool = False
    #: Fractional-knapsack LP relaxation upper bound (Issue #12.3) - a
    #: diagnostic-only estimate of the best achievable "preserved
    #: semantics" value under this budget, for comparison against the
    #: real (integer 0/1, resolution-tiered) result above. Never used to
    #: gate admission itself.
    fractional_upper_bound: float = 0.0
    #: How many Swap-Refinement Pass substitutions (Issue #12.2) actually
    #: fired - a more-relevant candidate the main greedy pass left
    #: unselected displacing a less-relevant one it had already packed.
    swaps_performed: int = 0
    #: Issue A3, updated by Item 18 (Progressive Seed Degradation): real
    #: token cost of the seed's own rendering *at whatever compression
    #: level it was actually packed at* - see `seed_compression_level`
    #: below - not always its L0 cost anymore. The one quantity
    #: `budget_exceeded` below is computed from. Always populated (never
    #: 0 unless the seed genuinely renders to nothing), independent of
    #: whether it actually exceeds `budget`.
    seed_cost: float = 0.0
    #: Item 18: which of L0/L1/L2/L3 the seed actually ended up packed
    #: at, after `ContextKnapsackPacker._degrade_seed_to_fit` tries each
    #: tier in order and stops at the first that fits `budget` (or L3,
    #: the last resort, regardless of fit). 0 (L0, full source) is the
    #: common case; > 0 means the rendered `PackedItem` for the seed
    #: carries an explicit `/* Warning: ... */` notice a reader can see
    #: directly, in addition to this metadata field.
    seed_compression_level: int = 0
    #: Issue A3, redefined by Item 18: True exactly when even the
    #: seed's minimal L3 stub - the smallest representation this class
    #: ever renders, and the last tier `_degrade_seed_to_fit` tries -
    #: still exceeds `budget`. Before Item 18 this fired at L0 alone
    #: (the seed was never compressed to fit); now it is the genuinely
    #: last-resort case, since L1/L2/L3 degradation absorbs everything
    #: short of that. `docs/design_formalism.md` SS4.4 documents this as
    #: the one deliberate, unconditional exception to Strict Budget
    #: Compliance: Tokens(Rendered) <= Budget OR Tokens(Seed_L3) >
    #: Budget. A caller (the MCP server, the CLI, any downstream
    #: consumer) should surface this explicitly rather than silently
    #: accept an over-budget response - the whole reason this flag
    #: exists instead of leaving the overflow implicit in
    #: `allocated_tokens > budget`.
    budget_exceeded: bool = False
    #: Item 18: alias for `budget_exceeded`, matching the audit's own
    #: naming for this specific (now genuinely rare - only when even an
    #: L3 stub doesn't fit) last-resort case. Always equal to
    #: `budget_exceeded` - kept as a separate field rather than a
    #: property so both names are visible on a plain dataclass
    #: (`asdict`, JSON serialization, ...) without special-casing.
    fatal_seed_overflow: bool = False
    #: Item 20: `sum(Score(Selected)) / fractional_upper_bound` - see
    #: `ContextKnapsackPacker._knapsack_efficiency_ratio`'s own docstring.
    #: Diagnostic only.
    knapsack_efficiency_ratio: float = 1.0
    #: Item 20: set when `knapsack_efficiency_ratio` falls below
    #: `ContextKnapsackPacker.EFFICIENCY_RATIO_WARNING_THRESHOLD` (0.75) -
    #: a signal of high item fragmentation (many small, awkwardly-sized
    #: candidates the greedy/swap-refine passes couldn't pack
    #: efficiently), surfaced the same way `RuntimeTrust`'s own low-trust
    #: warning is (`prism.runtime.reconciler`) - a human-readable string
    #: when the condition holds, `None` otherwise.
    knapsack_efficiency_warning: str | None = None
    #: Issue A3: True when the final rendered package exceeds `budget` for
    #: any reason - computed independently from `allocated_tokens >
    #: budget` rather than aliased to `budget_exceeded`, even though in
    #: this architecture the two are currently always equal (every
    #: candidate beyond the seed is strictly admission-gated against
    #: `_admission_budget <= budget`, so the seed is the only possible
    #: source of overflow today). Kept distinct because the two questions
    #: are conceptually different ("did the mandatory seed alone not fit"
    #: vs. "did the document Prism actually produced not fit") and a
    #: future admission path that isn't seed-only should not need to
    #: retrofit this flag's meaning.
    truncation_occurred: bool = False


class ContextKnapsackPacker:
    # Word-count-based estimation still carries real error even after
    # calibrating TOKENS_PER_WORD against measured samples (see its
    # comment) - packing candidates only up to this fraction of the
    # nominal budget leaves headroom to absorb that error, so the actual
    # rendered document (measured by a real tokenizer) usually still lands
    # at or under what the caller asked for. `PackResult.budget` still
    # reports the true nominal budget; this only tightens the internal
    # admission threshold.
    SAFETY_MARGIN = 0.92

    # Adaptive Compact Scaffolding thresholds: below either one, the normal
    # verbose scaffold (architectural-path diagram, per-item line-range/path
    # headers, multi-line L2 contracts) costs more tokens than it delivers
    # in value, and can even make Prism's own package *larger* than a naive
    # whole-file dump of the same small neighborhood - the opposite of the
    # point. `COMPACT_MODE_RAW_FOOTPRINT_TOKENS` mirrors the threshold a
    # human would eyeball ("this is a small, few-file corner of the repo");
    # `COMPACT_MODE_MAX_ACTIVE_NODES` catches the case where a target has a
    # tiny call-chain footprint even if its file happens to be large.
    COMPACT_MODE_RAW_FOOTPRINT_TOKENS = 1200
    COMPACT_MODE_MAX_ACTIVE_NODES = 3

    #: A calibrated reserve for content the serializer (`prism.serializers.
    #: markdown`) renders around the packed items but that never passes
    #: through this class's own per-item `estimate_tokens` accounting at
    #: all - the "1. REPOSITORY INTENT PROFILE"/"2. SUBSYSTEM CONTRACT"/
    #: "3. ACTIVE EXECUTION PIPELINE"/"4. TARGET SYMBOL & SINK-AWARE
    #: CONTRACTS" hierarchical block (`hierarchy=` in `render_markdown`)
    #: and the "Idiomatic Blueprint" section (`blueprint=`) are both
    #: computed and rendered by the *caller*, after `pack()` already
    #: returned, specifically so this class doesn't need to depend on
    #: `prism.graph.hierarchy`/`prism.slicer.blueprint` just to report a
    #: token count - but a caller that knows it's going to render either
    #: (`prism.cli`'s `query` command does, always) should pass
    #: `reserved_overhead_tokens` so the *rendered document* actually
    #: respects the budget the user asked for, not just the packed-items
    #: portion of it. 700 is a measured calibration (a real hierarchical-
    #: block + blueprint rendering came out to ~695 tokens against a
    #: production repository during this benchmark's own re-verification
    #: run), not a guess - see `tests/test_fallibility_pruning.py`/
    #: `benchmarks/comparison_report.md`'s own C3-05 note for the
    #: regression this fixes.
    DEFAULT_RESERVED_OVERHEAD_TOKENS = 2400

    #: Fallibility-Based Knapsack Pruning's own "leaf" threshold - see the
    #: comment where it's used, in the candidate-packing loop below.
    SHALLOW_OUT_DEGREE = 2

    #: Data-Flow Centrality threshold (Issue #10.2):
    #: DataFlowCentrality(v) = |{(u,v) in E | v is an argument or return
    #: value of u}| - proxied here by how many distinct incoming CALLS
    #: edges captured `v`'s own return value into a variable
    #: (`bound_to is not None` on the edge's own CallSiteContext - a real,
    #: already-tracked data-flow-significant use, as opposed to a bare
    #: fire-and-forget call whose result nothing downstream touches). A
    #: node this many callers depend on for real data (not just control
    #: flow) is never compressed below Level 1 (Pruned) regardless of how
    #: far it sits from the seed - a distant node whose actual return
    #: value threads through several call sites is exactly the kind of
    #: thing a debugging agent needs to see the real arguments/values of.
    DATA_FLOW_CENTRALITY_THRESHOLD = 2

    def __init__(
        self,
        token_budget: int,
        compressor: ASTCompressor | None = None,
        distance_engine: DistanceEngine | None = None,
        reserved_overhead_tokens: int = 0,
    ) -> None:
        self.budget = token_budget
        # Never let a reserve zero out packing entirely - a caller passing
        # a small `--budget` alongside the default reserve should still
        # get *something* pinned (the seed itself, at minimum), not an
        # empty package. The floor is deliberately well below the naive
        # "half the nominal budget" a first cut used - that floor turned
        # out to swallow `DEFAULT_RESERVED_OVERHEAD_TOKENS` entirely once
        # it grew past `token_budget * 0.5`, silently capping every
        # reserve above that at the same effective budget regardless of
        # how much higher it was configured (confirmed during this
        # benchmark's own re-verification: raising the reserve past ~2000
        # against a 4000 budget produced zero further reduction until this
        # floor was lowered).
        MIN_EFFECTIVE_BUDGET_RATIO = 0.2
        effective_budget = max(token_budget - reserved_overhead_tokens, token_budget * MIN_EFFECTIVE_BUDGET_RATIO)
        self._admission_budget = effective_budget * self.SAFETY_MARGIN
        self.compressor = compressor or ASTCompressor()

    def pack(
        self,
        seed: str,
        builder: ConcreteGraphBuilder,
        tag_matrix: dict[str, set[str]],
        distance_engine: DistanceEngine,
        contracts: dict[str, BehavioralContract] | None = None,
        query_type: str = QUERY_TYPE_GENERAL,
    ) -> PackResult:
        # `calls_graph`, not `graph`: the packer's own notion of "reachable
        # from the seed" must stay scoped to real behavioral call/construct
        # edges, not the richer EXTENDS/IMPLEMENTS/READS_STATE relations
        # `graph` also carries - see `ConcreteGraphBuilder.calls_graph`'s
        # docstring for the regression this avoids.
        g_c = builder.calls_graph
        distances = distance_engine.compute_all(seed, g_c)
        # `compute_all` already discounts a `confidence="CONFIRMED_RUNTIME"`
        # edge's hop cost (see `DistanceEngine._weighted_undirected`), so a
        # runtime-confirmed path's distance value is already lower than an
        # equal-length static-only one - `confirmed` below is the same
        # signal made explicit and rigorous (which *nodes*, not just
        # smaller numbers), used as an admission-order tie-breaker so two
        # candidates the discount alone left at a near-identical distance
        # still resolve in favor of the one actual execution validated.
        confirmed = distance_engine.confirmed_runtime_reachable(seed, g_c)
        reachable = self._directed_reachable(g_c, seed)
        compact = self._is_compact_mode(builder, reachable)

        seed_symbol = builder.symbol_table.get(seed)
        if seed_symbol is None:
            raise ValueError(f"Seed symbol '{seed}' was not found in the concrete graph (unknown or external symbol)")
        seed_path = self._relative_path(builder, seed_symbol.file)
        # Item 18 (third post-implementation audit) / Progressive Seed
        # Degradation: the seed is still always packed *unconditionally*
        # (never excluded outright - Invariant #4, Seed Dominance; no `if
        # cost > self._admission_budget: ...` guard, checked against the
        # raw `self.budget` rather than the reduced `_admission_budget`,
        # same as Issue A3's original treatment), but is no longer always
        # rendered at L0 regardless of budget - see `_degrade_seed_to_fit`.
        seed_compression_level, seed_content, seed_cost = self._degrade_seed_to_fit(
            builder, tag_matrix, seed, seed_symbol, seed_path, compact
        )
        items = [PackedItem(seed, seed_compression_level, seed_content, seed_symbol.language_id, seed_symbol.line_range, seed_path)]
        # `budget_exceeded`/`fatal_seed_overflow` (Item 18 renames Issue
        # A3's single `budget_exceeded` meaning) are now True only in the
        # truly last-resort case: even the minimal L3 stub - the smallest
        # representation this class ever renders - doesn't fit `budget`.
        # See docs/design_formalism.md SS4.4 for the updated formal
        # statement of Strict Budget Compliance's seed exception.
        fatal_seed_overflow = seed_cost > self.budget
        budget_exceeded = fatal_seed_overflow
        total_tokens = seed_cost
        packed: set[str] = {seed}
        # Swap-Refinement Pass (Issue #12) only ever displaces an
        # ordinary, distance-ranked candidate - the seed, an
        # architectural-path "requires" obligation, and an already-minimal
        # infallible-leaf/sentinel item are exempt, each for its own
        # already-established reason (the seed always stays at L0; a
        # "requires" item is a confirmed metamodel obligation, not merely
        # distance-ranked; an infallible-leaf/sentinel item is already at
        # (near-)zero cost, so displacing it saves little and destroys a
        # deliberately-cheap signal).
        protected: set[str] = {seed}

        # A "requires" hop in the architectural path names a confirmed
        # metamodel obligation (e.g. "this #db_write requires an
        # #auth_guard") that the seed's own call graph does *not* reach -
        # that's what makes it worth flagging in the first place. Pack a
        # real L2 contract for it before ranking anything else: without
        # this, it would surface only as a bare qualified name in the
        # Architectural Path section with no signature or import path to
        # act on, which - confirmed against a live model - gets treated as
        # if the dotted path itself were valid, callable Python
        # (`app.auth.verify_session(...)` instead of `verify_session(...)`)
        # rather than a hint to import and call the real function.
        path = architectural_path(seed, g_c, tag_matrix, distance_engine.metamodel)
        for _depth, relation, node in path:
            if relation != "requires" or node in packed:
                continue
            symbol = builder.symbol_table.get(node)
            if symbol is None or symbol.kind not in ("function", "method"):
                continue
            content = self._render(builder, tag_matrix, node, 2, compact)
            if content is None:
                continue
            node_path = self._relative_path(builder, symbol.file)
            cost = estimate_tokens(content) + _wrapping_overhead_tokens(node, node_path, 2, symbol.line_range)
            if total_tokens + cost > self._admission_budget:
                continue
            items.append(PackedItem(node, 2, content, symbol.language_id, symbol.line_range, node_path))
            total_tokens += cost
            packed.add(node)
            protected.add(node)

        # Exception/data classes are surfaced inline via a function's own
        # "Raises:"/signature contract; packing them as separate context
        # blocks would just repeat that information, so only functions and
        # methods compete for knapsack slots. In compact mode, candidates
        # are further scoped to the seed's own directed call-chain closure
        # (`reachable`) - the same footprint a raw whole-file dump would
        # cover - rather than the wider undirected neighborhood `distances`
        # spans (siblings, callers, anything sharing a distant tag). Without
        # this, trimming the scaffold alone doesn't actually guarantee a
        # small package matches or beats raw size: a small seed can still
        # have plenty of undirected neighbors (e.g. its own callers) that a
        # raw dump of *its* call chain would never have included at all.
        candidates = sorted(
            (
                node
                for node in distances
                if node not in packed
                and (not compact or node in reachable)
                and (
                    ((symbol := builder.symbol_table.get(node)) is not None and symbol.kind in ("function", "method"))
                    or self._sentinel_type(g_c, node) is not None
                )
            ),
            # Runtime-confirmed candidates sort first at a tied (or
            # near-tied, post-discount) distance - see the `confirmed`
            # comment above `pack`'s own call to `compute_all`.
            key=lambda n: (distances[n], 0 if n in confirmed else 1),
        )

        for node in candidates:
            sentinel_type = self._sentinel_type(g_c, node)
            if sentinel_type is not None:
                # Resolution Boundary Sentinels (Tasks 1-3): an
                # `UnresolvedPolymorphicNode`/`DynamicEdgeSentinel` never
                # goes through L0-L3 tier stepping or the infallible-leaf
                # branch below - it has no `BehavioralContract`/def_node to
                # render from at all - and always serializes as the same
                # compact, fixed-format diagnostic regardless of how close
                # it sits to the seed ("high-priority, minimal-weight",
                # per spec). It's also a true terminal leaf: nothing ever
                # adds an outgoing edge from it (see
                # `ConcreteGraphBuilder._emit_dynamic_edge_sentinel`/
                # `_resolve_ambiguous_call`), so no further traversal
                # guard is needed here either.
                data = g_c.nodes[node]
                content = self._render_sentinel(data, tag_matrix, sentinel_type)
                resolution = (
                    UNRESOLVED_POLYMORPHIC_RESOLUTION
                    if sentinel_type == "unresolved_polymorphic"
                    else DYNAMIC_EDGE_SENTINEL_RESOLUTION
                )
                cost = estimate_tokens(content)
                if total_tokens + cost > self._admission_budget:
                    break
                line = data.get("call_site_line", 0)
                items.append(PackedItem(
                    node, resolution, content, "text", (line, line), data.get("call_site_file", ""),
                ))
                total_tokens += cost
                packed.add(node)
                protected.add(node)
                continue

            node_symbol = builder.symbol_table.get(node)
            node_path = self._relative_path(builder, node_symbol.file)

            # Fallibility-Based Knapsack Pruning: a shallow call-chain
            # node (`out_degree <= SHALLOW_OUT_DEGREE` in `calls_graph` -
            # a true leaf, or a thin wrapper delegating to at most a
            # couple of trivial helpers of its own - "leaf" in the
            # spec's own paradigm-case sense, widened past a literal
            # `out_degree == 0` after this benchmark's own C3-05 re-
            # verification measured that a strict leaf-only definition
            # left too many provably-safe helpers - `TErr`,
            # `fstring_contains_expr`, `passes_all_checks`, ... in
            # black's own `trans.py` - still paying full L1/L2 rendering
            # cost purely because each happened to make 1-2 calls of its
            # own) whose own `BehavioralContract` proves it Infallible
            # (pure, simple, no throws, no effects - see
            # `prism.slicer.compressor.is_infallible`) never earns full
            # AST source or a multi-line contract block, regardless of how
            # close it sits to the seed: token budget is reserved for
            # nodes that could actually be the root cause of whatever the
            # query is investigating. Packing is *always* budget-
            # constrained (a finite `self.budget` exists on every call),
            # so this applies unconditionally - `query_type` is accepted
            # and threaded through (see `QUERY_TYPE_BUG_LOCALIZATION`) for
            # future extension, not as a second gate on top of this one.
            infallible_leaf = (
                contracts is not None
                and g_c.out_degree(node) <= self.SHALLOW_OUT_DEGREE
                and is_infallible(contracts.get(node), tag_matrix.get(node))
            )
            if infallible_leaf:
                return_type = contracts[node].return_type
                content = render_infallible_signature(node, return_type)
                cost = estimate_tokens(content)  # no heading/fence wrapper at all - see the serializer
                if total_tokens + cost > self._admission_budget:
                    break
                items.append(PackedItem(
                    node, INFALLIBLE_SIGNATURE_RESOLUTION, content,
                    node_symbol.language_id, node_symbol.line_range, node_path,
                ))
                total_tokens += cost
                # `packed`/`protected` were previously never updated here -
                # harmless on its own (this loop never revisits the same
                # `node` twice within one `pack()` call regardless), but a
                # real latent gap once the Swap-Refinement Pass (Issue #12)
                # started treating "not in packed" as "eligible to (re-)pack"
                # - without this, an already-packed infallible-leaf node
                # could get packed a *second* time as a swap-in candidate.
                packed.add(node)
                protected.add(node)
                continue

            target_res = distance_engine.resolution_for_distance(distances[node])
            # Data-Flow Centrality (Issue #10.2): a node enough distinct
            # callers actually depend on for its *return value* (not just
            # a control-flow hop) is never compressed below Level 1
            # (Pruned) - a hard floor on the downgrade loop just below,
            # not merely a starting point it can still slide past under
            # budget pressure. If even Level 1 doesn't fit, such a node is
            # excluded entirely rather than further degraded - matching
            # the audit's own "never compressed below Level 1" wording
            # literally, not just "prefer not to".
            min_resolution = (
                1 if self._data_flow_centrality(g_c, node) >= self.DATA_FLOW_CENTRALITY_THRESHOLD else 3
            )
            target_res = min(target_res, min_resolution) if min_resolution == 1 else target_res
            content = self._render(builder, tag_matrix, node, target_res, compact)
            if content is None:
                continue
            # Resolution-invariant: L0-L3's label text is always the same
            # length ("L0".."L3"), so the wrapper's own token cost doesn't
            # actually change as `target_res` steps up in the downgrade
            # loop below - computed once, not recomputed per iteration.
            overhead = _wrapping_overhead_tokens(node, node_path, target_res, node_symbol.line_range)
            cost = estimate_tokens(content) + overhead
            while total_tokens + cost > self._admission_budget and target_res < min_resolution:
                target_res += 1
                content = self._render(builder, tag_matrix, node, target_res, compact)
                cost = estimate_tokens(content) + overhead
            if total_tokens + cost <= self._admission_budget:
                items.append(PackedItem(node, target_res, content, node_symbol.language_id, node_symbol.line_range, node_path))
                total_tokens += cost
                packed.add(node)
            elif min_resolution == 1:
                # A high-data-flow-centrality node that doesn't even fit
                # at its Level 1 floor is skipped, not further degraded -
                # continue to the next (lower-priority) candidate instead
                # of aborting the whole remaining pass over it alone.
                continue
            else:
                break

        # Swap-Refinement Pass (Issue #12): the main greedy loop above
        # admits candidates strictly in distance order and stops the
        # instant one doesn't fit even at L3 - which can leave a
        # still-unselected, more-relevant candidate further down the
        # (sorted) list starved out entirely, while a less-relevant one
        # that happened to fit earlier keeps its slot. This is a bounded
        # cleanup pass, not a second admission loop: it only ever
        # displaces an ordinary candidate (never the seed, a "requires"
        # obligation, or an already-minimal infallible-leaf/sentinel item
        # - see `protected`), and only when the incoming candidate is
        # strictly closer than the outgoing one.
        items, total_tokens, swaps_performed = self._swap_refine(
            items, total_tokens, candidates, packed, protected, distances, builder, tag_matrix, compact,
        )

        fractional_upper_bound = self._fractional_relaxation_bound(candidates, distances)
        efficiency_ratio = self._knapsack_efficiency_ratio(items, seed, distances, fractional_upper_bound)
        efficiency_warning = (
            f"Knapsack efficiency ratio {efficiency_ratio:.2f} is below the "
            f"{self.EFFICIENCY_RATIO_WARNING_THRESHOLD:.2f} warning threshold - high item "
            "fragmentation (many small, awkwardly-sized candidates the greedy/swap-refine "
            "passes couldn't pack efficiently against the fractional-relaxation upper bound)."
            if efficiency_ratio < self.EFFICIENCY_RATIO_WARNING_THRESHOLD
            else None
        )

        preserved = self._preserved_semantics(items, candidates)
        return PackResult(
            seed=seed,
            budget=self.budget,
            allocated_tokens=total_tokens,
            items=items,
            architectural_path=path,
            preserved_semantics=preserved,
            compact=compact,
            fractional_upper_bound=fractional_upper_bound,
            swaps_performed=swaps_performed,
            seed_cost=seed_cost,
            budget_exceeded=budget_exceeded,
            truncation_occurred=total_tokens > self.budget,
            seed_compression_level=seed_compression_level,
            fatal_seed_overflow=fatal_seed_overflow,
            knapsack_efficiency_ratio=efficiency_ratio,
            knapsack_efficiency_warning=efficiency_warning,
        )

    #: Item 18 (third post-implementation audit) / Progressive Seed
    #: Degradation: the warning comment prepended to a seed's own
    #: rendered content when it had to be compressed below L0 to fit the
    #: budget - visible to a downstream reader/LLM in the rendered
    #: content itself, not just in `PackResult`'s own metadata fields.
    _SEED_DEGRADATION_NOTICES = {
        1: "/* Warning: Seed compressed to L1 due to budget constraint */\n",
        2: "/* Warning: Seed compressed to L2 skeleton due to severe budget constraint */\n",
        3: "/* Warning: Seed compressed to minimal stub - budget insufficient even for a skeleton */\n",
    }

    def _degrade_seed_to_fit(
        self,
        builder: ConcreteGraphBuilder,
        tag_matrix: dict[str, set[str]],
        seed: str,
        seed_symbol,
        seed_path: str,
        compact: bool,
    ) -> tuple[int, str, float]:
        """Item 18: try L0, then L1, then L2, then (last resort) L3,
        stopping at the first tier whose real token cost fits
        `self.budget` - the seed is still always packed unconditionally
        (Invariant #4, Seed Dominance - nothing here can ever exclude
        it), it just no longer has to be the full, most expensive L0
        rendering to satisfy that. Checked against `self.budget` (the
        raw nominal budget), not the reduced `_admission_budget` - the
        seed's own admission has never gone through that safety-margin
        reduction (see Issue A3's original comment, carried forward
        here), only ordinary candidates do. L3 is always accepted
        regardless of whether it fits - nothing smaller exists to try,
        and the seed must always be shown (Invariant #4) even when doing
        so unavoidably exceeds the budget; that residual case is exactly
        what `fatal_seed_overflow`/`budget_exceeded` report.
        """
        for resolution in (0, 1, 2, 3):
            content = self._render(builder, tag_matrix, seed, resolution, compact)
            if content is None:
                raise ValueError(f"Seed symbol '{seed}' was not found in the concrete graph (unknown or external symbol)")
            notice = self._SEED_DEGRADATION_NOTICES.get(resolution)
            rendered = f"{notice}{content}" if notice else content
            cost = estimate_tokens(rendered) + _wrapping_overhead_tokens(
                seed, seed_path, resolution, seed_symbol.line_range, is_seed=True
            )
            if cost <= self.budget or resolution == 3:
                return resolution, rendered, cost
        raise AssertionError("unreachable: the resolution==3 branch above always returns")

    #: Swap-Refinement Pass hard cap (Issue #12.2) - bounds the pass's own
    #: worst-case work (one rendering attempt per unselected candidate,
    #: independent of how many actually swap) and keeps the number of
    #: post-hoc mutations to the packed set small and auditable rather
    #: than open-ended.
    MAX_SWAPS = 5

    def _swap_refine(
        self,
        items: list[PackedItem],
        total_tokens: float,
        candidates: list[str],
        packed: set[str],
        protected: set[str],
        distances: dict[str, float],
        builder: ConcreteGraphBuilder,
        tag_matrix: dict[str, set[str]],
        compact: bool,
    ) -> tuple[list[PackedItem], float, int]:
        """Issue #12.2: after the main greedy pass settles, check whether
        any still-unselected candidate - necessarily *more* relevant than
        anything sorted after it, since `candidates` is itself distance-
        sorted - can displace an already-packed, strictly *less* relevant
        ordinary candidate without exceeding the budget. Tries each
        unselected candidate at its cheapest (L3) representation only:
        the point of this pass is recovering candidates the main loop
        starved out entirely, not re-litigating resolution tiers for ones
        it already admitted.

        Issue A4 (post-implementation audit) - complexity and scope,
        stated precisely rather than left implicit:

        Complexity: let W = MAX_SWAP_ATTEMPTS = 20 (the number of closest
        unselected candidates considered - see that constant's own
        comment for the 11-second regression bounding it fixed) and let
        I = the number of currently-packed items. Each of the W
        candidates first pays an O(I) no-render distance pre-check; only
        a candidate that clears it pays for one real `_render()` call, so
        real rendering work is bounded by O(W) renders total, never more.
        A candidate that clears the pre-check then sorts the packed,
        swappable items by distance (O(I log I)) to find the single
        farthest evictable occupant. Total worst case is therefore
        O(W * I log I) plus at most W renders - **not** O(W * K): `K =
        MAX_SWAPS = 5` only caps how many of those W attempts are allowed
        to actually *succeed* (mutate `items`/`packed`), it does not
        reduce how many are attempted, pre-checked, or rendered.

        What this pass fixes vs. what it structurally cannot: it repairs
        the single most common greedy-knapsack failure mode - one close,
        clearly-more-relevant candidate starved out because the main
        loop's distance-ordered pass hit an earlier, farther, lower-value
        item that happened to fit and stopped there. It is a bounded,
        single-item local-search heuristic, not a restoration of
        knapsack optimality (that would require the NP-hard 0/1 exact
        solve this class deliberately avoids - see
        `_fractional_relaxation_bound`'s own docstring for the LP
        relaxation used instead as a diagnostic-only upper bound). It
        does **not**: perform multi-item swaps (evicting two or more
        lower-priority occupants to admit one better candidate);
        consider candidates beyond the closest W by distance, however
        much better they might be than a poorly-fit packed item; upgrade
        a successfully swapped-in candidate above its cheapest L3
        representation even if budget allows; or ever touch the
        resolution tier of an already-packed item that wasn't evicted.
        """
        # `candidates` is already distance-sorted, so the unselected
        # prefix most likely to actually beat something already packed is
        # the *closest* unselected candidates - only those are worth
        # attempting. Bounding by attempts (not just successful swaps) is
        # essential, not cosmetic: against a real, densely-connected
        # repository `unselected` can run into the hundreds, and without
        # this cap a mostly-fruitless sweep (checking, then discarding)
        # was measured taking upwards of 10 real seconds per `pack()` call
        # purely from rendering an L3 candidate that never ends up used.
        MAX_SWAP_ATTEMPTS = 20
        unselected = [n for n in candidates if n not in packed][:MAX_SWAP_ATTEMPTS]
        if not unselected:
            return items, total_tokens, 0

        swaps_performed = 0
        for candidate_node in unselected:
            if swaps_performed >= self.MAX_SWAPS:
                break
            candidate_distance = distances[candidate_node]

            # Cheap pre-check using only already-computed distances - no
            # rendering at all - before paying for an L3 render that can
            # only ever be wasted work if no packed, unprotected item is
            # even farther than this candidate to begin with.
            if not any(
                item.symbol not in protected and distances.get(item.symbol, -1.0) > candidate_distance
                for item in items
            ):
                continue

            candidate_symbol = builder.symbol_table.get(candidate_node)
            if candidate_symbol is None:
                continue
            candidate_path = self._relative_path(builder, candidate_symbol.file)
            content = self._render(builder, tag_matrix, candidate_node, 3, compact)
            if content is None:
                continue
            overhead = _wrapping_overhead_tokens(candidate_node, candidate_path, 3, candidate_symbol.line_range)
            candidate_cost = estimate_tokens(content) + overhead

            # Least-relevant (farthest) swappable packed item first, so a
            # swap - when one fires - displaces the worst available
            # occupant rather than an arbitrary one.
            swap_pool = sorted(
                (
                    item for item in items
                    if item.symbol not in protected and distances.get(item.symbol, -1.0) > candidate_distance
                ),
                key=lambda it: distances.get(it.symbol, -1.0),
                reverse=True,
            )
            for outgoing in swap_pool:
                outgoing_overhead = _wrapping_overhead_tokens(
                    outgoing.symbol, outgoing.relative_path, outgoing.resolution, outgoing.line_range
                )
                outgoing_cost = estimate_tokens(outgoing.content) + outgoing_overhead
                if total_tokens - outgoing_cost + candidate_cost > self._admission_budget:
                    continue
                items.remove(outgoing)
                items.append(PackedItem(
                    candidate_node, 3, content, candidate_symbol.language_id, candidate_symbol.line_range, candidate_path,
                ))
                total_tokens = total_tokens - outgoing_cost + candidate_cost
                packed.discard(outgoing.symbol)
                packed.add(candidate_node)
                swaps_performed += 1
                break

        return items, total_tokens, swaps_performed

    #: Fractional-relaxation diagnostic's flat per-candidate weight proxy
    #: (Issue #12.3) - a typical one-line L3 signature stub's token cost,
    #: cheap enough to use for every candidate without actually rendering
    #: each one (this bound only needs to be directionally honest, not
    #: exact - see `_fractional_relaxation_bound`'s own docstring).
    APPROX_L3_WEIGHT_TOKENS = 15.0

    def _fractional_relaxation_bound(self, candidates: list[str], distances: dict[str, float]) -> float:
        """Classic fractional-knapsack LP relaxation (Issue #12.3): the
        best achievable value if candidates could be packed in
        arbitrarily divisible fractions instead of the real all-or-
        nothing (0/1, resolution-tiered) constraint this class actually
        packs under - a theoretical upper bound reported for comparison,
        never used to gate a real admission decision. Value is proxied by
        inverse distance (closer = more valuable, matching this class's
        own distance-first candidate ordering); weight by a flat,
        conservative per-candidate footprint (`APPROX_L3_WEIGHT_TOKENS`)
        rather than this bound rendering every candidate just to report a
        diagnostic number.
        """
        ranked = sorted(candidates, key=lambda n: distances.get(n, float("inf")))
        remaining = self._admission_budget
        value = 0.0
        for node in ranked:
            if remaining <= 0:
                break
            item_value = 1.0 / (1.0 + distances.get(node, 0.0))
            if self.APPROX_L3_WEIGHT_TOKENS <= remaining:
                value += item_value
                remaining -= self.APPROX_L3_WEIGHT_TOKENS
            else:
                value += item_value * (remaining / self.APPROX_L3_WEIGHT_TOKENS)
                remaining = 0.0
        return round(value, 4)

    #: Item 20 (second post-implementation audit): below this
    #: `knapsack_efficiency_ratio`, the real 0/1 admission achieved
    #: meaningfully less than the fractional-relaxation upper bound
    #: predicted was achievable - a signal of high item fragmentation
    #: (many small, awkwardly-sized candidates the greedy/swap-refine
    #: passes couldn't pack efficiently), not an error.
    EFFICIENCY_RATIO_WARNING_THRESHOLD = 0.75

    def _knapsack_efficiency_ratio(
        self, items: list[PackedItem], seed: str, distances: dict[str, float], fractional_upper_bound: float
    ) -> float:
        """Item 20: `EfficiencyRatio = sum(Score(Selected)) / Bound_frac` -
        how much of `_fractional_relaxation_bound`'s theoretical upper
        bound the real, integer (0/1, resolution-tiered) admission
        actually achieved, using the *same* inverse-distance value proxy
        that bound itself uses so the two numbers are directly
        comparable. Diagnostic only, like the bound it's computed from -
        never gates a real admission decision.

        Not a strict `<= 1.0` guarantee: `fractional_upper_bound` prices
        every candidate at a flat `APPROX_L3_WEIGHT_TOKENS` proxy weight
        (cheap to compute without rendering every candidate), while real
        admission costs vary per resolution tier and can occasionally be
        *cheaper* than that flat proxy - reported as measured, not
        artificially clamped, since a ratio above 1.0 is itself a real,
        legitimate signal (the flat-weight model under-estimated how
        much could actually fit), not a bug to hide.
        """
        if fractional_upper_bound <= 0:
            return 1.0
        selected_value = sum(
            1.0 / (1.0 + distances.get(item.symbol, 0.0)) for item in items if item.symbol != seed
        )
        return round(selected_value / fractional_upper_bound, 4)

    @staticmethod
    def _relative_path(builder: ConcreteGraphBuilder, file_path: str) -> str:
        return os.path.relpath(file_path, builder.repo_root)

    @staticmethod
    def _sentinel_type(g_c, node: str) -> str | None:
        """`"unresolved_polymorphic"` | `"dynamic_edge"` | `None` - see
        `ConcreteGraphBuilder._resolve_ambiguous_call`/
        `_emit_dynamic_edge_sentinel`, which are the only two places that
        ever set the `sentinel_type` node attribute."""
        return g_c.nodes[node].get("sentinel_type") if node in g_c.nodes else None

    @staticmethod
    def _render_sentinel(data: dict, tag_matrix: dict[str, set[str]], sentinel_type: str) -> str:
        line = data.get("call_site_line", 0)
        if sentinel_type == "unresolved_polymorphic":
            candidates = [(c, tag_matrix.get(c, set())) for c in data.get("candidates", [])]
            return render_unresolved_polymorphic(data.get("identifier", ""), line, candidates)
        return render_dynamic_edge_sentinel(
            data.get("call_site_expr", ""), line, data.get("hazard_type", ""), data.get("target_object")
        )

    @staticmethod
    def _data_flow_centrality(g_c, node: str) -> int:
        """DataFlowCentrality(v) = |{(u,v) in E | v is an argument or
        return value of u}| (Issue #10.2) - proxied by how many distinct
        incoming CALLS edges captured `node`'s own return value into a
        variable (`bound_to is not None` on that edge's own
        `CallSiteContext`, already computed by
        `prism.graph.call_site.compute_call_site_context` for every
        resolved call). A bare fire-and-forget call (`node()` with its
        result discarded) doesn't count - only a use where the caller's
        own downstream logic actually depends on what `node` returned.
        """
        if node not in g_c:
            return 0
        return sum(1 for _u, _v, data in g_c.in_edges(node, data=True) if data.get("bound_to") is not None)

    def _render(
        self, builder: ConcreteGraphBuilder, tag_matrix: dict[str, set[str]], qname: str, resolution: int, compact: bool = False
    ) -> str | None:
        symbol = builder.symbol_table.get(qname)
        if symbol is None:
            return None
        parsed = builder.parsed_file(symbol.file)
        if parsed is None:
            return None
        source = parsed.source.decode("utf-8", errors="replace")
        name = qname.rsplit(".", 1)[-1]
        context = CompressionContext(tags=tag_matrix.get(qname, set()), callees=self._callee_labels(builder, qname), compact=compact)
        def_node = builder.def_node(qname)
        return self.compressor.compress(symbol.language_id, source, name, symbol.line_range, resolution, context, def_node=def_node)

    @staticmethod
    def _directed_reachable(g_c, seed: str) -> set[str]:
        """The seed plus every node on its own directed call-chain
        closure - exactly what `benchmarks.raw_context.build_raw_context`
        would dump for this seed. Computed independently here (the core
        engine never depends on `benchmarks`, which depends on `prism`, not
        the other way around); used both to decide Adaptive Compact
        Scaffolding and, when compact, to scope candidate selection to the
        same footprint a raw dump would have covered.
        """
        if seed not in g_c:
            return {seed}
        reachable = set(nx.descendants(g_c, seed))
        reachable.add(seed)
        return reachable

    def _is_compact_mode(self, builder: ConcreteGraphBuilder, reachable: set[str]) -> bool:
        """Adaptive Compact Scaffolding's trigger: is the seed's own
        call-chain neighborhood small enough that Prism's normal, verbose
        scaffolding - and packing candidates beyond that neighborhood at
        all - isn't worth its token cost?
        """
        if len(reachable) <= self.COMPACT_MODE_MAX_ACTIVE_NODES:
            return True

        files: set[str] = set()
        for node in reachable:
            symbol = builder.symbol_table.get(node)
            if symbol is not None:
                files.add(symbol.file)
        raw_footprint = 0.0
        for file_path in files:
            parsed = builder.parsed_file(file_path)
            if parsed is not None:
                raw_footprint += estimate_tokens(parsed.source.decode("utf-8", errors="replace"))
        return raw_footprint < self.COMPACT_MODE_RAW_FOOTPRINT_TOKENS

    def _callee_labels(self, builder: ConcreteGraphBuilder, qname: str) -> list[str]:
        if qname not in builder.calls_graph:
            return []
        labels = []
        for successor in sorted(builder.calls_graph.successors(qname)):
            symbol = builder.symbol_table.get(successor)
            if symbol is not None and symbol.kind == "class":
                # Constructor calls are already visible in the caller's own
                # code; omit them from the interface-contract "Calls" line
                # so it highlights behavioral delegation, not allocation.
                continue
            labels.append(successor)
        return labels

    @staticmethod
    def _preserved_semantics(items: list[PackedItem], candidates: list[str]) -> float:
        # An infallible-leaf compact signature isn't a normal L0-L3
        # resolution level at all (see `INFALLIBLE_SIGNATURE_RESOLUTION`) -
        # weighted the same as L3 (a bare one-line alias) here, since both
        # represent "the least detail this reporting metric tracks",
        # rather than crashing on a `RESOLUTION_WEIGHT` lookup miss.
        packed_weight = sum(
            RESOLUTION_WEIGHT[item.resolution] if item.resolution >= 0 else RESOLUTION_WEIGHT[3]
            for item in items
        )
        # +1 accounts for the pinned seed, always rendered at full L0.
        max_weight = 1.0 + len(candidates) * RESOLUTION_WEIGHT[0]
        if max_weight <= 0:
            return 100.0
        return round(min(packed_weight / max_weight, 1.0) * 100.0, 1)
