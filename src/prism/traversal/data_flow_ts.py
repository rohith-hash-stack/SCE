"""v1.1 local data-flow extraction, TypeScript/JavaScript front-end.

Real implementation lives in `prism.traversal._data_flow_common` (shared
across Python/Go/TS). TS/JS's own structural requirement from the spec -
inspect `variable_declarator` (`const`/`let`/`var`), mapping an `init`-
call-expression to its bound variable name - is handled there via
`VARIABLE_DECLARATOR_NODE_TYPE`'s `name`/`value` field shape (confirmed
directly: `variable_declarator` uses `value`, not `init`, as its field
name in this codebase's tree-sitter-typescript grammar version).
"""
from __future__ import annotations

from tree_sitter import Node

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.parser.tree_sitter_loader import LanguageID, ParsedFile
from prism.traversal._data_flow_common import compute_data_flow_edges, extract_data_flow

LANGUAGE_ID = LanguageID.TYPESCRIPT


def extract_ts_data_flow(
    func_ast: Node, parsed: ParsedFile, qualified_name: str, builder: ConcreteGraphBuilder
) -> list[tuple[str, str, float]]:
    return extract_data_flow(func_ast, parsed, qualified_name, builder)


def compute_ts_data_flow_edges(builder: ConcreteGraphBuilder) -> dict[tuple[str, str], float]:
    """Both TypeScript and plain JavaScript/TSX share the identical
    `variable_declarator`/`call_expression` grammar shape this extraction
    depends on - restricting `lang_filter` to `LanguageID.TYPESCRIPT`
    alone would silently skip a `.js`/`.tsx` file's own data flow, so this
    covers all three `LanguageID`s a JS-family file can carry.
    """
    edges: dict[tuple[str, str], float] = {}
    for lang in (LanguageID.TYPESCRIPT, LanguageID.JAVASCRIPT, LanguageID.TSX):
        for key, confidence in compute_data_flow_edges(builder, lang_filter=lang).items():
            if confidence > edges.get(key, 0.0):
                edges[key] = confidence
    return edges
