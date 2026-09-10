"""v1.1 Axis 4: Role `R(v)` - a symbol's topological neighborhood shape
(fan-in/fan-out over the real behavioral call graph, `builder.
calls_graph` - not the richer `builder.graph`, which also carries
EXTENDS/IMPLEMENTS/EMBEDS edges that say nothing about "how many places
call this") crossed with its own exported-ness and Substance domain.

Bits are independent, additive checks (a symbol can genuinely be both an
orchestrator *and* exported), not a single priority cascade the way Form/
Output classify a "primary" bit - the spec's own listing gives each Role
bit its own standalone condition.
"""
from __future__ import annotations

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.semantics.bitmask import FeatureBit

_NON_PURE_SUBSTANCE_MASK = int(
    FeatureBit.SINK_NETWORK_IO
    | FeatureBit.SINK_DATABASE_IO
    | FeatureBit.SINK_FILESYSTEM_IO
    | FeatureBit.SINK_PROCESS_IO
    | FeatureBit.SINK_TIME_IO
    | FeatureBit.SINK_RANDOMNESS
)


def _is_exported(builder: ConcreteGraphBuilder, symbol) -> bool:
    """Reuses `ContractExtractor._visibility`'s own per-language rules
    (Python leading-underscore convention, Go capitalization, JS/TS
    `export` wrapper, Java/C# access modifiers) rather than
    re-implementing them a second, potentially-drifting way."""
    from prism.graph.contracts import ContractExtractor

    def_node = builder.def_node(symbol.qualified_name)
    parsed = builder.parsed_file(symbol.file)
    if def_node is None or parsed is None:
        return False
    visibility = ContractExtractor()._visibility(def_node, parsed, symbol.enclosing_class, symbol.qualified_name)
    return visibility in ("public", "exported")


def compute_role_bits(builder: ConcreteGraphBuilder, substance_bits: dict[str, int]) -> dict[str, int]:
    """`{qualified_name: Role bits}` for every function/method `builder`
    indexed. Requires `substance_bits` (`prism.semantics.substance.
    compute_substance_bits`'s own output) since `ROLE_LEAF_UTILITY`/
    `ROLE_LEAF_SERVICE`/`ROLE_ADAPTER` all key off a symbol's (or its
    neighbors') Substance domain.
    """
    g = builder.calls_graph
    result: dict[str, int] = {}

    for symbol in builder.symbol_table:
        if symbol.kind not in ("function", "method"):
            continue
        qname = symbol.qualified_name
        if qname not in g:
            result[qname] = 0
            continue

        fan_in = g.in_degree(qname)
        fan_out = g.out_degree(qname)
        exported = _is_exported(builder, symbol)
        own_bits = substance_bits.get(qname, 0)
        is_pure = bool(own_bits & int(FeatureBit.SINK_PURE_COMPUTE))
        has_io_sink = bool(own_bits & _NON_PURE_SUBSTANCE_MASK)

        bits = FeatureBit(0)

        if exported and fan_in == 0:
            bits |= FeatureBit.ROLE_PUBLIC_API
            if fan_out > 0:
                bits |= FeatureBit.ROLE_ENTRYPOINT

        if fan_out >= 4 and fan_in <= 2:
            bits |= FeatureBit.ROLE_ORCHESTRATOR

        if fan_in >= 5 and fan_out <= 1 and is_pure:
            bits |= FeatureBit.ROLE_LEAF_UTILITY

        if fan_out <= 1 and has_io_sink:
            bits |= FeatureBit.ROLE_LEAF_SERVICE

        if fan_in >= 5 and fan_out >= 5:
            bits |= FeatureBit.ROLE_BRIDGE

        caller_sinks = 0
        for predecessor in g.predecessors(qname):
            caller_sinks |= substance_bits.get(predecessor, 0) & _NON_PURE_SUBSTANCE_MASK
        callee_sinks = 0
        for successor in g.successors(qname):
            callee_sinks |= substance_bits.get(successor, 0) & _NON_PURE_SUBSTANCE_MASK
        if caller_sinks and callee_sinks and not (caller_sinks & callee_sinks):
            bits |= FeatureBit.ROLE_ADAPTER

        result[qname] = int(bits)

    return result
