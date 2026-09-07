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
`guarded_by_null_check`, ...) and "Incoming Callers".
"""
from __future__ import annotations

from prism.graph.contracts import BehavioralContract
from prism.graph.hierarchy import HierarchicalIntentProfile
from prism.slicer.knapsack import PackResult

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
# untouched either way.
_CONTRACT_RESOLUTIONS = frozenset({1, 2})


def render_markdown(
    result: PackResult,
    tag_matrix: dict[str, set[str]],
    contracts: dict[str, BehavioralContract] | None = None,
    graph=None,
    hierarchy: HierarchicalIntentProfile | None = None,
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
    for item in result.items:
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
                lines.extend(_render_seed_summary(item.symbol, contract))
                lines.append("```")

        if is_seed and graph is not None:
            dep_lines = _render_dependencies(item.symbol, graph, contracts)
            if dep_lines:
                lines.append("")
                lines.extend(dep_lines)
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
    return lines


def _render_seed_summary(symbol: str, contract: BehavioralContract) -> list[str]:
    """The target's own contract, shown alongside its full L0 source (not
    instead of it) - a compact "here's the shape" header a reader can
    check without parsing the code fence above it."""
    lines = [f"Complexity: {contract.cyclomatic_complexity}"]
    if contract.effects:
        lines.append(f"Effects: {_yaml_list(contract.effects)}")
    if contract.thrown_exceptions:
        lines.append(f"Throws: {_yaml_list(contract.thrown_exceptions)}")
    lines.append(f"Purity: {contract.purity}")
    return lines


_DEPENDENCY_RELATIONS = frozenset({"CALLS", "INSTANTIATES"})


def _render_dependencies(seed: str, graph, contracts: dict[str, BehavioralContract]) -> list[str]:
    if seed not in graph:
        return []
    lines: list[str] = []

    outgoing = [
        (v, data) for _u, v, data in graph.out_edges(seed, data=True) if data.get("relation", "CALLS") in _DEPENDENCY_RELATIONS
    ]
    if outgoing:
        lines.append("Outgoing Dependencies:")
        for target, data in sorted(outgoing, key=lambda t: t[0]):
            lines.append(f"  - target: {target}")
            if data.get("relation") == "INSTANTIATES":
                lines.append("    relation: instantiates")
            else:
                for key in ("call_kind", "inside_loop", "inside_try_catch", "guarded_by_null_check", "argument_flow"):
                    if key in data:
                        value = data[key]
                        rendered = str(value).lower() if isinstance(value, bool) else value
                        lines.append(f"    {key}: {rendered}")
            contract = contracts.get(target)
            if contract is not None:
                summary_bits = [f"purity: {_yaml_str(contract.purity)}"]
                if contract.return_type:
                    summary_bits.append(f"returns: {_yaml_str(contract.return_type)}")
                if contract.effects:
                    summary_bits.append(f"effects: {_yaml_list(contract.effects)}")
                lines.append(f"    contract: {{ {', '.join(summary_bits)} }}")
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

    return lines
