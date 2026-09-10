"""Emits the dual-layer graph as JSON, for inspection/diagnostics
(`prism index --debug-json`, or programmatic consumption).
"""
from __future__ import annotations

import json

from prism.graph.concrete_builder import ConcreteGraphBuilder


def build_debug_dict(builder: ConcreteGraphBuilder, tag_matrix: dict[str, set[str]]) -> dict:
    nodes = []
    for qname in sorted(builder.symbol_table.all_qualified_names()):
        symbol = builder.symbol_table.get(qname)
        nodes.append(
            {
                "qualified_name": qname,
                "kind": symbol.kind,
                "file": symbol.file,
                "line_range": list(symbol.line_range),
                "language": symbol.language_id,
                "enclosing_class": symbol.enclosing_class,
                "tags": sorted(tag_matrix.get(qname, set())),
            }
        )
    edges = [
        {"from": u, "to": v, "relation": data.get("relation", "CALLS")}
        for u, v, data in sorted(builder.graph.edges(data=True), key=lambda e: (e[0], e[1]))
    ]
    # Item 3 (second post-implementation audit): repository index
    # metadata - always present (1.0 for a repo with no Go receiver-
    # shaped call sites at all, not an error/None), not gated behind a
    # Go-only check the way the plain-text `prism index` output is
    # (that one skips printing it entirely for a non-Go repo; this
    # debug/programmatic surface always includes it for a stable schema).
    return {
        "nodes": nodes,
        "edges": edges,
        "go_call_resolution_ratio": builder.go_call_resolution_ratio,
        # Item 4: files an error boundary caught and skipped
        # (INDEX_ERROR_SKIPPED) during indexing - empty for the common
        # case (no adversarial/pathological files encountered).
        "index_errors": builder.index_errors,
    }


def render_json_debug(builder: ConcreteGraphBuilder, tag_matrix: dict[str, set[str]]) -> str:
    return json.dumps(build_debug_dict(builder, tag_matrix), indent=2, sort_keys=False)
