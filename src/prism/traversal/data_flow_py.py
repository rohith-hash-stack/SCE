"""v1.1 local data-flow extraction, Python front-end.

Real implementation lives in `prism.traversal._data_flow_common` (shared
across Python/Go/TS - see that module's own docstring for why) -
`extract_python_data_flow` is a thin, language-pinned wrapper around it,
matching the exact function-name/signature shape the spec asks for.
"""
from __future__ import annotations

from tree_sitter import Node

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.parser.tree_sitter_loader import ParsedFile
from prism.traversal._data_flow_common import compute_data_flow_edges, extract_data_flow

LANGUAGE_ID = "python"


def extract_python_data_flow(
    func_ast: Node, parsed: ParsedFile, qualified_name: str, builder: ConcreteGraphBuilder
) -> list[tuple[str, str, float]]:
    """`func_ast`: the function/method's own definition node (its body is
    walked internally). `resolved_call_sites` (per the spec's own
    signature) is derived internally from `builder`'s already-linked
    `G_C` rather than taken as a separate parameter - see
    `_data_flow_common._resolve_call_sites` for why reusing the real
    linked graph is more correct than a second, independent resolution
    pass. Returns `[(producer_symbol_id, consumer_symbol_id, confidence)]`.
    """
    return extract_data_flow(func_ast, parsed, qualified_name, builder)


def compute_python_data_flow_edges(builder: ConcreteGraphBuilder) -> dict[tuple[str, str], float]:
    return compute_data_flow_edges(builder, lang_filter=LANGUAGE_ID)
