"""Phase C: cross-boundary external-dependency retrieval.

See `docs/phase_c_architecture_spec.md` (Section 2) for the design this
package implements - Step 1 is `prism.external.index` (locate, parse via
Prism's existing tree-sitter loader, and extract via `prism.graph.
contracts.ContractExtractor`, no bespoke parsing pipeline of its own).
"""
from __future__ import annotations
