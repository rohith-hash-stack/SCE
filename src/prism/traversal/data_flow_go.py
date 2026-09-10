"""v1.1 local data-flow extraction, Go front-end.

Real implementation lives in `prism.traversal._data_flow_common` (shared
across Python/Go/TS). Go's own two structural requirements from the spec
are both handled there, not re-implemented here:
  - `short_var_declaration` (`:=`) with `left`/`right` *expression lists*
    (`_data_flow_common._bindings`'s `SHORT_VAR_DECL_NODE_TYPE` branch) -
    including the multi-assignment `val, err := u()` shape, positionally
    zipped when the right side itself has more than one expression.
  - The error-filter guard: `err`/`_`/`ctx`-named bindings are never
    entered into data-flow provenance at all
    (`_data_flow_common._NEVER_PROVENANCE_NAMES`), so a variable-pass edge
    can never phantom-couple a real payload producer to an error-logging
    consumer through a shared `err` binding.
"""
from __future__ import annotations

from tree_sitter import Node

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.parser.tree_sitter_loader import LanguageID, ParsedFile
from prism.traversal._data_flow_common import compute_data_flow_edges, extract_data_flow

LANGUAGE_ID = LanguageID.GO


def extract_go_data_flow(
    func_ast: Node, parsed: ParsedFile, qualified_name: str, builder: ConcreteGraphBuilder
) -> list[tuple[str, str, float]]:
    return extract_data_flow(func_ast, parsed, qualified_name, builder)


def compute_go_data_flow_edges(builder: ConcreteGraphBuilder) -> dict[tuple[str, str], float]:
    return compute_data_flow_edges(builder, lang_filter=LANGUAGE_ID)
