"""The retired 639-entry canonical sink catalog, kept as a read-only
compatibility view (Phase 2, Operation Groundwire).

The catalog used to be one flat, hand-curated table mixing two different
things: runtime/stdlib primitives and third-party entry points. It is now
split by architectural layer:

- `prism.semantics.primitives_l2.L2_PRIMITIVE_ENTRIES` - the Layer 2
  stdlib/runtime primitives Prism ships;
- `prism.semantics.boundary_stubs_l3.L3_BOUNDARY_STUBS` - temporary Layer 3
  stubs for third-party entry points, until Phase 3 indexes dependency
  source.

Both map every entry to atomic primitive bits through
`prism.semantics.primitives`. `CANONICAL_SINKS` is their union in the old
`{category: {language: entries}}` shape, so the Substance axis
(`prism.semantics.substance`) and every existing consumer see exactly the
same entries as before. Add new entries to the layer table they belong to,
never here.
"""
from __future__ import annotations

from prism.semantics.boundary_stubs_l3 import L3_BOUNDARY_STUBS
from prism.semantics.primitives_l2 import L2_PRIMITIVE_ENTRIES


def _union(*tables: dict[str, dict[str, set[str]]]) -> dict[str, dict[str, set[str]]]:
    merged: dict[str, dict[str, set[str]]] = {}
    for table in tables:
        for category, languages in table.items():
            for language, entries in languages.items():
                merged.setdefault(category, {}).setdefault(language, set()).update(entries)
    return merged


CANONICAL_SINKS: dict[str, dict[str, set[str]]] = _union(L2_PRIMITIVE_ENTRIES, L3_BOUNDARY_STUBS)
