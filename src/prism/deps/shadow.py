"""Summary-first precedence over Layer 3 boundary stubs, entry by entry
(offline export summarization).

A Python Layer 3 stub entry (`redis.Redis.get`) is *shadowed* when, with
the dependency's headers indexed, static resolution lands it on a summarized
symbol whose summary carries actionable signal - at least one operational
I/O bit (`OPERATIONAL_IO_MASK`: network, filesystem, database, process; any
tier) or OPAQUE native evidence - or names a call through one of the
package's module-level runtime proxies (`django.db.connection.cursor`), a
PROXY boundary. Ambient bits alone (clocks, timers, randomness - a lazy
`redis.Redis()` reading a clock) never shadow an operational stub. A
shadowed entry is ignored by capability and Substance matching: the summary
takes full precedence. An entry static resolution cannot land on a
summarized symbol - a bound method of a module-level instance of an
ordinary class (`numpy.random.rand`), a name the package does not define -
or one whose summary is empty or ambient only (a lazy constructor such as
`httpx.Client`) stays as the fallback.
"""
from __future__ import annotations

from prism.deps.summarizer import PackageSummary, SymbolSummary, constructor_methods
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.symbol_table import resolve_export
from prism.semantics.bitmask import OPERATIONAL_IO_MASK
from prism.semantics.boundary_stubs_l3 import L3_BOUNDARY_STUBS

#: Shadow value for an entry landing on a fully native (opaque) package.
OPAQUE_PACKAGE = "<opaque-package>"


def python_stub_entries() -> frozenset[str]:
    return frozenset(e for langs in L3_BOUNDARY_STUBS.values() for e in langs.get("python", ()))


def resolve_stub_entry(builder: ConcreteGraphBuilder, entry: str) -> str | None:
    """The function, method or class `entry` names, followed through
    package re-exports and inherited methods, or `None`."""
    table = builder.symbol_table
    segments = entry.split(".")
    current = segments[0]
    for name in segments[1:]:
        symbol = table.get(current)
        if symbol is not None and symbol.kind == "class":
            nxt = builder._inherited_method(current, name)
            if nxt not in table:
                return None
        elif symbol is not None:
            return None  # an attribute of a function: nothing static to follow
        elif f"{current}.{name}" in table:
            nxt = f"{current}.{name}"
        else:
            nxt = resolve_export(current, name, builder.export_registry, table) or f"{current}.{name}"
        current = nxt
    symbol = table.get(current)
    return current if symbol is not None and symbol.kind in ("function", "method", "class") else None


def entry_summary(
    builder: ConcreteGraphBuilder, qname: str, summary: PackageSummary, funcs: set[str]
) -> SymbolSummary | None:
    """`qname`'s summary; a class answers with its constructors'."""
    symbol = builder.symbol_table.get(qname)
    if symbol is not None and symbol.kind == "class":
        parts = [summary.symbols[c] for c in constructor_methods(builder, qname, funcs) if c in summary.symbols]
        return merge_summaries(parts)
    return summary.symbols.get(qname)


def merge_summaries(symbols: list[SymbolSummary]) -> SymbolSummary | None:
    """One summary for several functions that all run (a constructor's
    `__new__` and `__init__`): per bit, the best evidence."""
    if not symbols:
        return None
    evidence = {}
    for symbol in symbols:
        for bit, ev in symbol.evidence.items():
            if bit not in evidence or (ev.tier, ev.depth) < (evidence[bit].tier, evidence[bit].depth):
                evidence[bit] = ev
    return SymbolSummary("class", evidence)


def shadows(symbol: SymbolSummary) -> bool:
    """Actionable summary signal: native code reached (OPAQUE evidence), or
    at least one operational I/O bit."""
    return symbol.opaque or bool(symbol.mask & OPERATIONAL_IO_MASK)


def compute_stub_shadow(
    builder: ConcreteGraphBuilder,
    summaries: dict[str, PackageSummary],
    header_funcs: set[str],
    native_names: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """`{stub entry: shadowing summarized symbol}` for every Python Layer 3
    entry of a package in `summaries` that resolves to actionable summary
    signal. Must run while the headers are still indexed."""
    shadow: dict[str, str] = {}
    for entry in sorted(python_stub_entries()):
        summary = summaries.get(entry.split(".")[0])
        if summary is None:
            continue
        if summary.status == "opaque":
            shadow[entry] = OPAQUE_PACKAGE
            continue
        if entry in native_names:
            shadow[entry] = entry  # re-exported straight from a compiled module
            continue
        proxy = next((p for p in summary.proxies if entry.startswith(p + ".")), None)
        if proxy is not None:
            shadow[entry] = proxy  # a runtime proxy: unknown, scored by the action leg
            continue
        target = resolve_stub_entry(builder, entry)
        if target is None:
            continue
        symbol = entry_summary(builder, target, summary, header_funcs)
        if symbol is not None and shadows(symbol):
            shadow[entry] = target
    return shadow
