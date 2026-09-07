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
    return {"nodes": nodes, "edges": edges}


def render_json_debug(builder: ConcreteGraphBuilder, tag_matrix: dict[str, set[str]]) -> str:
    return json.dumps(build_debug_dict(builder, tag_matrix), indent=2, sort_keys=False)
