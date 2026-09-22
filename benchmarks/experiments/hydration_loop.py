"""Phase B spike, Approach A: Two-Pass Hydration Protocol.

Unlike Approach B (rendering-only) and Approach C (a forked selection
algorithm, dropped after a diagnosed negative result - see
`knapsack_spine_variant.py`'s own docstring and the debrief), Approach A
never re-derives *which* symbols matter algorithmically at all. It asks
the LLM itself, in a cheap first turn, then renders only what it asked
for. `docs/design_formalism.md` Sec 10.4's own closing finding motivated
Approach C's topological hypothesis; Approach A tests a different one -
that a model given a compact index of *everything reachable* can pick
its own relevant subset more reliably than either pure density (baseline)
or pure topology (C) can, precisely because it can use the actual task
semantics neither of those mechanisms ever sees.

### Protocol

**Turn 1 (manifest selection)**: `_build_candidate_index` renders every
symbol reachable from the seed (identical candidate universe
`pack_symbol_context`'s own `candidate_symbols` list is built from - see
that function's own body) as one compact `qualified_name|role|kind` line
each, no knapsack, no L0 bodies. `client.complete()` (not
`run_tsr_prompt`, which is shaped for the final scored answer) sends this
plus the real task prompt, asking for `{"thought_process": ...,
"requested_symbols": [...]}` - `OpenAICompatibleClient.complete()`
already forces `response_format={"type": "json_object"}` on every call,
so no fenced-JSON convention is needed here (unlike the T02 debug
contract, which targets an SLM that doesn't reliably honor JSON mode).

**Turn 2 (targeted hydration & scoring)**: `pack_symbol_context_
requested`/`build_context_package_requested` build a real, renderable
`ContextPackage` containing exactly the seed plus whichever requested
symbols resolve to a real candidate in Turn 1's own universe (a name the
model invented that never appeared in the manifest is dropped, not
silently rendered as if real - `skipped_hallucinated` in the result row
counts these). No density competition, no spine tiering - the model's
own selection *is* the selection. `_enforce_render_budget` (real
production code, unmodified) still caps the result to `target_budget`,
the same safety net every other approach in this spike goes through, so
the budget axis stays comparable across all of them even though nothing
here is knapsack-admitted against it. `run_tsr_prompt` then scores this
exactly like every other approach's own Turn (2 in this case).

Production `prism.packer.submodular_knapsack`/`prism.surface.build` are
imported for reuse, never modified - the same discipline `knapsack_
spine_variant.py` already established for this spike.
"""
from __future__ import annotations

import json
import time

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.contracts import BehavioralContract
from prism.packer.blast_radius import compute_upstream_callers
from prism.packer.submodular_knapsack import (
    DEFAULT_MAX_HOPS,
    DEFAULT_UPSTREAM_MAX_HOPS,
    ROLE_SEED,
    SeedNotFoundError,
    SubmodularPackedItem,
    SubmodularPackResult,
    _classify_role,
    _default_costs,
    suggest_similar_seeds,
)
from prism.semantics.bitmask import FORM_BITS, OUTPUT_BITS, ROLE_BITS, SUBSTANCE_BITS
from prism.semantics.extractor import compute_feature_masks_cached
from prism.slicer.tokenizer import active_backend, is_exact
from prism.surface.build import (
    ENGINE_NAME,
    ENGINE_VERSION,
    _RESOLUTION_TO_LEVEL,
    _axis_labels,
    _causal_path_enabled,
    _coverage_summary,
    _derive_contract,
    _enforce_render_budget,
    _node_body,
    _node_signature,
    _output_kind,
    _relative_path,
    causal_path_applies_to_task_type,
)
from prism.surface.causal_path import compute_causal_path
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
from prism.traversal._cache_keys import snapshot_file_hash_set
from prism.traversal.causal_weights import LAMBDA_DATA_FLOW, LAMBDA_GUARD, compute_all_data_flow_edges, compute_causal_edges, compute_guard_indicator_edges
from prism.traversal.continuous_dijkstra import build_causal_graph, compute_topological_distances

from benchmarks.metrics.cpi import cpi_strict
from benchmarks.metrics.fpr import fpr
from benchmarks.runner import DEBUG_TASK_RESPONSE_CONTRACT, SYSTEM_PROMPT, _ground_truth_universe, score_tsr_response
from benchmarks.tsr.client import OpenAICompatibleClient, run_tsr_prompt
from benchmarks.tsr.scorer_debug import _strip_code_fence
from prism.surface.renderer import RenderOptions, render

TURN1_SYSTEM_PROMPT = (
    "You are a senior software engineer investigating a codebase. You will be given a compact "
    "<candidate_index> - every symbol reachable from a seed function, one per line as "
    "qualified_name|role|kind (role is one of seed/callee/caller/transitive) - followed by a real "
    "task. Identify which of these symbols are actually on the direct execution path relevant to "
    "answering the task. Only name symbols that appear in the index - never invent one."
)


def _build_candidate_index(
    builder: ConcreteGraphBuilder, seed_id: str, max_hops: float = DEFAULT_MAX_HOPS, upstream_max_hops: float = DEFAULT_UPSTREAM_MAX_HOPS
) -> tuple[str, set[str]]:
    """`(manifest_text, candidate_universe)` - the identical candidate
    universe `pack_symbol_context`'s own body builds (seed + every
    forward-reachable node within `max_hops` + every upstream caller
    within `upstream_max_hops`), rendered as one compact
    `qualified_name|role|kind` line per real (symbol-table-resolved)
    candidate. Budget-independent by construction, same as `dist_w_map`
    itself - see this module's own docstring for why that's expected,
    not a gap: what budget affects here is Turn 2's own render cap, not
    which symbols are reachable in the first place.
    """
    graph = build_causal_graph(builder)
    dist_w_map = compute_topological_distances(builder, seed_id, d_max=max_hops)
    upstream_callers = compute_upstream_callers(builder, seed_id)
    dist_w_upstream_map = {symbol: caller.dist_w_upstream for symbol, caller in upstream_callers.items()}
    direct_successors = set(graph.successors(seed_id)) if seed_id in graph else set()

    candidates = {seed_id} | {n for n in dist_w_map if dist_w_map[n] <= max_hops} | {n for n in dist_w_upstream_map if dist_w_upstream_map[n] <= upstream_max_hops}

    lines = []
    resolved: set[str] = set()
    for qname in sorted(candidates):
        info = builder.symbol_table.get(qname)
        if info is None:
            continue
        role = _classify_role(qname, seed_id, direct_successors, dist_w_map, dist_w_upstream_map)
        lines.append(f"{qname}|{role}|{info.kind}")
        resolved.add(qname)
    manifest = "<candidate_index>\n" + "\n".join(lines) + "\n</candidate_index>"
    return manifest, resolved


def _turn1_user_prompt(manifest: str, task_prompt: str) -> str:
    return (
        f"{manifest}\n\nTask:\n{task_prompt}\n\n"
        'Respond with a JSON object: {"thought_process": "1-2 sentences on why", '
        '"requested_symbols": ["qualified.name", ...]} - requested_symbols ordered seed first, '
        "then the causal stages in execution order. Respond with this JSON object and nothing else."
    )


def _parse_requested_symbols(response_text: str) -> tuple[list[str], bool]:
    """`(requested_symbols, parsed_ok)` - `parsed_ok=False` (empty list)
    for anything that isn't a JSON object with a `requested_symbols`
    list of strings, mirroring `scorer_debug.extract_flat_symbols`'s own
    "never a bare parse exception" contract, but returning a flag
    instead of raising - a Turn 1 parse failure degrades Turn 2 to
    "only the seed" rather than aborting the whole cell.
    """
    try:
        candidate = _strip_code_fence(response_text.strip())
        obj = json.loads(candidate)
    except json.JSONDecodeError:
        return [], False
    if not isinstance(obj, dict):
        return [], False
    symbols = obj.get("requested_symbols")
    if not isinstance(symbols, list) or not all(isinstance(s, str) for s in symbols):
        return [], False
    return symbols, True


def pack_symbol_context_requested(
    builder: ConcreteGraphBuilder,
    seed_id: str,
    requested_symbols: list[str],
    candidate_universe: set[str],
) -> tuple[SubmodularPackResult, list[str]]:
    """Turn 2's own "selection": no knapsack, no density competition -
    the seed plus whichever of `requested_symbols` resolve to a real
    member of `candidate_universe` (Turn 1's own manifest - a name the
    model invented that was never in it is dropped, not rendered).
    Returns `(result, skipped)`; `skipped` is every requested name that
    didn't resolve, for logging. `result.total_cost`/`.items` still use
    real BPE costs (`_default_costs`, unchanged from production) - the
    only thing forked here is *which* symbols end up in the pack, not
    how any of them are priced or rendered.
    """
    if seed_id not in builder.symbol_table:
        raise SeedNotFoundError(seed_id, suggest_similar_seeds(builder, seed_id))

    selected: list[str] = [seed_id]
    seen = {seed_id}
    skipped: list[str] = []
    for qname in requested_symbols:
        if qname in seen:
            continue
        if qname not in candidate_universe or builder.symbol_table.get(qname) is None:
            skipped.append(qname)
            continue
        selected.append(qname)
        seen.add(qname)

    with snapshot_file_hash_set(builder.repo_root):
        graph = build_causal_graph(builder)
        feature_masks = compute_feature_masks_cached(builder, builder.repo_root)
        dist_w_map = compute_topological_distances(builder, seed_id, d_max=DEFAULT_MAX_HOPS)
        upstream_callers = compute_upstream_callers(builder, seed_id)
        dist_w_upstream_map = {symbol: caller.dist_w_upstream for symbol, caller in upstream_callers.items()}
        costs = _default_costs(builder, selected)

    # fix-include-class-when-method-selected (verbatim fork, matching
    # every other approach in this spike): a class the model's own
    # requested_symbols omitted but one of its own admitted methods
    # needs is still promoted in - the model wasn't asked to name
    # container classes explicitly, and every sibling approach gets this
    # fixup for free from production, so Turn 2 should too for a fair
    # comparison.
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
                costs.update(_default_costs(builder, [class_qname]))
            class_cost = costs.get(class_qname, 0)
            if class_cost > 0:
                reordered_selected.append(class_qname)
                promoted_classes.add(class_qname)
                selected_set.add(class_qname)
                running_cost += class_cost
        reordered_selected.append(qname)
    selected = reordered_selected

    items = [
        SubmodularPackedItem(
            symbol=qname,
            cost=costs.get(qname, 0),
            feature_mask=feature_masks.get(qname, 0),
            dist_w=0.0 if qname == seed_id else dist_w_map.get(qname, dist_w_upstream_map.get(qname, 0.0)),
            role=_classify_role(qname, seed_id, direct_successors, dist_w_map, dist_w_upstream_map),
            compression="L0_full",
        )
        for qname in selected
    ]
    total_cost = sum(item.cost for item in items)
    covered_mask = 0
    for item in items:
        covered_mask |= item.feature_mask

    result = SubmodularPackResult(seed=seed_id, budget=0, selected=selected, items=items, total_cost=total_cost, covered_mask=covered_mask)
    return result, skipped


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
    """Forked from `prism.surface.build.build_context_package` (same
    pattern `knapsack_spine_variant.build_context_package_spine_variant`
    already established for this spike): identical rendering body,
    verbatim, except `pack_result` comes from `pack_symbol_context_
    requested` instead of any knapsack call. `_enforce_render_budget`
    (real, unmodified production code) still runs at the end, so a
    model that over-requests still gets capped/trimmed to
    `target_budget` exactly like every other approach here - the budget
    axis stays comparable even though nothing upstream of it was
    knapsack-admitted against it. Returns `(pkg, skipped)`.
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
        engine=EngineRef(name=f"{ENGINE_NAME}_hydration_requested", version=ENGINE_VERSION, commit="unknown"),
        seed=SeedRef(symbol=seed_id, file=_relative_path(repo_root, seed_info.file), line=seed_info.line_range[0]),
        budget=BudgetRef(tokens=target_budget, tokenizer=active_backend(), exact=is_exact()),
        language=LanguageRef(tier=tier_digit, primary=primary_language, files=len(files_seen)),
        options={"engine": f"{ENGINE_NAME}_hydration_requested", "max_hops": str(DEFAULT_MAX_HOPS)},
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


def run_hydration_cell(client: OpenAICompatibleClient, engine, task, budget: int, seed: int) -> dict:
    """Runs both turns of the protocol for one (task, budget, seed) cell
    and returns a result row shaped like `run_spike.run_cell`'s own, plus
    hydration-specific diagnostics (`turn1_prompt_tokens`,
    `turn2_prompt_tokens`, `requested_count`, `skipped_hallucinated`,
    `turn1_parsed_ok`).
    """
    builder = engine._builder
    manifest, candidate_universe = _build_candidate_index(builder, task.seed_symbol)

    turn1_user = _turn1_user_prompt(manifest, task.prompt)
    t0 = time.monotonic()
    turn1_call = client.complete(
        None, TURN1_SYSTEM_PROMPT, turn1_user, seed=seed, task_id=task.task_id, engine="prism_v11_A_turn1",
    )
    requested_symbols, parsed_ok = _parse_requested_symbols(turn1_call.content)

    pkg, skipped = build_context_package_requested(
        builder, task.seed_symbol, engine._repo_root, budget, requested_symbols, candidate_universe,
        contracts=engine._contracts, task_type=task.task_type,
    )
    candidate_symbols = {n.id for n in pkg.nodes}

    options = RenderOptions(include_timestamp=False, include_run_id=False)
    rendered_xml = render(pkg, options)

    task_prompt = task.prompt
    if task.task_type == "debug":
        task_prompt = task.prompt + DEBUG_TASK_RESPONSE_CONTRACT

    results = run_tsr_prompt(
        client, SYSTEM_PROMPT, rendered_xml, task_prompt,
        seeds=(seed,), task_id=task.task_id, engine="prism_v11_A_hydration_turn2",
    )
    turn2_call = results[0].call
    total_latency_s = time.monotonic() - t0

    score = score_tsr_response(task, turn2_call.content, candidate_symbols)
    ground_truth = _ground_truth_universe(task)
    return {
        "task_id": task.task_id,
        "approach": "A_hydration",
        "budget": budget,
        "seed": seed,
        "tsr": score,
        "cpi_strict": cpi_strict(candidate_symbols, task.adjudicated.pipeline_symbols),
        "fpr_gt": fpr(candidate_symbols, ground_truth),
        "prompt_tokens": turn1_call.prompt_tokens + turn2_call.prompt_tokens,
        "turn1_prompt_tokens": turn1_call.prompt_tokens,
        "turn2_prompt_tokens": turn2_call.prompt_tokens,
        "completion_tokens": turn1_call.completion_tokens + turn2_call.completion_tokens,
        "llm_latency_s": round(total_latency_s, 3),
        "spine_size": len(candidate_symbols),
        "requested_count": len(requested_symbols),
        "skipped_hallucinated": len(skipped),
        "turn1_parsed_ok": parsed_ok,
    }
