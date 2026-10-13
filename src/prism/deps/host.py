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
   `boundary_tentative_bits`, `boundary_package`), which
   `prism.semantics.capabilities` counts at hop 1.

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
from prism.deps.summarizer import PackageSummary
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.symbol_table import GlobalSymbolTable, path_to_module


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
        if summary is not None and summary.status == "summarized":
            summaries[name] = summary
    header_root = tempfile.mkdtemp(prefix="prism-host-deps-")
    header_files: list[str] = []
    for name, summary in summaries.items():
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
        mask = symbol.mask if symbol is not None else 0
        confident = symbol.confident_mask if symbol is not None else 0
        attrs = graph.nodes[node]
        kind = attrs.get("kind")
        attrs.clear()
        attrs.update(
            external=True, boundary=True, kind=kind, boundary_package=package,
            boundary_version=summary.ref.version if summary is not None else None,
            boundary_bits=mask, boundary_confident_bits=confident, boundary_tentative_bits=mask & ~confident,
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
    return len(host_called)


def cleanup_header_root(header_root: str) -> None:
    shutil.rmtree(header_root, ignore_errors=True)
