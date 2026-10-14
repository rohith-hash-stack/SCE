"""Host-side boundary resolution against package summaries (offline export
summarization).

A host build that is given a `DependencyIndex` resolves its calls into
installed packages through their signature-only headers, then detaches
the headers again:

1. `attach_dependency_headers` finds the third-party packages the host
   imports, ensures their summaries, and materializes their headers in a
   temporary module root the builder indexes with the host files - so
   `requests.post` follows the facade to `requests.api.post`,
   `requests.Session()` types `session`, and so on;
2. `detach_dependency_headers`, after Pass 2, removes every header symbol,
   node and edge from the host's index. A host -> header edge keeps its
   target as an external *boundary* node carrying that entry point's
   summary (`boundary_bits`, `boundary_confident_bits`,
   `boundary_tentative_bits`, `boundary_package`, `boundary_tier`,
   `boundary_opaque`), which `prism.semantics.capabilities` counts at
   hop 1. A call into compiled native code - a fully native package, or an
   entry point whose summary reaches the package's own extension module -
   is an OPAQUE boundary: bits 0, capability unknown.

The host graph gains one boundary node per dependency entry point it
actually calls - never a dependency's internals.
"""
from __future__ import annotations

import os
import shutil
import tempfile

from prism.deps.discover import imported_roots
from prism.deps.headers import write_headers
from prism.deps.index import DependencyIndex
from prism.deps.summarizer import PackageSummary, SymbolSummary, constructor_methods, native_modules, native_reexports
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.symbol_table import GlobalSymbolTable, path_to_module

#: `boundary_tier` values on boundary nodes.
TIER_CONFIDENT, TIER_TENTATIVE, TIER_OPAQUE, TIER_NONE = "CONFIDENT", "TENTATIVE", "OPAQUE", "NONE"


def _host_local_roots(repo_root: str, files: list[str]) -> set[str]:
    roots = set()
    for f in files:
        module = path_to_module(f, repo_root)
        if module:
            roots.add(module.split(".")[0])
    return roots


def attach_dependency_headers(
    builder: ConcreteGraphBuilder, files: list[str], index: DependencyIndex
) -> tuple[list[str], dict[str, PackageSummary], str]:
    """Ensure summaries for the host's third-party imports and materialize
    their headers. Returns `(header files to index, summaries by package,
    header root)`; the caller removes the root after detaching."""
    python_files = [f for f in files if f.endswith(".py")]
    wanted = imported_roots(python_files) - _host_local_roots(builder.repo_root, files)
    summaries: dict[str, PackageSummary] = {}
    for name in sorted(wanted):
        summary = index.ensure(name)
        if summary is not None:
            summaries[name] = summary  # opaque packages too: their calls become OPAQUE boundary nodes
    header_root = tempfile.mkdtemp(prefix="prism-host-deps-")
    header_files: list[str] = []
    for name, summary in summaries.items():
        if summary.status == "summarized":
            header_files.extend(write_headers(summary.headers, name, header_root))
    builder.module_roots.append(header_root)
    return sorted(header_files), summaries, header_root


def detach_dependency_headers(builder: ConcreteGraphBuilder, summaries: dict[str, PackageSummary], header_root: str) -> int:
    """Strip header symbols out of the host index, turning host -> header
    edges into boundary nodes. Returns the number of boundary nodes."""
    prefix = header_root.rstrip(os.sep) + os.sep
    graph = builder.graph
    header_symbols = {s.qualified_name for s in builder.symbol_table if s.file.startswith(prefix)}
    header_nodes = {n for n, a in graph.nodes(data=True) if n in header_symbols or str(a.get("file", "")).startswith(prefix)}

    host_called = {v for u, v in graph.edges() if v in header_nodes and u not in header_nodes}
    # A fully native package has no header; the host's calls into it are
    # ordinary unresolved external targets named after the package.
    opaque_packages = {name for name, summary in summaries.items() if summary.status == "opaque"}
    # A summarized package's own compiled modules, and the names it
    # re-exports from them (`from ._core import connect`), are opaque too.
    native_names: set[str] = set()
    native_mods: set[str] = set()
    for name, summary in summaries.items():
        if summary.status == "summarized" and summary.stats.get("native_modules"):
            mods = native_modules(summary.ref.package_dir, summary.ref.package)
            native_mods |= mods
            native_names |= native_reexports(summary.headers, summary.ref.package, mods)

    def is_native(target: str) -> bool:
        return (
            target.split(".")[0] in opaque_packages
            or target in native_names
            or any(target == m or target.startswith(m + ".") for m in native_mods)
        )

    native_called = {
        v for u, v in graph.edges()
        if u not in header_nodes and v not in builder.symbol_table and is_native(v)
    }
    # `Cls(...)` runs the constructor: a class entry point carries its
    # `__init__`/`__new__` summaries (own or inherited, resolved while the
    # header hierarchy is still indexed).
    header_funcs = {q for q in header_symbols if builder.symbol_table.get(q).kind in ("function", "method")}
    constructors = {
        node: constructor_methods(builder, node, header_funcs)
        for node in host_called
        if (symbol := builder.symbol_table.get(node)) is not None and symbol.kind == "class"
    }
    for node in header_nodes:
        for _, target in list(graph.out_edges(node)):
            graph.remove_edge(node, target)
        if node not in host_called:
            graph.remove_node(node)
    by_package = {name: s for name, s in summaries.items()}
    for node in host_called:
        package = node.split(".")[0]
        summary = by_package.get(package)
        symbol = summary.symbols.get(node) if summary is not None else None
        if symbol is None and node in constructors and summary is not None:
            symbol = _merged([summary.symbols[c] for c in constructors[node] if c in summary.symbols])
        mask = symbol.mask if symbol is not None else 0
        confident = symbol.confident_mask if symbol is not None else 0
        attrs = graph.nodes[node]
        kind = attrs.get("kind")
        attrs.clear()
        opaque = bool(symbol is not None and symbol.opaque)
        attrs.update(
            external=True, boundary=True, kind=kind, boundary_package=package,
            boundary_version=summary.ref.version if summary is not None else None,
            boundary_bits=mask, boundary_confident_bits=confident, boundary_tentative_bits=mask & ~confident,
            boundary_opaque=opaque, boundary_tier=boundary_tier(mask, confident, opaque),
        )
    for node in native_called:
        summary = summaries[node.split(".")[0]]
        graph.nodes[node].update(
            external=True, boundary=True, boundary_package=summary.ref.package, boundary_version=summary.ref.version,
            boundary_bits=0, boundary_confident_bits=0, boundary_tentative_bits=0,
            boundary_opaque=True, boundary_tier=TIER_OPAQUE,
        )
    # Unresolved targets only header code pointed at are now isolated.
    for node in [n for n in graph.nodes if graph.degree(n) == 0 and n not in builder.symbol_table]:
        if graph.nodes[node].get("external") and node not in host_called:
            graph.remove_node(node)

    table = GlobalSymbolTable()
    for symbol in builder.symbol_table:
        if symbol.qualified_name in header_symbols:
            continue
        test_block = graph.nodes[symbol.qualified_name].get("test_block") if symbol.qualified_name in graph else None
        table.add(symbol, index_by_name=test_block not in ("test", "hook", "fixture"))
    builder.symbol_table = table
    for qname in header_symbols:
        builder._def_nodes.pop(qname, None)
    for path in [p for p in builder._parsed_files if p.startswith(prefix)]:
        builder._parsed_files.pop(path, None)
        builder._import_maps.pop(path, None)
    builder._methods_by_class = {c: m for c, m in builder._methods_by_class.items() if c not in header_symbols}
    builder.module_roots = [r for r in builder.module_roots if r != header_root]
    return len(host_called) + len(native_called)


def _merged(symbols: list[SymbolSummary]) -> SymbolSummary | None:
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


def boundary_tier(mask: int, confident_mask: int, opaque: bool) -> str:
    """A boundary node's evidence tier: OPAQUE when the entry point's only
    evidence is compiled native code (bits stay 0 - never invented),
    otherwise CONFIDENT/TENTATIVE by its summary bits, NONE without any."""
    if mask == 0:
        return TIER_OPAQUE if opaque else TIER_NONE
    return TIER_CONFIDENT if confident_mask == mask else TIER_TENTATIVE


def cleanup_header_root(header_root: str) -> None:
    shutil.rmtree(header_root, ignore_errors=True)
