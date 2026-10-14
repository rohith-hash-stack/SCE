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
   is an OPAQUE boundary: bits 0, capability unknown. A call through a
   module-level runtime proxy (`PackageSummary.proxies`) is a PROXY
   boundary, likewise bits 0.

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
from prism.deps.shadow import compute_stub_shadow, merge_summaries
from prism.deps.summarizer import PackageSummary, constructor_methods, native_modules, native_reexports
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.symbol_table import GlobalSymbolTable, path_to_module

#: `boundary_tier` values on boundary nodes.
TIER_CONFIDENT, TIER_TENTATIVE, TIER_OPAQUE, TIER_NONE = "CONFIDENT", "TENTATIVE", "OPAQUE", "NONE"
#: A call through a module-level runtime proxy (`django.db.connection`,
#: `flask.request`): what it reaches is decided at run time - unknown, like
#: OPAQUE, never invented.
TIER_PROXY = "PROXY"


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
    # Before call resolution: annotations naming compiled-only types bind.
    builder.opaque_types, builder.opaque_prefixes = native_index(summaries)
    return sorted(header_files), summaries, header_root


def native_index(summaries: dict[str, PackageSummary]) -> tuple[frozenset[str], tuple[str, ...]]:
    """`(native names, native prefixes)`: names a summarized package
    re-exports straight from its own compiled modules (`from ._core import
    connect`, `from psycopg2._psycopg import connection`), and the compiled
    modules plus fully native packages (no header at all), every name under
    which is native."""
    names: set[str] = set()
    prefixes = {name for name, summary in summaries.items() if summary.status == "opaque"}
    for summary in summaries.values():
        if summary.status == "summarized" and summary.stats.get("native_modules"):
            mods = native_modules(summary.ref.package_dir, summary.ref.package)
            prefixes |= mods
            names |= native_reexports(summary.headers, summary.ref.package, mods)
    return frozenset(names), tuple(sorted(prefixes))


def detach_dependency_headers(builder: ConcreteGraphBuilder, summaries: dict[str, PackageSummary], header_root: str) -> int:
    """Strip header symbols out of the host index, turning host -> header
    edges into boundary nodes. Returns the number of boundary nodes."""
    prefix = header_root.rstrip(os.sep) + os.sep
    graph = builder.graph
    header_symbols = {s.qualified_name for s in builder.symbol_table if s.file.startswith(prefix)}
    header_nodes = {n for n, a in graph.nodes(data=True) if n in header_symbols or str(a.get("file", "")).startswith(prefix)}

    host_called = {v for u, v in graph.edges() if v in header_nodes and u not in header_nodes}
    native_names, native_prefixes = native_index(summaries)

    def is_native(target: str) -> bool:
        # A native name itself, or a member of one (`conn.cursor()` on a
        # receiver annotated `psycopg2.extensions.connection`).
        return any(target == n or target.startswith(n + ".") for n in (*native_names, *native_prefixes))

    native_called = {
        v for u, v in graph.edges()
        if u not in header_nodes and v not in builder.symbol_table and is_native(v)
    }
    proxies = {name: (package, cls) for package, summary in summaries.items() for name, cls in summary.proxies.items()}

    def proxy_of(target: str) -> str | None:
        parts = target.split(".")
        for k in range(len(parts) - 1, 0, -1):  # longest proxy name a strict prefix of the call
            if ".".join(parts[:k]) in proxies:
                return ".".join(parts[:k])
        return None

    proxy_called = {
        v: name for u, v in graph.edges()
        if u not in header_nodes and v not in builder.symbol_table and v not in native_called
        and (name := proxy_of(v)) is not None
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
    # Summary-first precedence, entry by entry: Layer 3 stub entries landing
    # on actionable summary signal are shadowed (`prism.deps.shadow`).
    builder.stub_shadow = compute_stub_shadow(builder, summaries, header_funcs, native_names)
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
            symbol = merge_summaries([summary.symbols[c] for c in constructors[node] if c in summary.symbols])
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
    for node, name in proxy_called.items():
        package, cls = proxies[name]
        graph.nodes[node].update(
            external=True, boundary=True, boundary_package=package, boundary_version=summaries[package].ref.version,
            boundary_bits=0, boundary_confident_bits=0, boundary_tentative_bits=0,
            boundary_opaque=False, boundary_proxy=name, boundary_proxy_class=cls, boundary_tier=TIER_PROXY,
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
    return len(host_called) + len(native_called) + len(proxy_called)


def boundary_tier(mask: int, confident_mask: int, opaque: bool) -> str:
    """A boundary node's evidence tier: OPAQUE when the entry point's only
    evidence is compiled native code (bits stay 0 - never invented),
    otherwise CONFIDENT/TENTATIVE by its summary bits, NONE without any."""
    if mask == 0:
        return TIER_OPAQUE if opaque else TIER_NONE
    return TIER_CONFIDENT if confident_mask == mask else TIER_TENTATIVE


def cleanup_header_root(header_root: str) -> None:
    shutil.rmtree(header_root, ignore_errors=True)
