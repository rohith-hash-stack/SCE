"""v1.1: the one entry point most callers need - composes all four axes
(`prism.semantics.substance`/`form`/`output`/`role`) into a single
`{qualified_name: uint64 mask}` map, mirroring `prism.graph.contracts.
compute_contracts`'s/`prism.tagger.engine.TaggingEngine.tag_graph`'s own
"one function does the whole pass" shape rather than making every caller
re-orchestrate axis ordering (Role needs Substance's output; the other
three are independent) itself.
"""
from __future__ import annotations

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.semantics.bitmask import compose_mask
from prism.semantics.form import compute_form_bits
from prism.semantics.output import compute_output_bits
from prism.semantics.role import compute_role_bits
from prism.semantics.substance import compute_substance_bits


def compute_feature_masks(builder: ConcreteGraphBuilder) -> dict[str, int]:
    """`Phi(v) = (S(v), F(v), O(v), R(v))`, composed into one plain `int`
    bitmask per function/method symbol `builder` indexed. Role is
    computed last since it depends on Substance's own output (a symbol's
    - and its callers'/callees' - sink domain); Substance/Form/Output are
    otherwise independent of each other and could run in any order.
    """
    substance = compute_substance_bits(builder)
    form = compute_form_bits(builder)
    output = compute_output_bits(builder)
    role = compute_role_bits(builder, substance)

    masks: dict[str, int] = {}
    for symbol in builder.symbol_table:
        if symbol.kind not in ("function", "method"):
            continue
        qname = symbol.qualified_name
        masks[qname] = compose_mask(
            substance.get(qname, 0),
            form.get(qname, 0),
            output.get(qname, 0),
            role.get(qname, 0),
        )
    return masks
