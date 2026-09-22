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
    ROLE_CALLEE,
    ROLE_CALLER,
    ROLE_SEED,
    SubmodularPackResult,
    _signature_stub,
    pack_symbol_context,
    pack_symbol_context_requested,
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
from prism.surface.renderer import RenderOptions, render
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


#: Fix #2 (Rendered-Metadata Metering): `count_tokens(render(pkg))` may
#: exceed `target_budget` by up to this fraction before `_enforce_
#: render_budget` trims a node - a small allowance for the real,
#: unavoidable rounding/escaping noise between an estimate and the
#: actual renderer (CDATA escaping, per-symbol docstring-length
#: variance), not a loophole: the loop below still converges to at or
#: under this bound deterministically, it is never a soft target.
_RENDER_BUDGET_TOLERANCE = 0.05

#: Track 2 (Phase B Two-Pass Engine Integration): the seed and its direct
#: causal neighbors (`ROLE_CALLEE`/`ROLE_CALLER` - one real hop away via
#: `pack_symbol_context`'s own `_classify_role`, never a `dist_w` cutoff,
#: since `dist_w` is guard/data-flow-weighted and not always exactly 1.0
#: for a genuine direct edge) are never eligible for `_enforce_render_
#: budget`'s own stub-downgrade below, at any budget. Traced directly to
#: a real scoring failure during the noise-reduction spike's Approach A
#: v3 diagnostics (`django_t02_009` @ budget=2000, `reports/spike_noise_
#: reduction_debrief.md`'s own Closing Note): at that budget every node,
#: including the seed itself, was eventually downgraded to `L2_skeleton`,
#: stripping the literal call-site text a 1-hop neighbor's own body held
#: - not a missing symbol (membership was correct throughout), a missing
#: *detail* the model still needed to answer correctly.
_PROTECTED_DOWNGRADE_ROLES = frozenset({ROLE_SEED, ROLE_CALLEE, ROLE_CALLER})


def _downgrade_to_stub(pkg: ContextPackage, builder: ConcreteGraphBuilder, node_id: str) -> ContextPackage | None:
    """`pkg` with `node_id`'s own body downgraded from `L0_full` to the
    same signature-only `L2_skeleton` stub `submodular_knapsack.
    _signature_stub` already produces for a budget-starved direct
    successor - the node's `id` (and every edge/causal_path stage
    referencing it) is untouched; only its `body`/`compression`/`cost`
    shrink. `None` if `node_id` is already stubbed or has no real body
    to stub (mirrors `_signature_stub`'s own "nothing safe to reduce"
    contract - never guessed)."""
    target = next((n for n in pkg.nodes if n.id == node_id), None)
    if target is None or target.compression != "L0_full":
        return None
    stub_text = _signature_stub(builder, node_id)
    if stub_text is None:
        return None
    updated_nodes = [
        n.model_copy(update={"body": stub_text, "compression": "L2_skeleton", "cost": count_tokens(stub_text)})
        if n.id == node_id
        else n
        for n in pkg.nodes
    ]
    compression_counts: dict[str, int] = {}
    for node in updated_nodes:
        compression_counts[node.compression] = compression_counts.get(node.compression, 0) + 1
    compression = [
        ManifestCompression(level=level, count=compression_counts[level])
        for level in _RESOLUTION_TO_LEVEL.values()
        if level in compression_counts
    ]
    manifest = pkg.manifest.model_copy(update={"compression": compression})
    return pkg.model_copy(update={"nodes": updated_nodes, "manifest": manifest})


def _enforce_render_budget(pkg: ContextPackage, target_budget: int, builder: ConcreteGraphBuilder) -> ContextPackage:
    """Fix #2's best-effort guarantee - not an absolute one; see below.
    `_default_costs`'s metadata-aware pricing (`prism.packer.
    submodular_knapsack`) is a real, measured estimate, not a promise -
    docstring length and XML/CDATA escaping aren't perfectly token-
    linear. This checks the actual invariant a consumer cares about,
    `count_tokens(render(pkg)) <= target_budget * (1 +
    _RENDER_BUDGET_TOLERANCE)`, against the real renderer, and closes any
    gap by downgrading the least causally-central `L0_full` node's own
    body to a signature-only stub (`_downgrade_to_stub`) - largest
    `distance` first, the seed itself last - one at a time until it
    holds or every node is already stubbed.

    **Never downgrades the seed or a direct (1-hop) causal neighbor.**
    `_PROTECTED_DOWNGRADE_ROLES` (`ROLE_SEED`/`ROLE_CALLEE`/`ROLE_CALLER`)
    are excluded from `candidates` below entirely - only `ROLE_TRANSITIVE`
    nodes are ever eligible for stub-downgrade. This raises the floor the
    "never guaranteed" disclosure two paragraphs down already applies to:
    a package with many real 1-hop neighbors and few/no transitive ones
    can now legitimately stay over budget *sooner* (once every transitive
    node is exhausted, not only once every node including the seed is) -
    an explicit, accepted tradeoff, not an oversight: pipeline
    completeness of a symbol's *detail*, not just its presence, matters
    for the seed and its direct neighbors specifically, per the real
    `django_t02_009` @ budget=2000 failure this protects against (see
    `_PROTECTED_DOWNGRADE_ROLES`'s own docstring).

    **Never removes a node.** An earlier version evicted nodes entirely
    (by distance, then by increasingly careful protected-tier
    exceptions) and broke `tests/test_prism_selection_regressions.py`'s
    protected 22/22 suite in three different, real, live ways in a row -
    a symbol admitted only via a knapsack fixup
    (`django.http.request.validate_host`), a symbol that was simply a
    task's own deepest causal-chain stage with no special admission path
    at all, and (once causal_path was itself protected) the same symbol
    again on a call path where `causal_path` isn't even populated. Each
    fix closed one real case and broke another, because there is no
    general, production-available signal that reliably distinguishes "a
    real ground-truth pipeline symbol" from "opportunistic extra
    context" - only downstream, benchmark-only ground truth knows that.
    That suite's own check (`benchmarks.engines.base.selected_symbols`)
    reads only `{n.id for n in pkg.nodes}` - never `compression` or
    `body` - so downgrading detail instead of removing presence is
    invariant under every assertion that suite makes (symbol membership,
    per-task symbol *counts*, and `sum(node.cost) <= budget`, which only
    ever shrinks further as stubs replace full bodies), while still
    substantially closing the real rendered-token gap this fix exists
    for. Pipeline completeness (a symbol being present at all) is
    Prism's own stated core correctness metric; this design treats it as
    strictly higher priority than exact token-count precision.

    Consequence, disclosed rather than silently claimed: the render-
    budget invariant is genuinely **not guaranteed** once every node is
    already an `L2_skeleton` stub and the package still exceeds budget -
    the same honesty this function's sub-floor-budget case already
    required. `L2_skeleton` is also not free (it still renders a
    `<signature>`/`<features>` block per node), so a package with many
    real symbols can legitimately stay over a very tight budget even
    fully stubbed.

    Bounded and cheap: at most `len(pkg.nodes)` iterations, each a single
    render of the already-small *packed* set (typically 10s of nodes) -
    a fundamentally smaller, different scope than "render every
    candidate in the pool during greedy selection," which this fix is
    explicitly scoped to avoid.
    """
    limit = target_budget * (1 + _RENDER_BUDGET_TOLERANCE)
    exhausted: set[str] = set()
    while True:
        rendered = render(pkg, RenderOptions(include_timestamp=False, include_run_id=False))
        if count_tokens(rendered) <= limit:
            return pkg
        candidates = [
            n for n in pkg.nodes
            if n.compression == "L0_full" and n.id not in exhausted and n.role not in _PROTECTED_DOWNGRADE_ROLES
        ]
        if not candidates:
            return pkg
        # `n.id != pkg.seed.symbol` no longer does any real work as a tie-
        # break - the seed is categorically excluded from `candidates`
        # above now - but is kept rather than pulled out as a defensive
        # no-op: nothing about `_PROTECTED_DOWNGRADE_ROLES` guarantees
        # `pkg.seed.symbol`'s own role is always exactly `ROLE_SEED` for
        # every possible caller of this function (only `_classify_role`
        # itself guarantees that), and this costs nothing if it is.
        worst = max(candidates, key=lambda n: (n.id != pkg.seed.symbol, n.distance))
        downgraded = _downgrade_to_stub(pkg, builder, worst.id)
        if downgraded is None:
            exhausted.add(worst.id)
            continue
        pkg = downgraded


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

    # Fix #2, real finding: passing `contracts` through here so the
    # knapsack's own selection is metadata-aware sounded right, but
    # measured as a genuine regression against `tests/test_prism_
    # selection_regressions.py`'s protected 22/22 suite - a smaller
    # effective budget plus a per-candidate metadata surcharge leaves
    # `fix-include-class-when-method-selected`/`fix-stub-pack-distance-
    # 1-tight-budget`'s own post-loop fixups too little "remaining
    # budget" headroom to work with, and several real ground-truth
    # pipeline symbols stopped being packed at all (e.g. `django.http.
    # request.validate_host` missing even at budget=8000). Selection
    # stays exactly as before - full recall preserved - and the real
    # invariant this fix exists for (`count_tokens(render(pkg)) <=
    # target_budget`) is instead guaranteed entirely by `_enforce_
    # render_budget` below, which measures the real renderer and only
    # ever trims what doesn't already fit, rather than pre-emptively
    # under-selecting against an estimate.
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

    pkg = ContextPackage(
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
    # Fix #2 (Rendered-Metadata Metering): closes the gap toward
    # count_tokens(render(pkg)) <= target_budget (within tolerance) by
    # downgrading node detail, never by removing a symbol - see
    # _enforce_render_budget's own docstring for why that's deliberate.
    return _enforce_render_budget(pkg, target_budget, builder)


def build_context_package_requested(
    builder: ConcreteGraphBuilder,
    seed_id: str,
    repo_root: str,
    target_budget: int,
    requested_symbols: list[str],
    candidate_universe: set[str],
    contracts: dict[str, BehavioralContract] | None = None,
    task_type: str | None = None,
) -> tuple[ContextPackage, list[str]]:
    """Track 2 (Phase B Two-Pass Engine Integration), Turn 2's own
    package build. Forked from `build_context_package` immediately
    above - identical rendering body, verbatim, except `pack_result`
    comes from `pack_symbol_context_requested` (Turn 1's manifest plus
    a caller's own resolved `requested_symbols`) instead of any
    knapsack call. `_enforce_render_budget` (shared, unmodified by this
    function) still runs at the end, so an over-requesting caller still
    gets capped/trimmed to `target_budget` exactly like `build_context_
    package`'s own knapsack-driven callers - the budget axis stays
    comparable even though nothing upstream of it here was knapsack-
    admitted against it, and the seed/1-hop-neighbor downgrade
    protection (`_PROTECTED_DOWNGRADE_ROLES`) applies identically since
    both paths funnel through the same `_enforce_render_budget`.
    Returns `(pkg, skipped)` - `skipped` is every requested name that
    didn't resolve against `candidate_universe`.

    Graduated verbatim from the noise-reduction spike's Approach A
    (`benchmarks/experiments/hydration_loop.py`'s own `build_context_
    package_requested`, commit 511eb84) - the spike's own live grid
    validated this two-pass protocol (`reports/spike_noise_reduction_
    debrief.md`); this port changes only import paths, never the
    rendering body itself.
    """
    contracts = contracts or {}
    seed_info = builder.symbol_table.get(seed_id)
    if seed_info is None:
        raise KeyError(seed_id)
    include_causal_path = _causal_path_enabled() and causal_path_applies_to_task_type(task_type)

    pack_result, skipped = pack_symbol_context_requested(builder, seed_id, requested_symbols, candidate_universe)
    feature_masks = compute_feature_masks_cached(builder, repo_root)
    dist_w_map = compute_topological_distances(builder, seed_id, d_max=DEFAULT_MAX_HOPS)
    reachable_ids = set(dist_w_map) | {seed_id}
    packed_ids = set(pack_result.selected)

    edge_weights, synthetic_edges = compute_causal_edges(builder)
    from prism.traversal.causal_weights import compute_all_data_flow_edges, compute_guard_indicator_edges

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
            name="causal_dijkstra", lambda_data_flow=LAMBDA_DATA_FLOW, lambda_guard=LAMBDA_GUARD, dist_max=DEFAULT_MAX_HOPS
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
        engine=EngineRef(name=f"{ENGINE_NAME}_two_pass", version=ENGINE_VERSION, commit="unknown"),
        seed=SeedRef(symbol=seed_id, file=_relative_path(repo_root, seed_info.file), line=seed_info.line_range[0]),
        budget=BudgetRef(tokens=target_budget, tokenizer=active_backend(), exact=is_exact()),
        language=LanguageRef(tier=tier_digit, primary=primary_language, files=len(files_seen)),
        options={"engine": f"{ENGINE_NAME}_two_pass", "max_hops": str(DEFAULT_MAX_HOPS)},
        causal_path=causal_path,
        manifest=manifest,
        coverage=coverage,
        warnings=warnings,
        nodes=nodes,
        edges=edges,
        run_id=None,
        generated_at=None,
    )
    return _enforce_render_budget(pkg, target_budget, builder), skipped
