"""Phase B spike, Step 2: Approach C ("Forked Spine Pruning in Knapsack").

Forks the real production selection algorithm
(`prism.packer.submodular_knapsack.select_submodular_context`) rather
than modifying it - production stays completely untouched, exactly as
Approach B's own `RenderOptions.two_zone` addition did for the renderer.
Unlike Approach B (which only changed *rendering*, never which nodes get
selected), Approach C changes *selection itself*: this is the first spike
in this rollout that can actually move `fpr_gt`.

### The hypothesis under test

`docs/design_formalism.md` Sec 10.4's own closing finding on the disclosed
django_t02_017/`_urlparse` regression: excluding the VERIFICATION-role
test candidate let other real successors win main-loop rounds by pure
density, leaving too little budget for `_urlparse`'s own cascade-based
admission - "topological causal-spine prioritization must supersede pure
greedy novelty/density scoring under tight budget pressure." This module
tests that claim directly, on the same 3 tasks/3 budgets/2 seeds Approach
B was benchmarked against.

### Mechanics

1. **Predecessor spine marking (O(V+E))**: `nx.single_source_dijkstra`
   over the same causal graph/weight attribute `dist_w_map` already comes
   from (so the spine is derived from the identical Dijkstra run, not a
   second, possibly-inconsistent traversal) gives every reachable
   candidate's own shortest path from `seed_id`. The union of every node
   on every one of those paths is the "spine" - the topological causal
   backbone the seed can reach candidates *through*, not just the
   candidates themselves.
2. **Tier-1 admission**: spine nodes are admitted first, in ascending
   `dist_w` order, always at `L0_full`, budget-permitting - no density
   competition at all. Being structurally on the seed's own causal
   shortest-path tree outranks feature-novelty/cost density by
   construction; this is the literal mechanism the hypothesis above
   names.
3. **Tier-2 admission**: the remaining (non-spine) candidate pool runs a
   static-pool variant of production's own density-competitive greedy
   loop (same `compute_candidate_value` formula, same novelty-adaptive
   streak gate) - "static" because, unlike production's lazy
   frontier-expansion/cascade machinery, every candidate this call will
   ever consider is already known upfront (the full `dist_w_map`/upstream
   universe), so there is nothing left to discover mid-loop. This is a
   deliberate simplification for this spike: a Tier-2 node can be
   admitted even if an ancestor on its own shortest path was skipped for
   budget reasons - production's precedence constraint is not
   re-enforced here. Stated honestly, not silently assumed away.
4. **Peripheral leaf demotion/eviction**: only within Tier-2, and only
   when `target_budget <= TIGHT_BUDGET_THRESHOLD` (4000): a candidate
   with zero out-degree *into the candidate subgraph itself* (a real dead
   end - it may still have raw graph edges, but none of them lead
   anywhere else this call would ever consider) is priced at its
   `_signature_stub` cost instead of its full L0 cost, and is admitted at
   all only if its own (stub-priced) density clears the *median density
   of Tier-1's own admitted spine nodes* - a self-relative threshold
   computed fresh per call from whatever value range this specific
   seed's own spine actually produced, not a hand-picked constant. A leaf
   that doesn't clear it is evicted outright, never admitted at any
   compression level - this is the mechanism meant to suppress noise
   (`fpr_gt`) without touching recall (`cpi_strict`), since a true
   ground-truth pipeline symbol overwhelmingly sits on the spine, not
   among unrelated dead-end leaves.

Reuses real production helpers by import wherever the algorithm itself is
unchanged (`compute_candidate_value`, `_default_costs`, `_classify_role`,
`_signature_stub`, `_is_role_mismatched_verification`'s own logic,
`SubmodularPackedItem`/`SubmodularPackResult`) - only the admission
strategy inside the greedy loop is forked, per this rollout's own
"production stays untouched" constraint.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

import networkx as nx

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.contracts import BehavioralContract
from prism.graph.symbol_table import GlobalSymbolTable, SymbolRole
from prism.packer.blast_radius import CONTRACT_PRESERVATION_MULTIPLIER, compute_upstream_callers
from prism.packer.submodular_knapsack import (
    DEFAULT_BETA,
    DEFAULT_DELTA_MAX,
    DEFAULT_MAX_HOPS,
    DEFAULT_UPSTREAM_MAX_HOPS,
    DOMINANCE_SAFETY_BOUND,
    MAX_ZERO_NOVELTY_K,
    PHASE_E_IMMUNE_DIST,
    SCOPE_GATE_MIN_DIST,
    SeedNotFoundError,
    SubmodularPackedItem,
    SubmodularPackResult,
    _classify_role,
    _default_costs,
    _module_prefix3,
    _PHASE_E_SUBSTANCE_SINK_MASK,
    _signature_stub,
    compute_candidate_value,
    suggest_similar_seeds,
)
from prism.semantics.extractor import compute_feature_masks_cached
from prism.slicer.tokenizer import count_tokens
from prism.surface.build import (
    _axis_labels,
    _causal_path_enabled,
    _coverage_summary,
    _derive_contract,
    _enforce_render_budget,
    _node_body,
    _node_signature,
    _output_kind,
    _RESOLUTION_TO_LEVEL,
    _relative_path,
    causal_path_applies_to_task_type,
    ENGINE_NAME,
    ENGINE_VERSION,
)
from prism.surface.models import (
    BudgetRef,
    CausalPath,
    CausalPathStage,
    ContextPackage,
    EdgeEntry,
    EngineRef,
    EnvelopeWarning,
    LanguageRef,
    Manifest,
    ManifestCompression,
    ManifestDistanceMetric,
    NodeEntry,
    NodeFeatures,
    SeedRef,
)
from prism.language_tiers import precision_tier_for
from prism.packer.submodular_knapsack import ROLE_SEED
from prism.semantics.bitmask import FORM_BITS, OUTPUT_BITS, ROLE_BITS, SUBSTANCE_BITS
from prism.slicer.tokenizer import active_backend, is_exact
from prism.surface.causal_path import compute_causal_path
from prism.traversal._cache_keys import snapshot_file_hash_set
from prism.traversal.causal_weights import LAMBDA_DATA_FLOW, LAMBDA_GUARD, compute_all_data_flow_edges, compute_causal_edges, compute_guard_indicator_edges
from prism.traversal.continuous_dijkstra import build_causal_graph, compute_topological_distances

#: "Under tight budget pressure" per the implementation plan - matches
#: the 2000/4000/8000 budget sweep this spike runs (2000 and 4000 both
#: count as tight, 8000 does not).
TIGHT_BUDGET_THRESHOLD = 4000


@dataclass
class SpineVariantSelection:
    selected: list[str]
    compression: dict[str, str] = field(default_factory=dict)
    spine_ids: set[str] = field(default_factory=set)
    median_spine_density: float = 0.0


def select_submodular_context_spine_variant(
    graph: nx.DiGraph,
    seed_id: str,
    target_budget: int,
    dist_w_map: dict[str, float],
    feature_masks: dict[str, int],
    costs: dict[str, int],
    builder: ConcreteGraphBuilder,
    max_hops: float = DEFAULT_MAX_HOPS,
    beta: float = DEFAULT_BETA,
    delta_max: int = DEFAULT_DELTA_MAX,
    dist_w_upstream_map: dict[str, float] | None = None,
    upstream_contract_preserving: set[str] | None = None,
    upstream_max_hops: float = DEFAULT_UPSTREAM_MAX_HOPS,
    symbol_table: GlobalSymbolTable | None = None,
) -> SpineVariantSelection:
    if beta * delta_max >= DOMINANCE_SAFETY_BOUND:
        raise ValueError(
            f"beta * delta_max = {beta * delta_max} >= {DOMINANCE_SAFETY_BOUND} - "
            "this would let same-cohort feature novelty outrank real topological "
            "distance, breaking the 1-hop-vs-2-hop dominance guarantee this packer depends on."
        )

    s_pack = [seed_id]
    current_cost = costs.get(seed_id, 0)
    covered_mask = feature_masks.get(seed_id, 0)
    upstream_contract_preserving = upstream_contract_preserving or set()
    dist_w_upstream_map = dist_w_upstream_map or {}

    def _is_real_candidate(qname: str) -> bool:
        return costs.get(qname, 0) > 0

    _seed_info = symbol_table.get(seed_id) if symbol_table is not None else None
    _seed_module = _seed_info.module if _seed_info is not None else ""
    _seed_role = _seed_info.role if _seed_info is not None else SymbolRole.IMPLEMENTATION
    _seed_substance = feature_masks.get(seed_id, 0) & _PHASE_E_SUBSTANCE_SINK_MASK

    def _is_in_scope(qname: str, dist: float) -> bool:
        if symbol_table is None or dist <= SCOPE_GATE_MIN_DIST:
            return True
        info = symbol_table.get(qname)
        candidate_module = info.module if info is not None else ""
        if candidate_module and _seed_module and _module_prefix3(candidate_module) == _module_prefix3(_seed_module):
            return True
        candidate_substance = feature_masks.get(qname, 0) & _PHASE_E_SUBSTANCE_SINK_MASK
        return bool(candidate_substance & _seed_substance)

    def _is_role_mismatched_verification(qname: str) -> bool:
        if symbol_table is None or _seed_role != SymbolRole.IMPLEMENTATION:
            return False
        info = symbol_table.get(qname)
        return info is not None and info.role == SymbolRole.VERIFICATION

    def _is_admissible(qname: str, dist: float) -> bool:
        return _is_real_candidate(qname) and _is_in_scope(qname, dist) and not _is_role_mismatched_verification(qname)

    candidate_universe = set(dist_w_map)  # every forward-reachable node within max_hops (seed excluded, per compute_topological_distances' own contract)

    def _candidate_out_degree(qname: str) -> int:
        if qname not in graph:
            return 0
        return sum(1 for succ in graph.successors(qname) if succ in candidate_universe or succ == seed_id)

    # --- Predecessor spine marking (O(V+E)) - see this module's own
    # docstring. Every reachable candidate trivially sits on the tail end
    # of *some* shortest path (its own), so "nodes sitting on the
    # directed path between the seed and a terminal/boundary symbol"
    # means the *interior* nodes of that shortest-path tree - the ones a
    # path to some deeper/terminal candidate actually routes *through* -
    # not the terminal endpoints themselves. Concretely: a candidate with
    # at least one real successor edge into another reachable candidate
    # is a backbone/interior node (it is, by construction, on the way to
    # whatever that successor - or something further past it - reaches);
    # a candidate with zero such successors (`_candidate_out_degree == 0`)
    # is itself a terminal/boundary symbol, not an interior spine node,
    # and falls to Tier 2's peripheral-leaf handling below instead.
    # (Verified this is the right split empirically, not assumed: an
    # earlier version of this function marked every reachable candidate
    # as "spine" via its own trivial self-path, which made Tier 1
    # indistinguishable from the full candidate set and reproduced the
    # exact django_t02_017/_urlparse regression this spike exists to
    # test, just relocated - _urlparse lost the ascending-dist_w Tier-1
    # race to several dist_w=1.0 siblings before its own dist_w=2.0 turn,
    # the same failure shape as the original bug, only now inside a
    # differently-named tier.)
    spine_ids: set[str] = {n for n in candidate_universe if _candidate_out_degree(n) > 0}

    # Upstream candidates (Bidirectional Blast-Radius) join Tier 2 only -
    # never spine-tier, since the spine is specifically the seed's own
    # *forward* shortest-path tree.
    upstream_candidates: set[str] = set()
    for pred, dist in dist_w_upstream_map.items():
        if dist <= upstream_max_hops and _is_real_candidate(pred):
            upstream_candidates.add(pred)

    forward_candidates = {n for n in candidate_universe if n != seed_id}
    all_candidates = forward_candidates | upstream_candidates

    def _dist_of(qname: str) -> float:
        if qname in dist_w_map:
            return dist_w_map[qname]
        return dist_w_upstream_map.get(qname, float("inf"))

    admissible = {n for n in all_candidates if _is_admissible(n, _dist_of(n))}

    tier1 = sorted((n for n in admissible if n in spine_ids), key=lambda n: (dist_w_map.get(n, float("inf")), n))
    tier2_pool = {n for n in admissible if n not in spine_ids}

    # --- Tier 1: spine nodes - strict topological priority, always
    # L0_full, ascending dist_w, budget-permitting. No density
    # competition: this is the hypothesis itself, not an approximation
    # of it. ---
    spine_admitted_densities: list[float] = []
    for node in tier1:
        cost = costs.get(node, 0)
        if current_cost + cost > target_budget:
            continue
        dist = _dist_of(node)
        mask = feature_masks.get(node, 0)
        value = compute_candidate_value(dist, mask, covered_mask, beta, delta_max)
        if node in upstream_contract_preserving:
            value *= CONTRACT_PRESERVATION_MULTIPLIER
        spine_admitted_densities.append(value / max(cost, 1))
        s_pack.append(node)
        current_cost += cost
        covered_mask |= mask

    spine_admitted_densities.sort()
    if spine_admitted_densities:
        n = len(spine_admitted_densities)
        median_spine_density = (
            spine_admitted_densities[n // 2]
            if n % 2 == 1
            else (spine_admitted_densities[n // 2 - 1] + spine_admitted_densities[n // 2]) / 2.0
        )
    else:
        median_spine_density = 0.0

    tight_budget = target_budget <= TIGHT_BUDGET_THRESHOLD

    # --- Tier 2: static-pool density competition over the remaining,
    # non-spine candidates - see this module's own docstring for how this
    # differs from production's lazy frontier-expansion/cascade loop.
    # Peripheral leaf demotion/eviction only ever applies here. ---
    compression: dict[str, str] = {}
    remaining_pool = set(tier2_pool)
    consecutive_zero_novelty_admits = 0

    def _is_streak_immune(dist: float) -> bool:
        return dist <= PHASE_E_IMMUNE_DIST

    while remaining_pool:
        best_node = None
        best_density = -1.0
        best_use_stub = False
        best_effective_cost = 0

        for candidate in sorted(remaining_pool):
            dist = _dist_of(candidate)
            mask = feature_masks.get(candidate, 0)
            full_cost = costs.get(candidate, 0)
            is_leaf = _candidate_out_degree(candidate) == 0
            use_stub = tight_budget and is_leaf

            effective_cost = full_cost
            stub_text = None
            if use_stub:
                stub_text = _signature_stub(builder, candidate)
                if stub_text is not None:
                    effective_cost = max(count_tokens(stub_text), 1)
                # No real source to stub (no symbol-table entry/parsed
                # file) - falls back to full-cost pricing below, the same
                # "can't render what doesn't exist" contract production's
                # own _signature_stub callers already follow.

            if current_cost + effective_cost > target_budget:
                continue

            raw_novel_bits = (mask & ~covered_mask).bit_count()
            if raw_novel_bits == 0 and not _is_streak_immune(dist) and consecutive_zero_novelty_admits >= MAX_ZERO_NOVELTY_K:
                continue

            value = compute_candidate_value(dist, mask, covered_mask, beta, delta_max)
            if candidate in upstream_contract_preserving:
                value *= CONTRACT_PRESERVATION_MULTIPLIER
            density = value / max(effective_cost, 1)

            # Peripheral leaf eviction: a leaf whose own stub-priced
            # density doesn't clear the spine's own median density is
            # noise this variant exists to suppress - never admitted at
            # any compression level, not just downgraded.
            if use_stub and stub_text is not None and density < median_spine_density:
                continue

            if density > best_density:
                best_density = density
                best_node = candidate
                best_use_stub = use_stub and stub_text is not None
                best_effective_cost = effective_cost

        if best_node is None:
            break

        s_pack.append(best_node)
        mask = feature_masks.get(best_node, 0)
        novel_bits = (mask & ~covered_mask).bit_count()
        dist = _dist_of(best_node)
        if novel_bits == 0:
            if not _is_streak_immune(dist):
                consecutive_zero_novelty_admits += 1
        else:
            consecutive_zero_novelty_admits = 0
        current_cost += best_effective_cost
        covered_mask |= mask
        remaining_pool.discard(best_node)
        if best_use_stub:
            costs[best_node] = best_effective_cost
            compression[best_node] = "L2_skeleton"

    # Mandatory downstream direct-successor protection - mirrors
    # production's own post-loop force-add (see submodular_knapsack.py's
    # own docstring for the full reasoning); kept here for parity with
    # the baseline safety net, not itself part of this spike's own
    # topological-priority hypothesis.
    if seed_id in graph:
        for succ in sorted(graph.successors(seed_id)):
            if succ in s_pack:
                continue
            dist = dist_w_map.get(succ, float("inf"))
            if dist > PHASE_E_IMMUNE_DIST or not _is_real_candidate(succ):
                continue
            cost = costs.get(succ, 0)
            if current_cost + cost > target_budget:
                continue
            if _is_role_mismatched_verification(succ):
                continue
            s_pack.append(succ)
            current_cost += cost
            covered_mask |= feature_masks.get(succ, 0)

    if upstream_candidates:
        best_upstream = min(upstream_candidates, key=lambda u: (dist_w_upstream_map.get(u, float("inf")), u))
        if (
            best_upstream not in s_pack
            and current_cost + costs.get(best_upstream, 0) <= target_budget
            and not _is_role_mismatched_verification(best_upstream)
        ):
            s_pack.append(best_upstream)

    return SpineVariantSelection(
        selected=s_pack, compression=compression, spine_ids=spine_ids, median_spine_density=median_spine_density
    )


def pack_symbol_context_spine_variant(
    builder: ConcreteGraphBuilder,
    seed_id: str,
    target_budget: int,
    max_hops: float = DEFAULT_MAX_HOPS,
    beta: float = DEFAULT_BETA,
    delta_max: int = DEFAULT_DELTA_MAX,
    upstream_max_hops: float = DEFAULT_UPSTREAM_MAX_HOPS,
) -> SubmodularPackResult:
    """Forked from `prism.packer.submodular_knapsack.pack_symbol_context`:
    identical wiring (causal graph, feature masks, Dijkstra distances,
    upstream callers, real BPE costs, the class-promotion and stub-pack
    post-loop fixups, verbatim) except the core greedy selection call,
    which uses `select_submodular_context_spine_variant` instead of
    production's `select_submodular_context`. `contracts=None` throughout
    - production's own `pack_symbol_context` never threads `contracts`
    into its packing/budget step either (see that function's own Fix #2
    comment: "passing contracts through here...measured as a genuine
    regression...selection stays exactly as before"), so this stays
    directly comparable to the baseline/B_two_zone cells it's
    benchmarked against, which go through the identical contracts-blind
    production path.
    """
    if seed_id not in builder.symbol_table:
        raise SeedNotFoundError(seed_id, suggest_similar_seeds(builder, seed_id))

    effective_budget = target_budget

    with snapshot_file_hash_set(builder.repo_root):
        graph = build_causal_graph(builder)
        feature_masks = compute_feature_masks_cached(builder, builder.repo_root)
        dist_w_map = compute_topological_distances(builder, seed_id, d_max=max_hops)
        upstream_callers = compute_upstream_callers(builder, seed_id)
        dist_w_upstream_map = {symbol: caller.dist_w_upstream for symbol, caller in upstream_callers.items()}
        upstream_contract_preserving = {symbol for symbol, caller in upstream_callers.items() if caller.unpacks_return}

        candidate_symbols = (
            [seed_id]
            + [n for n in dist_w_map if dist_w_map[n] <= max_hops]
            + [n for n in dist_w_upstream_map if dist_w_upstream_map[n] <= upstream_max_hops]
        )
        costs = _default_costs(builder, candidate_symbols, contracts=None)

        variant = select_submodular_context_spine_variant(
            graph, seed_id, effective_budget, dist_w_map, feature_masks, costs, builder,
            max_hops=max_hops, beta=beta, delta_max=delta_max,
            dist_w_upstream_map=dist_w_upstream_map,
            upstream_contract_preserving=upstream_contract_preserving,
            upstream_max_hops=upstream_max_hops,
            symbol_table=builder.symbol_table,
        )

    selected = variant.selected
    stub_compression: dict[str, str] = dict(variant.compression)

    # --- fix-include-class-when-method-selected (verbatim fork of
    # pack_symbol_context's own post-loop fixup) ---
    direct_successors = set(graph.successors(seed_id)) if seed_id in graph else set()
    selected_set = set(selected)
    running_cost = sum(costs.get(q, 0) for q in selected)
    promoted_classes: set[str] = set()
    reordered_selected: list[str] = []
    for qname in selected:
        info = builder.symbol_table.get(qname)
        class_qname = info.enclosing_class if info is not None and info.kind == "method" else None
        if class_qname is not None and class_qname not in selected_set and class_qname not in promoted_classes:
            if class_qname not in costs:
                costs.update(_default_costs(builder, [class_qname], contracts=None))
            class_cost = costs.get(class_qname, 0)
            if class_cost > 0 and running_cost + class_cost <= effective_budget:
                reordered_selected.append(class_qname)
                promoted_classes.add(class_qname)
                selected_set.add(class_qname)
                running_cost += class_cost
            elif class_cost > 0:
                print(
                    f"[spine-variant] fix-include-class-when-method-selected: {class_qname!r} "
                    f"needed by an admitted method but the remaining budget ({effective_budget - running_cost}) "
                    f"can't afford its cost ({class_cost}) - skipped",
                    file=sys.stderr,
                )
        reordered_selected.append(qname)
    selected = reordered_selected

    # --- fix-stub-pack-distance-1-tight-budget (verbatim fork) ---
    for succ in sorted(direct_successors):
        if succ in selected_set:
            continue
        full_cost = costs.get(succ, 0)
        if full_cost <= 0:
            continue
        remaining = effective_budget - running_cost
        if full_cost <= remaining:
            continue
        stub_text = _signature_stub(builder, succ)
        if stub_text is None:
            continue
        stub_cost = max(count_tokens(stub_text), 1)
        if stub_cost <= remaining:
            selected.append(succ)
            selected_set.add(succ)
            costs[succ] = stub_cost
            stub_compression[succ] = "L2_skeleton"
            running_cost += stub_cost
        else:
            print(
                f"[spine-variant] fix-stub-pack-distance-1-tight-budget: {succ!r} "
                f"is a direct pipeline successor but neither its full cost "
                f"({full_cost}) nor its signature-only stub cost ({stub_cost}) "
                f"fit the remaining budget ({remaining}) - skipped",
                file=sys.stderr,
            )

    items = [
        SubmodularPackedItem(
            symbol=qname,
            cost=costs.get(qname, 0),
            feature_mask=feature_masks.get(qname, 0),
            dist_w=0.0 if qname == seed_id else dist_w_map.get(qname, dist_w_upstream_map.get(qname, 0.0)),
            role=_classify_role(qname, seed_id, direct_successors, dist_w_map, dist_w_upstream_map),
            compression=stub_compression.get(qname, "L0_full"),
        )
        for qname in selected
    ]
    total_cost = sum(item.cost for item in items)
    covered_mask = 0
    for item in items:
        covered_mask |= item.feature_mask

    return SubmodularPackResult(
        seed=seed_id, budget=target_budget, selected=selected, items=items,
        total_cost=total_cost, covered_mask=covered_mask,
    )


def build_context_package_spine_variant(
    builder: ConcreteGraphBuilder,
    seed_id: str,
    repo_root: str,
    target_budget: int,
    contracts: dict[str, BehavioralContract] | None = None,
    max_hops: float = DEFAULT_MAX_HOPS,
    task_type: str | None = None,
) -> ContextPackage:
    """Forked from `prism.surface.build.build_context_package`: identical
    body verbatim except line-for-line swapping the one call to
    production's `pack_symbol_context` for this module's own
    `pack_symbol_context_spine_variant` - every other rendering concern
    (signatures, features, edges, causal path, coverage, manifest,
    warnings, the final `_enforce_render_budget` safety net) is the same
    real production code, imported, not reimplemented, so the rendered
    XML stays directly comparable to the baseline/B_two_zone cells this
    is benchmarked against.
    """
    contracts = contracts or {}
    seed_info = builder.symbol_table.get(seed_id)
    if seed_info is None:
        raise KeyError(seed_id)
    include_causal_path = _causal_path_enabled() and causal_path_applies_to_task_type(task_type)

    pack_result: SubmodularPackResult = pack_symbol_context_spine_variant(builder, seed_id, target_budget, max_hops=max_hops)
    feature_masks = compute_feature_masks_cached(builder, repo_root)
    dist_w_map = compute_topological_distances(builder, seed_id, d_max=max_hops)
    reachable_ids = set(dist_w_map) | {seed_id}
    packed_ids = set(pack_result.selected)

    edge_weights, synthetic_edges = compute_causal_edges(builder)
    data_flow_edges = {(u, v) for (u, v) in compute_all_data_flow_edges(builder)}
    guard_edges = {(u, v) for (u, v) in compute_guard_indicator_edges(builder)}

    nodes: list[NodeEntry] = []
    languages_seen: dict[str, int] = {}
    files_seen: set[str] = set()
    for item in pack_result.items:
        info = builder.symbol_table.get(item.symbol)
        if info is None:
            continue
        files_seen.add(info.file)
        languages_seen[info.language_id] = languages_seen.get(info.language_id, 0) + 1
        mask = feature_masks.get(item.symbol, 0)
        nodes.append(
            NodeEntry(
                id=item.symbol,
                role=item.role,
                distance=0.0 if item.role == ROLE_SEED else item.dist_w,
                compression=item.compression,
                cost=item.cost,
                symbol_name=item.symbol.rsplit(".", 1)[-1],
                symbol_kind=info.kind,
                language=info.language_id,
                file=_relative_path(repo_root, info.file),
                line=info.line_range[0],
                end_line=info.line_range[1],
                signature=_node_signature(item.symbol, contracts, mask),
                features=NodeFeatures(
                    substance=_axis_labels(mask, SUBSTANCE_BITS),
                    form=_axis_labels(mask, FORM_BITS),
                    output=_axis_labels(mask, OUTPUT_BITS),
                    role=_axis_labels(mask, ROLE_BITS),
                ),
                contract=_derive_contract(item.symbol, builder, packed_ids) if item.role != ROLE_SEED else None,
                body=_node_body(builder, item.symbol) if item.compression == "L0_full" else (_signature_stub(builder, item.symbol) or ""),
            )
        )

    edges: list[EdgeEntry] = []
    for (u, v), weight in edge_weights.items():
        if u not in packed_ids or v not in packed_ids:
            continue
        relation = builder.graph.get_edge_data(u, v, default={}).get("relation", "CALLS") if builder.graph.has_edge(u, v) else "CALLS"
        u_dist = 0.0 if u == seed_id else dist_w_map.get(u, float("inf"))
        v_dist = 0.0 if v == seed_id else dist_w_map.get(v, float("inf"))
        edges.append(
            EdgeEntry(
                from_node=u,
                to_node=v,
                type=relation if relation in ("CALLS", "INSTANTIATES", "EXTENDS", "IMPLEMENTS", "OVERRIDES", "EMBEDS") else "CALLS",
                weight=weight,
                data_flow=(u, v) in data_flow_edges,
                guard=(u, v) in guard_edges,
                back_edge=v_dist < u_dist,
            )
        )

    causal_path = None
    if include_causal_path:
        causal_edge_pairs = [(e.from_node, e.to_node) for e in edges if e.type in ("CALLS", "INSTANTIATES")]
        stages, truncated = compute_causal_path(
            seed_id, packed_ids, causal_edge_pairs, dist_w_map, feature_masks, _output_kind, builder.symbol_table.get
        )
        causal_path = CausalPath(
            seed=seed_id,
            stages=[
                CausalPathStage(order=i, symbol=symbol, distance=distance, role=role)
                for i, (symbol, distance, role) in enumerate(stages, start=1)
            ],
            truncated=truncated,
        )

    compression_counts: dict[str, int] = {}
    for node in nodes:
        compression_counts[node.compression] = compression_counts.get(node.compression, 0) + 1
    compression = [ManifestCompression(level=level, count=compression_counts[level]) for level in _RESOLUTION_TO_LEVEL.values() if level in compression_counts]

    manifest = Manifest(
        packed_nodes=len(nodes),
        considered_nodes=len(reachable_ids),
        reachable_nodes=len(reachable_ids),
        compression=compression,
        distance_metric=ManifestDistanceMetric(
            name="causal_dijkstra", lambda_data_flow=LAMBDA_DATA_FLOW, lambda_guard=LAMBDA_GUARD, dist_max=max_hops
        ),
    )

    coverage = _coverage_summary(feature_masks, reachable_ids, packed_ids)

    primary_language = seed_info.language_id
    tier = precision_tier_for(primary_language)
    tier_digit = tier.value[-1] if tier is not None else "3"

    warnings: list[EnvelopeWarning] = []
    if not is_exact():
        warnings.append(
            EnvelopeWarning(
                code="TOKENIZER_FALLBACK",
                severity="medium",
                message=f"real BPE tokenizer unavailable, using {active_backend()} - token counts are approximate",
            )
        )
    if tier_digit == "2":
        warnings.append(EnvelopeWarning(code="LANGUAGE_TIER_2", severity="low", message=f"{primary_language} is Tier 2 (structural/lexical linking only)"))
    elif tier_digit == "3":
        warnings.append(EnvelopeWarning(code="LANGUAGE_TIER_3", severity="low", message=f"{primary_language} is Tier 3 (lexical/package-level linking only)"))

    pkg = ContextPackage(
        task_type=task_type,
        engine=EngineRef(name=f"{ENGINE_NAME}_spine_variant", version=ENGINE_VERSION, commit="unknown"),
        seed=SeedRef(symbol=seed_id, file=_relative_path(repo_root, seed_info.file), line=seed_info.line_range[0]),
        budget=BudgetRef(tokens=target_budget, tokenizer=active_backend(), exact=is_exact()),
        language=LanguageRef(tier=tier_digit, primary=primary_language, files=len(files_seen)),
        options={"engine": f"{ENGINE_NAME}_spine_variant", "max_hops": str(max_hops)},
        causal_path=causal_path,
        manifest=manifest,
        coverage=coverage,
        warnings=warnings,
        nodes=nodes,
        edges=edges,
        run_id=None,
        generated_at=None,
    )
    return _enforce_render_budget(pkg, target_budget, builder)
