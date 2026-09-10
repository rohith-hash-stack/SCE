"""HLD section 7: renders a `PackResult` as the structured Markdown context
package handed to an LLM coding agent.

`contracts`/`graph` (both optional, default `None` - every existing call
site keeps working unchanged) turn on token-efficient behavioral-contract
rendering (see `prism.graph.contracts`): the target symbol (L0) still gets
its full source, but every other packed item that has a computed
`BehavioralContract` renders as a compact YAML-shaped contract block
instead of a skeletonized/stubbed code fence - the whole point being that
an LLM coding agent rarely needs a callee's *implementation* to reason
about a call site, only its *interface and effects*. When `graph` is also
given, the target symbol's own block additionally lists its "Outgoing
Dependencies" (its own resolved call sites, each with the call-site
context `prism.graph.call_site` computed - `call_kind`, `inside_loop`,
`guarded_by_null_check`, ..., and, per the native-call-site-synonym work,
`bound_to`/`role`/`criticality`) and "Incoming Callers".

`runtime_overlay`/`runtime_bias`/`hybrid_flow_result` (all optional,
default `None`/the static-only default) turn on Section 2.4's runtime-
telemetry rendering when a `--trace-file` was ingested: the target's own
summary gains a "Profile:" block (runtime execution-hit count with its
per-environment breakdown, plus a hybrid-recalibrated downstream effect
distribution), and each outgoing dependency line gains its own
`runtime_hits` count and a dampening note when its call-site synonym
priority multiplier reduced its weight (the "noisy logging leaf" case
Section 2.1.4 exists to protect against).
"""
from __future__ import annotations

from dataclasses import dataclass

from prism.analysis.flow_engine import FlowEngineResult
from prism.analysis.hybrid_engine import synonym_priority_multiplier
from prism.graph.contracts import BehavioralContract
from prism.graph.hierarchy import HierarchicalIntentProfile
from prism.runtime.trace_ingester import AggregatedTrace
from prism.slicer.blueprint import StructuralBlueprint
from prism.slicer.compressor import (
    DYNAMIC_EDGE_SENTINEL_RESOLUTION,
    INFALLIBLE_SIGNATURE_RESOLUTION,
    UNRESOLVED_POLYMORPHIC_RESOLUTION,
)
from prism.slicer.knapsack import PackResult

_SENTINEL_RESOLUTIONS = frozenset({UNRESOLVED_POLYMORPHIC_RESOLUTION, DYNAMIC_EDGE_SENTINEL_RESOLUTION})

# Short form ("L0", not "Full Implementation - L0"): every heading now also
# carries the symbol's original line range and relative file path (see
# render_markdown below), so keeping the resolution label itself terse
# matters for real token cost, not just readability - this is the exact
# format asked for, e.g. "(L0 - lines 142-168 in
# django/contrib/sessions/base.py)".
_RESOLUTION_LABELS = {0: "L0", 1: "L1", 2: "L2", 3: "L3"}

_FENCE_LANGUAGE = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "tsx": "tsx",
    "go": "go",
    "java": "java",
    "csharp": "csharp",
}

# Resolutions that render as a compact contract block, when one is
# available, instead of a code fence - L0 (the seed, always full source)
# and L3 (already a one-line alias, nothing left to compact further) are
# untouched either way. L1 (Pruned - Issue #10) was removed from this set:
# it exists specifically to show a nearby dependency's *real* body with
# real call arguments, and letting the compact YAML contract block
# (interface/tags only, no argument values at all) supersede it would
# defeat that purpose for exactly the callees it matters most for. L2
# (Skeleton) keeps the override - its own compressor output already
# degrades to an arg-stripped body, so the richer contract block is
# strictly more useful when one is available.
_CONTRACT_RESOLUTIONS = frozenset({2})


def render_markdown(
    result: PackResult,
    tag_matrix: dict[str, set[str]],
    contracts: dict[str, BehavioralContract] | None = None,
    graph=None,
    hierarchy: HierarchicalIntentProfile | None = None,
    runtime_overlay: AggregatedTrace | None = None,
    hybrid_flow_result: FlowEngineResult | None = None,
    blueprint: StructuralBlueprint | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# SEMANTIC REPOSITORY CONTEXT")
    lines.append(f"Target Symbol: `{result.seed}`")
    lines.append(
        f"Context Budget: {result.budget} tokens | "
        f"Allocated: {round(result.allocated_tokens)} tokens | "
        f"Preserved Semantics: {result.preserved_semantics}%"
    )
    lines.append("")
    if hierarchy is not None:
        lines.extend(_render_hierarchical_sections(result, hierarchy, contracts or {}, graph))
        lines.append("")
    if not result.compact:
        # Adaptive Compact Scaffolding: the Architectural Path diagram is
        # exactly the kind of verbose structural overview that isn't worth
        # its token cost once the seed's own neighborhood is already small
        # enough to read directly - see ContextKnapsackPacker.pack.
        lines.append("## 1. Architectural Path")
        lines.extend(_render_architectural_path(result, tag_matrix))
        lines.append("")
    lines.append("## 2. Injected Code Units")
    lines.append("")
    contracts = contracts or {}
    # Fallibility-Based Knapsack Pruning (prism.slicer.knapsack): an
    # infallible-leaf item never gets its own "### symbol (Lx)" heading +
    # fence/YAML block at all - that per-item wrapper overhead is exactly
    # what this pruning exists to avoid paying for a node already proven
    # to have nothing that could go wrong. Every such item is grouped
    # into one compact list instead, rendered once after the normal items.
    normal_items = [
        item for item in result.items
        if item.resolution != INFALLIBLE_SIGNATURE_RESOLUTION and item.resolution not in _SENTINEL_RESOLUTIONS
    ]
    infallible_items = [item for item in result.items if item.resolution == INFALLIBLE_SIGNATURE_RESOLUTION]
    sentinel_items = [item for item in result.items if item.resolution in _SENTINEL_RESOLUTIONS]
    for item in normal_items:
        is_seed = item.symbol == result.seed
        if result.compact:
            # Single-line, no line-range/path suffix: on a small context
            # (the only time compact mode triggers) a reader can just look
            # at the whole package directly rather than needing a jump-back
            # reference into the real file.
            label = _RESOLUTION_LABELS[item.resolution]
        else:
            # Exact line grounding survives compression: even an L1-L3
            # item, whose *content* is skeletonized/stubbed and no longer a
            # literal source slice, still names precisely where the real
            # definition lives - a reader (or an LLM) can always jump to
            # the real file rather than treating the packed content as the
            # only truth.
            start, end = item.line_range
            label = f"{_RESOLUTION_LABELS[item.resolution]} - lines {start}-{end} in {item.relative_path}"
        heading = f"### [TARGET] {item.symbol} ({label})" if is_seed else f"### {item.symbol} ({label})"
        lines.append(heading)

        contract = contracts.get(item.symbol)
        if not is_seed and item.resolution in _CONTRACT_RESOLUTIONS and contract is not None:
            lines.append("```yaml")
            lines.extend(_render_contract_block(item.symbol, contract))
            lines.append("```")
        else:
            fence = _FENCE_LANGUAGE.get(item.language_id, "")
            lines.append(f"```{fence}")
            lines.append(item.content)
            lines.append("```")
            if is_seed and contract is not None:
                lines.append("")
                lines.append("```yaml")
                runtime_profile = _seed_runtime_profile(item.symbol, graph, runtime_overlay)
                lines.extend(_render_seed_summary(item.symbol, contract, runtime_profile, hybrid_flow_result))
                lines.append("```")

        if is_seed and graph is not None:
            dep_lines = _render_dependencies(item.symbol, graph, contracts, runtime_overlay)
            if dep_lines:
                lines.append("")
                lines.extend(dep_lines)
        if is_seed and blueprint is not None:
            lines.append("")
            lines.extend(_render_blueprint(blueprint))
        lines.append("")

    if infallible_items:
        lines.append("### Infallible Leaf Dependencies (pruned from full context)")
        lines.append("")
        for item in infallible_items:
            lines.append(item.content)
        lines.append("")

    if sentinel_items:
        lines.append("### Resolution Boundaries (ambiguous / dynamic dispatch)")
        lines.append("")
        for item in sentinel_items:
            lines.append(item.content)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------- #
# Hierarchical Structural Contracts (Layers 2-5, prism.graph.hierarchy)
# --------------------------------------------------------------------- #
def _yaml_scalar(value) -> str:
    if isinstance(value, str):
        return _yaml_str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return _yaml_list(value)
    return str(value)


def _yaml_inline_dict(d: dict) -> str:
    return "{ " + ", ".join(f"{k}: {_yaml_scalar(v)}" for k, v in d.items()) + " }"


def _render_hierarchical_sections(
    result: PackResult,
    hierarchy: HierarchicalIntentProfile,
    contracts: dict[str, BehavioralContract],
    graph,
) -> list[str]:
    """Renders the four Semantic Knowledge Engine sections the
    hierarchical-intent-profiling spec asks for, injected ahead of every
    raw source code fence (`## 2. Injected Code Units` below) - a
    structural summary an LLM coding agent can read before it ever sees a
    line of implementation."""
    lines: list[str] = ["## Hierarchical Structural Contracts", ""]

    repo = hierarchy.repository
    lines.append("### 1. REPOSITORY INTENT PROFILE")
    lines.append("```yaml")
    lines.append(f"Archetype: {_yaml_str(repo.archetype)}")
    lines.append(f"Global Sinks: {_yaml_inline_dict({k: round(v, 4) for k, v in repo.global_sink_mass.items()})}")
    lines.append(
        "Structural Invariants: "
        + _yaml_inline_dict({
            "fan_divergence": repo.fan_divergence,
            "max_critical_path_depth": repo.max_critical_path_depth,
            "subsystem_count": repo.subsystem_count,
        })
    )
    lines.append("```")
    lines.append("")

    seed_item = next((i for i in result.items if i.symbol == result.seed), None)
    subsystem = hierarchy.subsystem_for_relative_path(seed_item.relative_path) if seed_item is not None else None
    lines.append("### 2. SUBSYSTEM CONTRACT")
    lines.append("```yaml")
    if subsystem is not None:
        lines.append(f"Module: {_yaml_str(subsystem.module)}")
        lines.append(f"Instability: {subsystem.coupling_instability}")
        lines.append(f"Cohesion: {subsystem.cohesion_score}")
        lines.append(f"Dominant Sinks: {_yaml_list(subsystem.dominant_sinks)}")
        lines.append(f"Purity Ratio: {subsystem.purity_ratio}")
    else:
        lines.append("# no subsystem profile available for the target's module")
    lines.append("```")
    lines.append("")

    pipeline = hierarchy.flow_covering(result.seed)
    lines.append("### 3. ACTIVE EXECUTION PIPELINE")
    lines.append("```yaml")
    if pipeline is not None:
        lines.append(f"Entry Root: {_yaml_str(pipeline.entry_root)}")
        lines.append(f"Flow Topology: {_yaml_str(pipeline.flow_topology)}")
        lines.append(f"Critical Path Depth: {pipeline.critical_path_depth}")
        lines.append(f"Sync Mode: {_yaml_str(pipeline.sync_concurrency_mode)}")
        lines.append(f"Fallibility Index: {pipeline.fallibility_index}")
    else:
        lines.append("# target is not reachable from any known entry root within the traversal horizon")
    lines.append("```")
    lines.append("")

    lines.append("### 4. TARGET SYMBOL & SINK-AWARE CONTRACTS")
    lines.append("```yaml")
    lines.append(f"Target: {_yaml_str(result.seed)}")
    lines.append(f"Archetype: {_yaml_str(hierarchy.archetype_of(result.seed))}")
    egress = hierarchy.flow_result.egress(result.seed)
    ingress = hierarchy.flow_result.ingress(result.seed)
    lines.append(f"Egress: {_yaml_inline_dict({k: round(v, 4) for k, v in egress.as_dict().items() if v > 0})}")
    lines.append(f"Ingress: {_yaml_inline_dict({k: round(v, 4) for k, v in ingress.items()})}")
    if graph is not None and result.seed in graph:
        deps = [
            v for _u, v, data in graph.out_edges(result.seed, data=True)
            if data.get("relation", "CALLS") in _DEPENDENCY_RELATIONS
        ]
        if deps:
            lines.append("Dependencies:")
            for target in sorted(set(deps)):
                lines.append(f"  - target: {_yaml_str(target)}")
                lines.append(f"    archetype: {_yaml_str(hierarchy.archetype_of(target))}")
                contract = contracts.get(target)
                if contract is not None:
                    summary = {"purity": contract.purity}
                    if contract.effects:
                        summary["effects"] = contract.effects
                    lines.append(f"    contract: {_yaml_inline_dict(summary)}")
    lines.append("```")
    return lines


def _render_blueprint(blueprint: StructuralBlueprint) -> list[str]:
    """Canonical Structural Blueprint Injection (`prism.slicer.blueprint`) -
    the concrete syntactic scaffold (Guard/Delegate idioms) mined from the
    target's own directory, so a generated addition can match local
    convention rather than falling back to generic textbook shape."""
    lines = [f"Idiomatic Blueprint (Mined from {blueprint.sibling_count} sibling methods in package):", "Scaffold:"]
    for pattern in blueprint.patterns:
        lines.append(f"  - {pattern.label}: {pattern.text}")
    return lines


def _render_architectural_path(result: PackResult, tag_matrix: dict[str, set[str]]) -> list[str]:
    rendered: list[str] = []
    for depth, relation, node in result.architectural_path:
        tags = sorted(tag_matrix.get(node, set()))
        tag_prefix = f"[{', '.join(tags)}] " if tags else ""
        if depth == 0:
            rendered.append(f"{tag_prefix}{node}")
            continue
        indent = "  " * depth
        arrow = "requires" if relation == "requires" else "calls"
        rendered.append(f"{indent}└── {arrow} ──► {tag_prefix}{node}")
    return rendered


# --------------------------------------------------------------------- #
# Behavioral-contract rendering (prism.graph.contracts)
# --------------------------------------------------------------------- #
def _yaml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _yaml_list(values) -> str:
    return "[" + ", ".join(_yaml_str(v) for v in values) + "]"


def _render_hof_callbacks(contract: BehavioralContract) -> list[str]:
    """Higher-Order Function & Callback Signature Contracts - surfaces
    every callback-typed parameter's real signature (see
    `prism.graph.call_site.detect_hof_callback_params`), since a
    callback parameter produces no `CALLS` edge of its own and would
    otherwise be visible only as a bare type name."""
    if not contract.hof_callbacks:
        return []
    lines = ["Higher-Order Callbacks:"]
    for cb in contract.hof_callbacks:
        lines.append(f"  - param: {cb.param}")
        lines.append(f"    type: {cb.type}")
        lines.append(f"    invoked_dynamically: {str(cb.invoked_dynamically).lower()}")
        lines.append(f"    expected_signature: {_yaml_str(cb.expected_signature)}")
    return lines


def _render_contract_block(symbol: str, contract: BehavioralContract) -> list[str]:
    lines = [f"Symbol: {symbol.rsplit('.', 1)[-1]}"]
    if contract.params:
        lines.append(f"Params: {_yaml_list(p.render() for p in contract.params)}")
    if contract.return_type:
        lines.append(f"Returns: {_yaml_str(contract.return_type)}")
    lines.append(f"Purity: {contract.purity}")
    if contract.is_async:
        lines.append("Async: true")
    if contract.effects:
        lines.append(f"Effects: {_yaml_list(contract.effects)}")
    if contract.thrown_exceptions:
        lines.append(f"Throws: {_yaml_list(contract.thrown_exceptions)}")
    if contract.state_mutations:
        lines.append(f"Mutates: {_yaml_list(contract.state_mutations)}")
    lines.append(f"Complexity: {contract.cyclomatic_complexity}")
    if contract.visibility != "public":
        lines.append(f"Visibility: {contract.visibility}")
    if contract.is_deprecated:
        lines.append("Deprecated: true")
    if contract.doc_summary:
        lines.append(f"Summary: {_yaml_str(contract.doc_summary)}")
    lines.extend(_render_hof_callbacks(contract))
    return lines


def _render_seed_summary(
    symbol: str,
    contract: BehavioralContract,
    runtime_profile: "_SeedRuntimeProfile | None" = None,
    hybrid_flow_result: FlowEngineResult | None = None,
) -> list[str]:
    """The target's own contract, shown alongside its full L0 source (not
    instead of it) - a compact "here's the shape" header a reader can
    check without parsing the code fence above it."""
    lines = [f"Complexity: {contract.cyclomatic_complexity}"]
    if contract.effects:
        lines.append(f"Effects: {_yaml_list(contract.effects)}")
    if contract.thrown_exceptions:
        lines.append(f"Throws: {_yaml_list(contract.thrown_exceptions)}")
    lines.append(f"Purity: {contract.purity}")
    lines.extend(_render_hof_callbacks(contract))

    # Section 2.4 - runtime telemetry, only rendered once a `--trace-file`
    # was actually ingested.
    if runtime_profile is not None:
        lines.append("Profile:")
        lines.append(f"  Runtime Profile: {runtime_profile.provenance_summary}")
        if runtime_profile.total_hits:
            breakdown = ", ".join(f"{env}={count:,}" for env, count in sorted(runtime_profile.env_breakdown.items()))
            suffix = f" [{breakdown}]" if breakdown else ""
            lines.append(f"  Runtime Execution: {runtime_profile.total_hits:,} hits{suffix}")
        if hybrid_flow_result is not None:
            egress = hybrid_flow_result.egress(symbol).as_dict()
            nonzero = {k: round(v, 4) for k, v in egress.items() if v > 0}
            if nonzero:
                lines.append(f"  Downstream Flow: {_yaml_inline_dict(nonzero)}")
    return lines


@dataclass(frozen=True)
class _SeedRuntimeProfile:
    """A pre-computed summary of the seed's own incoming runtime hits -
    computed once in `render_markdown` (which has the graph handle) and
    handed to `_render_seed_summary`, rather than that function re-deriving
    it from `AggregatedTrace` + `graph` itself."""

    total_hits: int
    env_breakdown: dict[str, int]
    provenance_summary: str


def _seed_runtime_profile(seed: str, graph, runtime_overlay: AggregatedTrace | None) -> "_SeedRuntimeProfile | None":
    if runtime_overlay is None or not runtime_overlay.per_env_run_counts or graph is None or seed not in graph:
        return None
    total = 0
    env_breakdown: dict[str, int] = {}
    for caller in graph.predecessors(seed):
        total += runtime_overlay.total_hits(caller, seed)
        for env, count in runtime_overlay.breakdown(caller, seed).items():
            env_breakdown[env] = env_breakdown.get(env, 0) + count
    return _SeedRuntimeProfile(total_hits=total, env_breakdown=env_breakdown, provenance_summary=runtime_overlay.provenance_summary())


_DEPENDENCY_RELATIONS = frozenset({"CALLS", "INSTANTIATES"})

#: Metadata Serialization Compaction on High-Coupling Nodes: above this
#: outgoing-fan-out, the full multi-line YAML contract block per callee
#: (the dominant token cost on a dense node like `StringParenWrapper.
#: do_splitter_match`/`Line.append`/`convert_type` - confirmed against
#: the +5%-22% bloat this benchmark's own C3-05/C3-06/C4-04 measured)
#: switches to a compact one-line-per-callee representation.
COMPACT_FANOUT_THRESHOLD = 5
#: Even in compact mode, only this many callees are individually listed -
#: the remainder are summarized in one trailing line rather than paying
#: per-item token cost for a long tail of equally-uninteresting utilities.
COMPACT_MAX_RENDERED = 8


def _is_critical_dependency(data: dict) -> bool:
    """A dependency that stays in its fuller (though still compacted -
    two lines, not the full multi-line block) form even under fan-out
    compaction: either its call site is demonstrably load-bearing
    (`is_return_bound`), or it's a confirmed runtime error sink (Issue 4 -
    Active Trace Path Prioritization)."""
    return bool(data.get("is_return_bound")) or data.get("execution_status") == "ERROR_SINK"


def _dependency_priority(target: str, data: dict, runtime_overlay: AggregatedTrace | None, seed: str) -> tuple:
    """Sort key for which callees survive `COMPACT_MAX_RENDERED`'s cap -
    critical dependencies first, then by real runtime traffic (a stand-in
    "hybrid weight" proxy: `prism.analysis.hybrid_engine`'s real hybrid
    weight is itself seed-relative and needs a full trace-calibrated
    Markov pass to compute, which is disproportionate machinery to invoke
    just to order a dependency list - runtime hit count already captures
    its dominant term, `N_exec`, directly), then alphabetically for a
    stable, deterministic tie-break."""
    critical = 0 if _is_critical_dependency(data) else 1
    hits = runtime_overlay.total_hits(seed, target) if runtime_overlay is not None else 0
    return (critical, -hits, target)


def _render_compact_dependency_line(target: str, contract: BehavioralContract | None) -> str:
    if contract is None:
        return f"  - {target}"
    bits = [contract.purity]
    if contract.effects:
        bits.append(f"effects: [{', '.join(contract.effects)}]")
    elif contract.return_type:
        bits.append(f"returns: {contract.return_type}")
    return f"  - {target} [{', '.join(bits)}]"


def _render_critical_compact_lines(target: str, data: dict, contract: BehavioralContract | None) -> list[str]:
    bound_to = data.get("bound_to")
    role = data.get("call_site_role")
    role_bits = []
    if role:
        role_bits.append(f"role: {role}")
    if data.get("is_return_bound"):
        role_bits.append("return_bound")
    if data.get("execution_status") == "ERROR_SINK":
        role_bits.append("error_sink")
    header = f"  - {target}"
    if bound_to:
        header += f" -> bound_to: {bound_to}"
    if role_bits:
        header += f" [{', '.join(role_bits)}]"
    lines = [header]
    if contract is not None:
        bits = [f"returns: {_yaml_str(contract.return_type)}" if contract.return_type else None, f"purity: {contract.purity}"]
        if contract.thrown_exceptions:
            bits.append(f"throws: {_yaml_list(contract.thrown_exceptions)}")
        lines.append(f"    contract: {{ {', '.join(b for b in bits if b)} }}")
    return lines


def _render_full_dependency_lines(
    seed: str, target: str, data: dict, contract: BehavioralContract | None, runtime_overlay: AggregatedTrace | None,
) -> list[str]:
    """The full, multi-line-per-field dependency block - unchanged from
    before Metadata Serialization Compaction, used as-is whenever fan-out
    is at or below `COMPACT_FANOUT_THRESHOLD`."""
    lines = [f"  - target: {target}"]
    # Native call-site synonyms (prism.graph.call_site) - what this
    # *particular* call site does with the callee's result, not just which
    # symbol it calls. `role` intentionally mirrors `bound_to` when the
    # call is simply assigned (matching the spec's own worked example:
    # `role: credentials` for a call bound to `credentials`) rather than
    # repeating the same value under a second, redundant-looking key for
    # no reason - a `predicate_guard`/`assertion_subject` role means the
    # call wasn't assigned at all, so `bound_to` stays `None` there.
    bound_to = data.get("bound_to")
    role = data.get("call_site_role")
    if bound_to:
        lines.append(f"    bound_to: {bound_to}")
    if role:
        lines.append(f"    role: {role}")
    if data.get("is_return_bound"):
        lines.append("    criticality: return_bound")
    if runtime_overlay is not None:
        hits = runtime_overlay.total_hits(seed, target)
        if hits:
            errors = runtime_overlay.total_errors(seed, target)
            lines.append(f"    runtime_hits: {hits:,} calls ({errors} errors)")
            beta = synonym_priority_multiplier(data, callee_simple_name=target.rsplit(".", 1)[-1])
            if beta < 1.0:
                lines.append("    runtime_note: dampened by log-weighting (priority-collision protection)")
    if data.get("relation") == "INSTANTIATES":
        lines.append("    relation: instantiates")
    else:
        for key in ("call_kind", "inside_loop", "inside_try_catch", "guarded_by_null_check", "argument_flow"):
            if key in data:
                value = data[key]
                rendered = str(value).lower() if isinstance(value, bool) else value
                lines.append(f"    {key}: {rendered}")
    if contract is not None:
        summary_bits = [f"purity: {_yaml_str(contract.purity)}"]
        if contract.return_type:
            summary_bits.append(f"returns: {_yaml_str(contract.return_type)}")
        if contract.effects:
            summary_bits.append(f"effects: {_yaml_list(contract.effects)}")
        lines.append(f"    contract: {{ {', '.join(summary_bits)} }}")
    return lines


def _render_dependencies(
    seed: str,
    graph,
    contracts: dict[str, BehavioralContract],
    runtime_overlay: AggregatedTrace | None = None,
) -> list[str]:
    if seed not in graph:
        return []
    lines: list[str] = []

    outgoing = [
        (v, data) for _u, v, data in graph.out_edges(seed, data=True) if data.get("relation", "CALLS") in _DEPENDENCY_RELATIONS
    ]
    if outgoing:
        k_out = len(outgoing)
        if k_out <= COMPACT_FANOUT_THRESHOLD:
            lines.append("Outgoing Dependencies:")
            for target, data in sorted(outgoing, key=lambda t: t[0]):
                lines.extend(_render_full_dependency_lines(seed, target, data, contracts.get(target), runtime_overlay))
        else:
            # Metadata Serialization Compaction on High-Coupling Nodes.
            lines.append(f"Outgoing Dependencies (Compact View - {k_out} total):")
            ordered = sorted(outgoing, key=lambda t: _dependency_priority(t[0], t[1], runtime_overlay, seed))
            rendered, remainder = ordered[:COMPACT_MAX_RENDERED], ordered[COMPACT_MAX_RENDERED:]
            for target, data in rendered:
                contract = contracts.get(target)
                if _is_critical_dependency(data):
                    lines.extend(_render_critical_compact_lines(target, data, contract))
                else:
                    lines.append(_render_compact_dependency_line(target, contract))
            if remainder:
                pure_leaf_count = sum(
                    1 for target, _data in remainder
                    if (c := contracts.get(target)) is not None and c.purity == "pure"
                )
                other_count = len(remainder) - pure_leaf_count
                summary = f"... and {pure_leaf_count} other pure leaf utilities" if pure_leaf_count else ""
                if other_count:
                    summary += (", " if summary else "... and ") + f"{other_count} other dependencies"
                lines.append(f"  {summary}.")
        lines.append("")

    incoming = [
        (u, data) for u, _v, data in graph.in_edges(seed, data=True) if data.get("relation", "CALLS") in _DEPENDENCY_RELATIONS
    ]
    if incoming:
        lines.append("Incoming Callers:")
        for caller, data in sorted(incoming, key=lambda t: t[0]):
            call_kind = data.get("call_kind")
            suffix = f" ({call_kind})" if call_kind else ""
            lines.append(f"  - {caller}{suffix}")

    trace_lines = _render_trace_branches(seed, graph, runtime_overlay)
    if trace_lines:
        lines.append("")
        lines.extend(trace_lines)

    return lines


def _render_trace_branches(seed: str, graph, runtime_overlay: AggregatedTrace | None) -> list[str]:
    """Active Trace Path Prioritization (Issue 4): once a `--trace-file`
    was ingested, the seed's own outgoing edges are split by
    `execution_status` (set by `ConcreteGraphBuilder.apply_runtime_overlay`)
    into what a diagnosis should look at first (ACTIVE/ERROR_SINK - real,
    observed traffic) versus what's merely statically reachable but never
    actually exercised in this trace (UNOBSERVED) - a dormant, environment-
    gated fallback branch is exactly the kind of thing a model without
    this distinction hallucinates as the root cause of a failure it never
    actually ran (confirmed during this benchmark's own C2-07 run against
    `Editor.edit_files`'s Windows fallback path). Renders nothing at all
    when no trace was ingested (every edge's `execution_status` is simply
    absent), so this is a pure addition with no effect on the untraced
    default path.
    """
    if runtime_overlay is None or not runtime_overlay.per_env_run_counts:
        return []
    outgoing = [
        (v, data) for _u, v, data in graph.out_edges(seed, data=True)
        if data.get("relation", "CALLS") in _DEPENDENCY_RELATIONS and "execution_status" in data
    ]
    if not outgoing:
        return []

    envs = ",".join(sorted(runtime_overlay.per_env_run_counts.keys()))
    lines = [f"Outgoing Branches (Trace: {envs}):"]

    active = sorted((v, d) for v, d in outgoing if d["execution_status"] in ("ACTIVE", "ERROR_SINK"))
    unobserved = sorted((v, d) for v, d in outgoing if d["execution_status"] == "UNOBSERVED")

    if active:
        lines.append("[ACTIVE PATHS]")
        for target, data in active:
            hits = runtime_overlay.total_hits(seed, target)
            if data["execution_status"] == "ERROR_SINK":
                errors = runtime_overlay.total_errors(seed, target)
                lines.append(f"- callee: {target} (hits: {hits}, status: ERROR [errors: {errors}])")
            else:
                lines.append(f"- callee: {target} (hits: {hits}, status: OK)")
    if unobserved:
        lines.append("[UNOBSERVED PATHS]")
        for target, _data in unobserved:
            lines.append(f"- callee: {target} (hits: 0, unobserved in current trace)")

    if active:
        lines.append("")
        lines.append(
            "Diagnosis Guidance: Focus root-cause diagnosis on ACTIVE execution paths with "
            "non-zero hits before inspecting unobserved fallbacks."
        )
    return lines
