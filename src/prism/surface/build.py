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
    compute_feature_masks_cached`' own per-axis `FeatureBit` masks.
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
    _signature_stub,
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
from prism.semantics.extractor import compute_feature_masks_cached
from prism.slicer.tokenizer import active_backend, count_tokens, is_exact
from prism.traversal._data_flow_common import _bindings, _call_arguments, _decl_node_types, _node_key, _resolve_call_sites
from prism.traversal.causal_weights import LAMBDA_DATA_FLOW, LAMBDA_GUARD, compute_causal_edges
from prism.traversal.continuous_dijkstra import compute_topological_distances
from prism.parser.tree_sitter_loader import node_text
from prism.surface.causal_path import compute_causal_path
from prism.surface.models import (
    BudgetRef,
    CausalPath,
    CausalPathStage,
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
    """Phase F (Issue #30 - "file_path must be POSIX-normalized"):
    `os.path.relpath` returns backslash-separated components on Windows -
    every `NodeEntry.file`/`SeedRef.file` this module ever produces goes
    into the public XML envelope, so a Windows build host would leak
    backslash paths into an otherwise-portable document. The same
    backslash-to-forward-slash normalization `prism.scanner.path_norm.
    get_module_parts` already applies for module-name derivation, reused
    here for consistency rather than a second, possibly-drifting
    convention. A no-op on POSIX (`os.path.relpath` never contains a
    backslash there), so this changes nothing observable in this
    environment - real, but only externally visible on Windows."""
    return os.path.relpath(file_path, repo_root).replace("\\", "/")


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
    return NodeSignature(params=params, returns=returns, docstring=contract.docstring)


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


def _causal_path_enabled() -> bool:
    """The env-var-controlled *default* for `include_causal_path` below -
    read fresh on every call (never cached at import time), so an A/B
    harness can flip `PRISM_ENABLE_CAUSAL_PATH` between two runs of the
    same process without touching any code. Unset, or anything other
    than `"0"`/`"false"`/`"False"`, means enabled - matching `include_
    causal_path`'s own previous unconditional-`True` default, so a
    caller that has never heard of this env var sees no behavior
    change."""
    return os.environ.get("PRISM_ENABLE_CAUSAL_PATH", "1") not in ("0", "false", "False")


#: Phase D Invariant 1 (Blockers B2 / Issues #37-38): task types shaped
#: like T13's own blast-radius/overview retrieval - many candidate
#: upstream callers or a broad multi-concern scan, never "the" single
#: forward story a causal_path tells. Real values only (benchmarks.
#: ground_truth.schema.EvaluationTask.task_type's own literal enum is
#: exactly {"chain", "blast", "redundancy", "architecture", "debug"} -
#: "T13"/"T02" are filename conventions, never real task_type strings,
#: so they are deliberately not listed here).
_BLAST_STYLE_TASK_TYPES = frozenset({"blast", "architecture", "redundancy"})


def causal_path_applies_to_task_type(task_type: str | None) -> bool:
    """`task_type=None` (unknown, or a caller with no benchmark-task
    context at all - an ad-hoc MCP query, say) defaults to `True` - the
    causal_path feature's own pre-Phase-D behavior, completely
    unaffected for any caller that never supplies a task type. A real
    T02-style task ("chain"/"debug") also returns `True`. Only the
    T13-style task types above return `False`."""
    if task_type is None:
        return True
    return task_type not in _BLAST_STYLE_TASK_TYPES


def build_context_package(
    builder: ConcreteGraphBuilder,
    seed_id: str,
    repo_root: str,
    target_budget: int,
    contracts: dict[str, BehavioralContract] | None = None,
    max_hops: float = DEFAULT_MAX_HOPS,
    run_id: str | None = None,
    generated_at: str | None = None,
    include_causal_path: bool | None = None,
    task_type: str | None = None,
) -> ContextPackage:
    """Runs the causal engine (`pack_symbol_context`) and wraps its real
    output in a `ContextPackage`. Raises `KeyError` if `seed_id` isn't in
    `builder.symbol_table` - callers (the MCP tool layer) are expected to
    check that first and raise their own, more specific error.

    `include_causal_path` (default `None`, resolved per call to
    `_causal_path_enabled() and causal_path_applies_to_task_type(task_
    type)` - i.e. `PRISM_ENABLE_CAUSAL_PATH` AND a T02-shaped task type,
    unless a caller says otherwise): the T02/chain/debug-style
    single-seed-forward-chain use case this envelope was designed
    around. Pass `False` explicitly for a blast-radius (upstream
    callers, not a forward chain) or overview (broad, multi-concern)
    retrieval, where a single forward `<causal_path>` either doesn't
    apply or would mislead - that explicit `True`/`False` always wins
    over both the env var and `task_type` (Phase D: task_type is only
    ever consulted when `include_causal_path` is left `None`, exactly
    the same precedence the env var already had). Either way,
    `prism.surface.models.ContextPackage.causal_path` stays `None`
    rather than ever emitting an empty `<causal_path>` block.

    `task_type` (Phase D, Invariant 1): see `causal_path_applies_to_
    task_type`'s own docstring. `None` (the default - every pre-Phase-D
    caller, and any caller without a benchmark-task context at all)
    means "no task-type gating", identical to the function's own
    pre-Phase-D behavior."""
    if include_causal_path is None:
        include_causal_path = _causal_path_enabled() and causal_path_applies_to_task_type(task_type)
    contracts = contracts or {}
    seed_info = builder.symbol_table.get(seed_id)
    if seed_info is None:
        raise KeyError(seed_id)

    pack_result: SubmodularPackResult = pack_symbol_context(builder, seed_id, target_budget, max_hops=max_hops)
    feature_masks = compute_feature_masks_cached(builder, repo_root)
    # Zero-Debt Hardening Pass, Task 3: compute_topological_distances now
    # defaults its own d_max to 5.0 (was None/unbounded). This call's
    # dist_w_map feeds reachable_ids (-> manifest.considered_nodes/
    # reachable_nodes), the causal path's sink tie-break, and the
    # back_edge flag below - all of which are meant to reflect "reachable
    # within this call's own max_hops", the same bound pack_symbol_
    # context (called just above) already uses to build packed_ids. d_max
    # is passed explicitly here so a caller with a real max_hops override
    # (e.g. tests/test_causal_path.py's max_hops=20.0 deep-chain case)
    # keeps getting the full search it asks for, instead of being
    # silently capped at the new global default of 5.0 regardless of
    # what it requested.
    dist_w_map = compute_topological_distances(builder, seed_id, d_max=max_hops)
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
                # fix-stub-pack-distance-1-tight-budget (Zero-Debt
                # Hardening Pass, Task 1): a stub-packed item's `body`
                # must be the same signature-only text its cost was
                # actually priced against (`_signature_stub`, computed
                # once in submodular_knapsack.py's own fixup and
                # recomputed identically here - deterministic given the
                # same source, so never out of sync with `item.cost`),
                # not the full body `_node_body` would return.
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

    return ContextPackage(
        task_type=task_type,
        engine=EngineRef(name=ENGINE_NAME, version=ENGINE_VERSION, commit="unknown"),
        seed=SeedRef(symbol=seed_id, file=_relative_path(repo_root, seed_info.file), line=seed_info.line_range[0]),
        budget=BudgetRef(tokens=target_budget, tokenizer=active_backend(), exact=is_exact()),
        language=LanguageRef(tier=tier_digit, primary=primary_language, files=len(files_seen)),
        options={"engine": ENGINE_NAME, "max_hops": str(max_hops)},
        causal_path=causal_path,
        manifest=manifest,
        coverage=coverage,
        warnings=warnings,
        nodes=nodes,
        edges=edges,
        run_id=run_id,
        generated_at=generated_at,
    )
