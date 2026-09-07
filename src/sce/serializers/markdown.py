"""HLD section 7: renders a `PackResult` as the structured Markdown context
package handed to an LLM coding agent.
"""
from __future__ import annotations

from sce.slicer.knapsack import PackResult

_RESOLUTION_LABELS = {
    0: "Full Implementation - L0",
    1: "Control Skeleton - L1",
    2: "Contract - L2",
    3: "Alias - L3",
}

_FENCE_LANGUAGE = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "tsx": "tsx",
    "go": "go",
}


def render_markdown(result: PackResult, tag_matrix: dict[str, set[str]]) -> str:
    lines: list[str] = []
    lines.append("# SEMANTIC REPOSITORY CONTEXT")
    lines.append(f"Target Symbol: `{result.seed}`")
    lines.append(
        f"Context Budget: {result.budget} tokens | "
        f"Allocated: {round(result.allocated_tokens)} tokens | "
        f"Preserved Semantics: {result.preserved_semantics}%"
    )
    lines.append("")
    lines.append("## 1. Architectural Path")
    lines.extend(_render_architectural_path(result, tag_matrix))
    lines.append("")
    lines.append("## 2. Injected Code Units")
    lines.append("")
    for item in result.items:
        is_seed = item.symbol == result.seed
        label = _RESOLUTION_LABELS[item.resolution]
        heading = f"### [TARGET] {item.symbol} ({label})" if is_seed else f"### {item.symbol} ({label})"
        lines.append(heading)
        fence = _FENCE_LANGUAGE.get(item.language_id, "")
        lines.append(f"```{fence}")
        lines.append(item.content)
        lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


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
