"""HLD section 7: renders a `PackResult` as the structured Markdown context
package handed to an LLM coding agent.
"""
from __future__ import annotations

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
