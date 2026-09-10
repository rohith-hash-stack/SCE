"""v1.1+ Agent Surface: `build_context_package` - the adapter from a real
packing run to a `prism.surface.models.ContextPackage`.

This standardizes on **the causal engine** (`prism.packer.
submodular_knapsack.pack_symbol_context`, Prism v1.1's Causal Coupling &
Four-Axis Semantic Model), not the older `D_hybrid`
`ContextKnapsackPacker` `get_symbol_context` still uses - `prism.surface.
models.ManifestDistanceMetric`'s own fields (`lambda_data_flow`,
`lambda_guard`) are literally `prism.traversal.causal_weights`' own
constants, not a concept `D_hybrid` has at all, so the envelope's own
schema already names which engine it was designed to describe. The two
engines stay independently reachable (`get_symbol_context` vs.
`prism.slice`/`prism.explain`) rather than one replacing the other.

Every non-trivial per-node field reuses infrastructure already built for
the causal engine rather than re-deriving it a second way:

  - `NodeFeatures`/`NodeSignatureReturn.kind`: `prism.semantics.extractor.
    compute_feature_masks`' own per-axis `FeatureBit` masks.
  - `EdgeEntry.data_flow`/`guard`: `prism.traversal.causal_weights.
    compute_causal_edges`' own synthetic/real edge classification.
  - `NodeEntry.role`: `prism.packer.submodular_knapsack._classify_role`,
    computed once inside `pack_symbol_context` itself (the one place that
    already has both distance maps and the causal graph in scope).
  - `NodeContract`: the same call-site provenance detection (`_resolve_
    call_sites`/`_bindings`) `prism.packer.blast_radius` uses for upstream
    callers, applied here to whichever *packed* predecessor->node edge
    actually explains a node's presence in the pack.
"""
from __future__ import annotations

import os

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.contracts import BehavioralContract
from prism.language_tiers import precision_tier_for
from prism.packer.submodular_knapsack import (
    DEFAULT_MAX_HOPS,
    ROLE_SEED,
    SubmodularPackResult,
    pack_symbol_context,
)
from prism.parser.lang_config import CALL_NODE_TYPE, iter_scoped_nodes
from prism.semantics.bitmask import (
    ALL_KNOWN_BITS,
    FORM_BITS,
    OUTPUT_BITS,
    ROLE_BITS,
    SUBSTANCE_BITS,
    FeatureBit,
)
from prism.semantics.extractor import compute_feature_masks
from prism.slicer.tokenizer import active_backend, count_tokens, is_exact
from prism.traversal._data_flow_common import _bindings, _call_arguments, _decl_node_types, _node_key, _resolve_call_sites
from prism.traversal.causal_weights import LAMBDA_DATA_FLOW, LAMBDA_GUARD, compute_causal_edges
from prism.traversal.continuous_dijkstra import compute_topological_distances
from prism.parser.tree_sitter_loader import node_text
from prism.surface.models import (
    BudgetRef,
    ContextPackage,
    CoverageGap,
    CoverageSummary,
    EdgeEntry,
    EngineRef,
    EnvelopeWarning,
    FeatureCoverage,
    LanguageRef,
    Manifest,
    ManifestCompression,
    ManifestDistanceMetric,
    NodeContract,
    NodeEntry,
    NodeFeatures,
    NodeSignature,
    NodeSignatureParam,
    NodeSignatureReturn,
    SeedRef,
)

ENGINE_NAME = "prism-causal"
ENGINE_VERSION = "0.2.0"

_RESOLUTION_TO_LEVEL = {0: "L0_full", 1: "L1_pruned", 2: "L2_skeleton", 3: "L3_alias"}

#: `FeatureBit.OUTPUT_*` -> the spec's own human label
#: ("Predicate, Command, Query, Factory, Transformer, etc.").
_OUTPUT_BIT_TO_KIND: dict[FeatureBit, str] = {
    FeatureBit.OUTPUT_PREDICATE: "Predicate",
    FeatureBit.OUTPUT_COMMAND: "Command",
    FeatureBit.OUTPUT_QUERY: "Query",
    FeatureBit.OUTPUT_FACTORY: "Factory",
    FeatureBit.OUTPUT_TRANSFORMER: "Transformer",
    FeatureBit.OUTPUT_AGGREGATOR: "Aggregator",
    FeatureBit.OUTPUT_FLUENT: "Fluent",
    FeatureBit.OUTPUT_ASYNC_DEFERRED: "AsyncDeferred",
    FeatureBit.OUTPUT_GUARD: "Guard",
}


def _relative_path(repo_root: str, file_path: str) -> str:
    return os.path.relpath(file_path, repo_root)


def _axis_labels(mask: int, axis_bits: frozenset) -> str:
    """Comma-joined `FeatureBit` names (own axis prefix stripped) set in
    `mask`, sorted by bit position - `"NONE"` if the axis contributes no
    bit at all (should not happen for Substance/Output, which always set
    a fallback/priority bit, but Form/Role can legitimately be empty)."""
    names = [b.name.split("_", 1)[1] for b in sorted(axis_bits, key=int) if mask & int(b) and b.name]
    return ",".join(names) if names else "NONE"


def _output_kind(mask: int) -> str:
    for bit, kind in _OUTPUT_BIT_TO_KIND.items():
        if mask & int(bit):
            return kind
    return "Unknown"


def _node_signature(qname: str, contracts: dict[str, BehavioralContract], output_mask: int) -> NodeSignature:
    contract = contracts.get(qname)
    if contract is None:
        return NodeSignature(returns=NodeSignatureReturn(type=None, kind=_output_kind(output_mask)))
    params = [NodeSignatureParam(name=p.name, type=p.type, optional=p.default is not None) for p in contract.params]
    returns = NodeSignatureReturn(type=contract.return_type, kind=_output_kind(output_mask))
    return NodeSignature(params=params, returns=returns)


def _node_body(builder: ConcreteGraphBuilder, qname: str) -> str:
    info = builder.symbol_table.get(qname)
    if info is None:
        return ""
    parsed = builder.parsed_file(info.file)
    if parsed is None:
        return ""
    source = parsed.source.decode("utf-8", errors="replace")
    lines = source.splitlines()
    start, end = info.line_range
    return "\n".join(lines[max(start - 1, 0):end])


def _derive_contract(node_qname: str, builder: ConcreteGraphBuilder, packed_ids: set[str]) -> NodeContract | None:
    """The call site, if any, from another *packed* node that explains
    `node_qname`'s presence - reusing the exact same provenance detection
    `prism.packer.blast_radius.compute_upstream_callers` applies to
    upstream callers, generalized here to any packed predecessor."""
    if node_qname not in builder.graph:
        return None
    for caller, _target, data in builder.graph.in_edges(node_qname, data=True):
        if caller not in packed_ids or data.get("relation", "CALLS") not in ("CALLS", "INSTANTIATES"):
            continue
        def_node = builder.def_node(caller)
        info = builder.symbol_table.get(caller)
        if def_node is None or info is None:
            continue
        parsed = builder.parsed_file(info.file)
        if parsed is None:
            continue
        lang = parsed.language_id
        call_type = CALL_NODE_TYPE.get(lang)
        if not call_type:
            continue

        resolved = _resolve_call_sites(def_node, parsed, caller, builder)
        matching_calls = [
            call_node
            for call_node in iter_scoped_nodes(def_node, {call_type}, lang)
            if resolved.get(_node_key(call_node)) == node_qname
        ]
        if not matching_calls:
            continue
        call_node = min(matching_calls, key=lambda n: n.start_point[0])

        unpacks = None
        decl_types = _decl_node_types(lang)
        if decl_types:
            for decl_node in iter_scoped_nodes(def_node, decl_types, lang):
                for name, value in _bindings(decl_node, lang, parsed.source):
                    if value is not None and _node_key(value) == _node_key(call_node):
                        unpacks = name
        args = _call_arguments(call_node, lang)
        passes_args = None
        if args:
            rendered = ", ".join(node_text(a, parsed.source) for a in args)
            passes_args = rendered if len(rendered) <= 80 else rendered[:77] + "..."

        return NodeContract(target_id=caller, call_line=call_node.start_point[0] + 1, unpacks=unpacks, passes_args=passes_args)
    return None


def _coverage_summary(feature_masks: dict[str, int], reachable_ids: set[str], packed_ids: set[str]) -> CoverageSummary:
    reachable_mask = 0
    for qname in reachable_ids:
        reachable_mask |= feature_masks.get(qname, 0)
    covered_mask = 0
    per_bit_count: dict[FeatureBit, int] = {}
    for qname in packed_ids:
        mask = feature_masks.get(qname, 0)
        covered_mask |= mask
        for bit in ALL_KNOWN_BITS:
            if mask & int(bit):
                per_bit_count[bit] = per_bit_count.get(bit, 0) + 1

    present_bits = [b for b in sorted(ALL_KNOWN_BITS, key=int) if reachable_mask & int(b)]
    features = [
        FeatureCoverage(id=b.name, present=bool(covered_mask & int(b)), count=per_bit_count.get(b, 0)) for b in present_bits
    ]
    gaps = [
        CoverageGap(feature=b.name, reason="no_candidate_in_budget")
        for b in present_bits
        if not (covered_mask & int(b))
    ]
    covered_count = sum(1 for f in features if f.present)
    return CoverageSummary(
        total_features=len(features),
        covered_features=covered_count,
        omitted_features=len(features) - covered_count,
        features=features,
        gaps=gaps,
    )


def build_context_package(
    builder: ConcreteGraphBuilder,
    seed_id: str,
    repo_root: str,
    target_budget: int,
    contracts: dict[str, BehavioralContract] | None = None,
    max_hops: float = DEFAULT_MAX_HOPS,
    run_id: str | None = None,
    generated_at: str | None = None,
) -> ContextPackage:
    """Runs the causal engine (`pack_symbol_context`) and wraps its real
    output in a `ContextPackage`. Raises `KeyError` if `seed_id` isn't in
    `builder.symbol_table` - callers (the MCP tool layer) are expected to
    check that first and raise their own, more specific error."""
    contracts = contracts or {}
    seed_info = builder.symbol_table.get(seed_id)
    if seed_info is None:
        raise KeyError(seed_id)

    pack_result: SubmodularPackResult = pack_symbol_context(builder, seed_id, target_budget, max_hops=max_hops)
    feature_masks = compute_feature_masks(builder)
    dist_w_map = compute_topological_distances(builder, seed_id)
    reachable_ids = set(dist_w_map) | {seed_id}
    packed_ids = set(pack_result.selected)

    edge_weights, synthetic_edges = compute_causal_edges(builder)
    data_flow_edges = set()
    guard_edges = set()
    from prism.traversal.causal_weights import compute_all_data_flow_edges, compute_guard_indicator_edges

    for (u, v) in compute_all_data_flow_edges(builder):
        data_flow_edges.add((u, v))
    for (u, v) in compute_guard_indicator_edges(builder):
        guard_edges.add((u, v))

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
                compression=_RESOLUTION_TO_LEVEL[0],
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
                body=_node_body(builder, item.symbol),
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

    return ContextPackage(
        engine=EngineRef(name=ENGINE_NAME, version=ENGINE_VERSION, commit="unknown"),
        seed=SeedRef(symbol=seed_id, file=_relative_path(repo_root, seed_info.file), line=seed_info.line_range[0]),
        budget=BudgetRef(tokens=target_budget, tokenizer=active_backend(), exact=is_exact()),
        language=LanguageRef(tier=tier_digit, primary=primary_language, files=len(files_seen)),
        options={"engine": ENGINE_NAME, "max_hops": str(max_hops)},
        manifest=manifest,
        coverage=coverage,
        warnings=warnings,
        nodes=nodes,
        edges=edges,
        run_id=run_id,
        generated_at=generated_at,
    )
