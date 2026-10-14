"""Offline export summarization of one pure-Python package (Operation
Groundwire, Phase 3b productionized).

`summarize_package(ref, dependencies)` indexes ONE package in isolation -
its own source plus only the signature-only headers of its already
summarized dependencies (`prism.deps.headers`) - and computes, for every
function and method, which atomic primitives (`FeatureBit.PRIM_*`) it can
reach at any depth:

1. own bits: calls matched against the Layer 2 primitive table only (the
   Layer 3 boundary stubs are never consulted - summaries replace them);
   a dependency header symbol carries its own summary's bits;
2. dispatch: a call reaching `Base.m` also reaches every override of `m`
   (class hierarchy), and a Protocol method links to the
   same method of every in-package class defining all the Protocol's
   non-dunder methods (structural typing the call graph cannot see) -
   both TENTATIVE;
3. Layer 2 callback contracts (`prism.semantics.l2_contracts`): a class
   extending a Layer 2 class gets nodes for the inherited methods its
   code calls on `self`, carrying the Layer 2 bits and linking to the
   overrides the stdlib calls back;
4. a fixpoint per primitive: best (tier, depth) from any function to a
   holder of that primitive, CONFIDENT only when no TENTATIVE edge is on
   the path, with one witness path kept;
5. the same fixpoint for `NATIVE`: a call into the package's own compiled
   extension module (`psycopg2._psycopg`) marks the caller - and, through
   the fixpoint, every function reaching it - as opaque. Native code never
   contributes capability bits; its capability is unknown, not zero.

The result is pure data (`PackageSummary`), cached by `prism.deps.store`.
"""
from __future__ import annotations

import ast
import heapq
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field

from prism.deps.discover import DistributionRef
from prism.deps.headers import package_headers, write_headers
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.symbol_table import GlobalSymbolTable
from prism.semantics.bitmask import PRIMITIVE_BITS
from prism.semantics.capabilities import compute_capability_profiles
from prism.semantics.l2_contracts import L2_CALLBACK_CONTRACTS
from prism.semantics.primitives import primitive_registry

#: Bump whenever the summarization algorithm changes what it computes -
#: the one way cached summaries are invalidated besides the package itself.
SUMMARY_SCHEMA_VERSION = 3
CONFIDENT, TENTATIVE = 0, 1
#: Evidence key (not a primitive bit) for "reaches compiled native code":
#: what such code does is unknown, so it never contributes capability bits.
NATIVE = -1
_BITS = tuple(int(b) for b in sorted(PRIMITIVE_BITS, key=int))
#: Compiled extension modules (CPython `.so`/`.pyd`, any ABI tag).
_NATIVE_SUFFIXES = (".so", ".pyd")
_PROPAGATING = ("CALLS", "INSTANTIATES")


@dataclass(frozen=True)
class BitEvidence:
    tier: int  # CONFIDENT | TENTATIVE
    depth: int  # call edges to the primitive
    path: tuple[str, ...]  # witness: this symbol ... the primitive's holder


@dataclass
class SymbolSummary:
    kind: str
    evidence: dict[int, BitEvidence] = field(default_factory=dict)  # primitive bit -> evidence

    @property
    def mask(self) -> int:
        out = 0
        for bit in self.evidence:
            if bit > 0:
                out |= bit
        return out

    @property
    def confident_mask(self) -> int:
        out = 0
        for bit, ev in self.evidence.items():
            if bit > 0 and ev.tier == CONFIDENT:
                out |= bit
        return out

    @property
    def opaque(self) -> bool:
        """Reaches compiled native code (its capability there is unknown)."""
        return NATIVE in self.evidence


@dataclass
class PackageSummary:
    ref: DistributionRef
    status: str  # "summarized" | "opaque" (no Python source to read)
    symbols: dict[str, SymbolSummary] = field(default_factory=dict)  # only symbols with any bit or NATIVE
    headers: dict[str, str] = field(default_factory=dict)
    stats: dict = field(default_factory=dict)

    def mask(self, qualified_name: str) -> int:
        s = self.symbols.get(qualified_name)
        return s.mask if s is not None else 0


def _python_files(root: str) -> list[str]:
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        out.extend(os.path.join(dirpath, f) for f in sorted(files) if f.endswith(".py"))
    return out


def native_modules(package_dir: str, package: str) -> set[str]:
    """Dotted names of the compiled extension modules inside a package
    whose source is not shipped (`psycopg2/_psycopg.cpython-311-x86_64-
    linux-gnu.so` -> `psycopg2._psycopg`). A compiled module next to a
    same-named `.py` (mypyc/Cython pure-Python mode, e.g.
    `charset_normalizer/md.py`) is compiled *from* that source, which is
    analysed as usual - not opaque."""
    out = set()
    for dirpath, dirs, files in os.walk(package_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            stem = name.split(".")[0]
            if name.endswith(_NATIVE_SUFFIXES) and f"{stem}.py" not in files:
                rel = os.path.relpath(os.path.join(dirpath, stem), package_dir)
                out.add(".".join([package, *rel.split(os.sep)]))
    return out


def _class_bases(builder: ConcreteGraphBuilder, cls: str) -> list[str]:
    node = builder.def_node(cls)
    sup = node.child_by_field_name("superclasses") if node is not None else None
    symbol = builder.symbol_table.get(cls)
    parsed = builder.parsed_file(symbol.file) if symbol is not None else None
    if sup is None or parsed is None:
        return []
    return [parsed.source[c.start_byte:c.end_byte].decode(errors="replace") for c in sup.named_children]


def native_reexports(headers: dict[str, str], package: str, natives: set[str]) -> set[str]:
    """Public names a package's modules import straight from its own
    compiled modules (`from ._core import connect` in `__init__.py` ->
    `nativedb.connect`): calling one runs native code."""
    names: set[str] = set()
    if not natives:
        return names
    for rel, text in headers.items():
        parts = [package, *rel[:-3].split(os.sep)]
        is_init = parts[-1] == "__init__"
        module = ".".join(parts[:-1] if is_init else parts)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for stmt in tree.body:
            if not isinstance(stmt, ast.ImportFrom):
                continue
            if stmt.level:
                base = module.split(".") if is_init else module.split(".")[:-1]
                base = base[: len(base) - (stmt.level - 1)] if stmt.level > 1 else base
                source = ".".join([*base, *([stmt.module] if stmt.module else [])])
            else:
                source = stmt.module or ""
            for alias in stmt.names:
                full = f"{source}.{alias.name}"
                if any(source == m or source.startswith(m + ".") or full == m for m in natives):
                    names.add(f"{module}.{alias.asname or alias.name}")
    return names


def constructor_methods(builder: ConcreteGraphBuilder, cls: str, funcs) -> list[str]:
    """`cls`'s `__init__` and `__new__` as run by `cls(...)`: each the
    nearest definition along the MRO (`object`'s own are not indexed)."""
    found = []
    for name in ("__init__", "__new__"):
        for owner in (cls, *builder._mro_ancestors(cls)):
            if f"{owner}.{name}" in funcs:
                found.append(f"{owner}.{name}")
                break
    return found


def _enclosing_function(builder: ConcreteGraphBuilder, qname: str, by_span: dict) -> str | None:
    """The innermost function or method whose body defines `qname`
    (`by_span`: `(file, start_byte, end_byte)` -> function qname)."""
    node = builder.def_node(qname)
    cursor = node.parent if node is not None else None
    while cursor is not None and cursor.type != "function_definition":
        cursor = cursor.parent
    if cursor is None:
        return None
    symbol = builder.symbol_table.get(qname)
    return by_span.get((symbol.file if symbol else None, cursor.start_byte, cursor.end_byte))


def summarize_package(ref: DistributionRef, dependencies: list[PackageSummary] = ()) -> PackageSummary:
    """Summarize `ref`'s package against its already summarized
    `dependencies` (their headers and bits only)."""
    started = time.time()
    sources = _python_files(ref.package_dir)
    natives = native_modules(ref.package_dir, ref.package)
    if not sources:
        return PackageSummary(ref, "opaque", stats={"reason": "no Python source", "native_modules": len(natives)})
    scratch = tempfile.mkdtemp(prefix="prism-summarize-")
    work, header_root = os.path.join(scratch, "src"), os.path.join(scratch, "headers")
    try:
        shutil.copytree(
            ref.package_dir, os.path.join(work, ref.package),
            ignore=lambda d, names: [n for n in names if n == "__pycache__" or (not n.endswith(".py") and not os.path.isdir(os.path.join(d, n)))],
        )
        os.makedirs(header_root)
        for dep in dependencies:
            if dep.status == "summarized" and dep.ref.package != ref.package:
                write_headers(dep.headers, dep.ref.package, header_root)
        files = _python_files(work) + _python_files(header_root)
        builder = ConcreteGraphBuilder(work, GlobalSymbolTable())
        builder.module_roots.append(header_root)  # headers: typed resolution only, never fallback candidates
        builder.dispatch_all_members = True  # a union/branch-typed receiver may be any of its classes
        builder.pass1_collect_definitions(files)
        builder.pass2_resolve_calls(files)
        summary = _summarize_built(builder, ref, dependencies, natives)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    summary.headers = package_headers(ref.package_dir)
    summary.stats["t_total_s"] = round(time.time() - started, 3)
    return summary


def _summarize_built(
    builder: ConcreteGraphBuilder, ref: DistributionRef, dependencies: list[PackageSummary], natives: set[str] = frozenset()
) -> PackageSummary:
    package = ref.package
    own_pkg = lambda q: q == package or q.startswith(package + ".")  # noqa: E731
    table = {s.qualified_name: s for s in builder.symbol_table}
    funcs = {q for q, s in table.items() if s.kind in ("function", "method")}
    classes = {q for q, s in table.items() if s.kind == "class" and own_pkg(q)}
    methods_of: dict[str, set[str]] = {}
    for q in funcs:
        if table[q].enclosing_class:
            methods_of.setdefault(table[q].enclosing_class, set()).add(q.rsplit(".", 1)[1])

    def resolve_method(cls: str, name: str) -> str | None:
        for owner in (cls, *builder._mro_ancestors(cls)):
            if f"{owner}.{name}" in funcs:
                return f"{owner}.{name}"
        return None

    # 1. seeds: own Layer 2 bits, dependency summary bits on header symbols
    seeds: dict[str, dict[int, BitEvidence]] = {}
    profiles = compute_capability_profiles(builder, max_hops=0, include_l3_stubs=False)
    for q in funcs:
        if own_pkg(q):
            own = profiles[q].own if q in profiles else 0
            if own:
                seeds[q] = {b: BitEvidence(CONFIDENT, 0, (q,)) for b in _BITS if own & b}
    for dep in dependencies:
        for q, sym in dep.symbols.items():
            if q in funcs:
                seeds[q] = {b: BitEvidence(ev.tier, ev.depth, (q, *ev.path[1:])) for b, ev in sym.evidence.items()}

    edges: list[tuple[str, str, int]] = []
    native_calls = 0
    for u, v, d in builder.graph.edges(data=True):
        if d.get("relation", "CALLS") in _PROPAGATING and u in funcs and own_pkg(u):
            edges.append((u, v, TENTATIVE if d.get("kind") == "TENTATIVE_CALL" else CONFIDENT))
            if any(v == m or v.startswith(m + ".") for m in natives):
                # A call into the package's own compiled module: the work
                # happens in native code. Seeded as NATIVE, never as bits.
                seeds.setdefault(u, {})[NATIVE] = BitEvidence(CONFIDENT, 1, (u, v))
                native_calls += 1

    # 1b. closures: a function may run every closure it defines - called
    #     directly, or handed on (`handler = _connect_and_send_request`,
    #     a middleware chain, a callback). TENTATIVE.
    closure_edges = 0
    by_span = {}
    for q in funcs:
        node = builder.def_node(q)
        if node is not None:
            by_span[(table[q].file, node.start_byte, node.end_byte)] = q
    for q in funcs:
        if own_pkg(q) and table[q].nested_in_function:
            outer = _enclosing_function(builder, q, by_span)
            if outer is not None and outer != q:
                edges.append((outer, q, TENTATIVE))
                closure_edges += 1

    # 1c. construction runs the constructor: `Cls(...)` (an INSTANTIATES
    #     edge to the class) reaches `__init__`/`__new__`, own or inherited.
    ctor_edges = 0
    for cls, symbol in table.items():
        if symbol.kind == "class":
            for ctor in constructor_methods(builder, cls, funcs):
                edges.append((cls, ctor, CONFIDENT))
                ctor_edges += 1

    # 2a. class-hierarchy dispatch: a call that reaches `Base.m` may run any
    #     override of `m` in a subclass (TENTATIVE) - the summary is a "may
    #     reach" over-approximation, and an abstract base's own body (a bare
    #     `raise NotImplementedError`) would otherwise hide every real one.
    overriders: dict[str, set[str]] = {}
    for u, v, d in builder.graph.edges(data=True):
        if d.get("relation") == "OVERRIDES":
            overriders.setdefault(v, set()).add(u)

    def all_overriders(method: str) -> set[str]:
        found, stack = set(), list(overriders.get(method, ()))
        while stack:
            m = stack.pop()
            if m not in found:
                found.add(m)
                stack.extend(overriders.get(m, ()))
        return found

    dispatch_edges = 0
    for u, v, _tier in list(edges):
        for override in all_overriders(v):
            if override != u:
                edges.append((u, override, TENTATIVE))
                dispatch_edges += 1

    # 2a-bis. mixin dispatch: `self.execute_command()` in a mixin that
    #     never declares it (only the classes mixing it in do) resolves to
    #     a name no function holds; every subclass defining it may run.
    subclasses: dict[str, set[str]] = {}
    for u, v, d in builder.graph.edges(data=True):
        if d.get("relation") == "EXTENDS":
            subclasses.setdefault(v, set()).add(u)

    def all_subclasses(cls: str) -> set[str]:
        found, stack = set(), list(subclasses.get(cls, ()))
        while stack:
            c = stack.pop()
            if c not in found:
                found.add(c)
                stack.extend(subclasses.get(c, ()))
        return found

    mixin_edges = 0
    for u, v, _tier in list(edges):
        if v in funcs or "." not in v:
            continue
        cls, name = v.rsplit(".", 1)
        if cls not in table or table[cls].kind != "class":
            continue
        for sub in sorted(all_subclasses(cls)):
            target = f"{sub}.{name}" if f"{sub}.{name}" in funcs else None
            if target and target != u:
                edges.append((u, target, TENTATIVE))
                mixin_edges += 1

    # 2b. typing.Protocol structural dispatch
    protocols = {c for c in classes if any(b.split(".")[-1].split("[")[0] == "Protocol" for b in _class_bases(builder, c))}

    def all_methods(cls: str) -> set[str]:
        names = set(methods_of.get(cls, ()))
        for ancestor in builder._mro_ancestors(cls):
            names |= methods_of.get(ancestor, set())
        return names

    protocol_edges = 0
    for proto in protocols:
        required = {m for m in all_methods(proto) if not m.startswith("__")}
        if not required:
            continue
        for cls in sorted(classes - protocols):
            if required <= all_methods(cls):
                for m in methods_of.get(proto, ()):
                    target = resolve_method(cls, m)
                    if target:
                        edges.append((f"{proto}.{m}", target, TENTATIVE))
                        protocol_edges += 1

    # 3. Layer 2 callback contracts
    registry = primitive_registry().get("python", {})
    callback_nodes: dict[str, str] = {}
    for cls in classes:
        import_map = builder._import_maps.get(table[cls].file)
        base = None
        for owner in (cls, *builder._mro_ancestors(cls)):
            if owner not in table:
                continue
            for text in _class_bases(builder, owner):
                head, *rest = text.split(".")
                resolved = import_map.resolve(head) if import_map is not None else None
                full = ".".join([resolved, *rest]) if resolved else text
                if full in L2_CALLBACK_CONTRACTS:
                    base = full
        if base is not None:
            for inherited in L2_CALLBACK_CONTRACTS[base]:
                if f"{cls}.{inherited}" not in funcs:  # inherited, not overridden
                    callback_nodes[f"{cls}.{inherited}"] = base
    callback_edges = 0
    for node, base in callback_nodes.items():
        cls, inherited = node.rsplit(".", 1)
        bits = registry.get(f"{base}.{inherited}", (0, ""))[0]
        if bits:
            seeds.setdefault(node, {}).update({b: BitEvidence(CONFIDENT, 0, (node,)) for b in _BITS if bits & b})
        for callee in L2_CALLBACK_CONTRACTS[base][inherited]:
            target = resolve_method(cls, callee) or (f"{cls}.{callee}" if f"{cls}.{callee}" in callback_nodes else None)
            if target:
                edges.append((node, target, CONFIDENT))
                callback_edges += 1

    # 4. fixpoint: per primitive, best (tier, depth) by reverse Dijkstra
    reverse: dict[str, list[tuple[str, int]]] = {}
    for u, v, tier in edges:
        reverse.setdefault(v, []).append((u, tier))
    evidence: dict[str, dict[int, BitEvidence]] = {}
    for bit in (*_BITS, NATIVE):
        heap = [(ev.tier, ev.depth, n, ev.path) for n, per in seeds.items() for b, ev in per.items() if b == bit]
        heapq.heapify(heap)
        settled: set[str] = set()
        while heap:
            tier, depth, node, path = heapq.heappop(heap)
            if node in settled:
                continue
            settled.add(node)
            if own_pkg(node) and node in funcs:
                evidence.setdefault(node, {})[bit] = BitEvidence(tier, depth, path)
            for caller, edge_tier in reverse.get(node, ()):
                if caller not in settled:
                    heapq.heappush(heap, (max(tier, edge_tier), depth + 1, caller, (caller, *path)))

    symbols = {q: SymbolSummary(table[q].kind, ev) for q, ev in sorted(evidence.items())}
    return PackageSummary(
        ref, "summarized", symbols,
        stats={
            "schema": SUMMARY_SCHEMA_VERSION,
            "functions": sum(1 for q in funcs if own_pkg(q)),
            "with_bits": len(symbols),
            "edges": len(edges),
            "dispatch_edges": dispatch_edges,
            "closure_edges": closure_edges,
            "ctor_edges": ctor_edges,
            "mixin_edges": mixin_edges,
            "protocol_edges": protocol_edges,
            "callback_edges": callback_edges,
            "native_modules": len(natives),
            "native_calls": native_calls,
            "opaque_symbols": sum(1 for s in symbols.values() if s.opaque),
        },
    )
