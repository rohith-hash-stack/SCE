"""Global symbol table, module-path resolution, and per-file scope maps.

This module implements the data structures used by the two-pass linker
described in HLD section 4.1:

  - `GlobalSymbolTable`: fully-qualified-name -> definition metadata,
    populated by Pass 1.
  - `LocalImportMap`: local token -> fully qualified symbol, built per file
    from `import` / `from ... import` statements.
  - `InstanceTypeMap`: local variable -> fully qualified class name, built
    per function from constructor-assignment statements (`v = Verifier()`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def path_to_module(file_path: str, repo_root: str) -> str:
    """Convert a repo-relative or absolute file path into a dotted module name.

    ``src/auth/jwt.py`` (relative to `repo_root`) -> ``src.auth.jwt``.
    ``src/auth/__init__.py`` -> ``src.auth``.
    """
    rel = os.path.relpath(file_path, repo_root)
    rel_no_ext, _ext = os.path.splitext(rel)
    parts = [p for p in rel_no_ext.split(os.sep) if p not in ("", ".")]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


@dataclass
class SymbolInfo:
    qualified_name: str
    kind: str  # "class" | "function" | "method" | "attribute"
    file: str
    line_range: tuple[int, int]  # 1-indexed, inclusive [start, end]
    language_id: str
    module: str
    enclosing_class: str | None = None


class GlobalSymbolTable:
    """Fully-qualified-name -> `SymbolInfo`, populated during Pass 1."""

    def __init__(self) -> None:
        self._symbols: dict[str, SymbolInfo] = {}
        # module -> {simple_name: qualified_name}, for intra-file Rule D lookups.
        self._module_index: dict[str, dict[str, str]] = {}
        # simple_name -> [qualified_name, ...], across the *whole* repo, for
        # Polysemy Disambiguation (see `score_candidate` below) - deliberately
        # separate from `_module_index` (which is scoped to one module and
        # only ever returns a single winner) since ambiguity detection needs
        # every same-named candidate across every module at once. Only
        # function/method symbols are indexed here: a class or a bare
        # attribute is never itself the target of an ambiguous *call*.
        self._simple_name_index: dict[str, list[str]] = {}

    def add(self, symbol: SymbolInfo) -> None:
        self._symbols[symbol.qualified_name] = symbol
        simple_name = symbol.qualified_name.rsplit(".", 1)[-1]
        self._module_index.setdefault(symbol.module, {})[simple_name] = symbol.qualified_name
        # Also index by the class-qualified name (Class.method) so `self.method()`
        # resolution can look up `EnclosingClass.method` without the module prefix.
        if symbol.enclosing_class is not None:
            class_simple = symbol.enclosing_class.rsplit(".", 1)[-1]
            local_name = f"{class_simple}.{simple_name}"
            self._module_index.setdefault(symbol.module, {})[local_name] = symbol.qualified_name
        if symbol.kind in ("function", "method"):
            self._simple_name_index.setdefault(simple_name, []).append(symbol.qualified_name)

    def get(self, qualified_name: str) -> SymbolInfo | None:
        return self._symbols.get(qualified_name)

    def __contains__(self, qualified_name: str) -> bool:
        return qualified_name in self._symbols

    def __iter__(self):
        return iter(self._symbols.values())

    def __len__(self) -> int:
        return len(self._symbols)

    def resolve_in_module(self, module: str, simple_name: str) -> str | None:
        """Rule D fallback: look up a bare name defined in the same module."""
        return self._module_index.get(module, {}).get(simple_name)

    def all_qualified_names(self) -> list[str]:
        return list(self._symbols.keys())

    def candidates_for_simple_name(self, simple_name: str) -> list[SymbolInfo]:
        """Every function/method in the repo whose bare trailing name is
        `simple_name` - the candidate set a Polysemy Disambiguation decision
        scores over. Two or more entries is what makes a call to that bare
        name genuinely ambiguous (see `score_candidate`); zero or one is not
        this module's concern (an unresolved call with zero repo-local
        candidates is an ordinary external/builtin reference, and exactly
        one is a plain resolution gap, not polysemy).
        """
        return [self._symbols[q] for q in self._simple_name_index.get(simple_name, [])]


# --------------------------------------------------------------------- #
# Polysemy Disambiguation via Context Scoring.
#
# When a call site's bare identifier (`validate()`, `obj.Close()`) can't be
# bound by the normal import-map/instance-map/same-module rules
# (`ConcreteGraphBuilder._resolve_segments`), and more than one function or
# method in the repo shares that same simple name, guessing which one the
# call actually meant - or silently dropping the edge, the prior behavior -
# both destroy information. This scores every same-named candidate
# deterministically and either binds the clear winner or emits an explicit
# `UnresolvedPolymorphicNode` sentinel so the ambiguity itself becomes part
# of the graph, not a silent gap in it.
# --------------------------------------------------------------------- #
POLYSEMY_THRESHOLD = 0.85
NAMESPACE_MATCH_WEIGHT = 0.50
ARITY_TYPE_MATCH_WEIGHT = 0.35
LOCALITY_DISTANCE_WEIGHT = 0.15

LOCALITY_SAME_FILE = 1.0
LOCALITY_SAME_PACKAGE = 0.6
LOCALITY_EXTERNAL = 0.2


def namespace_match(candidate: SymbolInfo, caller_module: str, import_map: LocalImportMap) -> float:
    """1.0 if the caller's own file imports `candidate`'s module (or a
    parent package of it) or shares that module outright; 0.0 otherwise."""
    if candidate.module == caller_module:
        return 1.0
    imported_modules = set(import_map.aliases.values())
    for target in imported_modules:
        if target == candidate.module or target.startswith(f"{candidate.module}.") or candidate.module.startswith(f"{target}."):
            return 1.0
    if candidate.module in import_map.wildcard_targets:
        return 1.0
    return 0.0


def locality_distance(candidate: SymbolInfo, caller_file: str, caller_module: str) -> float:
    """1.0 same file, 0.6 same package/directory, 0.2 external package."""
    if candidate.file == caller_file:
        return LOCALITY_SAME_FILE
    candidate_pkg = candidate.module.rsplit(".", 1)[0] if "." in candidate.module else candidate.module
    caller_pkg = caller_module.rsplit(".", 1)[0] if "." in caller_module else caller_module
    if candidate_pkg == caller_pkg:
        return LOCALITY_SAME_PACKAGE
    return LOCALITY_EXTERNAL


def arity_match(candidate_param_count: int | None, call_args_count: int) -> float:
    """1.0 if the call site's argument count matches the candidate's own
    parameter count; 0.0 on a mismatch or when the candidate's own count
    isn't known (no def_node to read it from) - type alignment beyond bare
    arity isn't attempted, since this codebase performs no type inference."""
    if candidate_param_count is None:
        return 0.0
    return 1.0 if candidate_param_count == call_args_count else 0.0


def score_candidate(namespace: float, arity_type: float, locality: float) -> float:
    """Score(C) = 0.50*NamespaceMatch + 0.35*ArityAndTypeMatch + 0.15*LocalityDistance."""
    return (
        NAMESPACE_MATCH_WEIGHT * namespace
        + ARITY_TYPE_MATCH_WEIGHT * arity_type
        + LOCALITY_DISTANCE_WEIGHT * locality
    )


@dataclass(frozen=True)
class UnresolvedPolymorphicNode:
    identifier: str
    candidates: list[str]
    call_site_file: str
    call_site_line: int

    def to_dict(self) -> dict:
        return {
            "identifier": self.identifier,
            "candidates": list(self.candidates),
            "call_site_file": self.call_site_file,
            "call_site_line": self.call_site_line,
        }


def unresolved_polymorphic_node_id(identifier: str, call_site_file: str, call_site_line: int) -> str:
    """A stable, unique graph-node id for one ambiguous call site - distinct
    from every real qualified name (which never contains `<`/`>`), and
    distinct per call site (not per identifier) so two different ambiguous
    calls to the same bare name don't collapse into one sentinel node."""
    return f"<ambiguous:{identifier}@{call_site_file}:{call_site_line}>"


@dataclass
class LocalImportMap:
    """local token -> fully-qualified symbol/module, for one source file."""

    aliases: dict[str, str] = field(default_factory=dict)
    # Package/namespace prefixes brought fully into scope without binding
    # any one simple name - Java's `import com.example.models.*;` and
    # every C# `using App.Models;` (C# has no separate wildcard import
    # syntax; a plain `using` already imports the whole namespace's
    # members, not just one class). Tried as a same-package/namespace
    # lookup fallback, after an exact `aliases` match and the caller's own
    # module/package, and in the order declared - see
    # `ConcreteGraphBuilder._resolve_reference_chain`.
    wildcard_targets: list[str] = field(default_factory=list)

    def add(self, local_name: str, qualified_target: str) -> None:
        self.aliases[local_name] = qualified_target

    def add_wildcard(self, package_or_namespace: str) -> None:
        if package_or_namespace not in self.wildcard_targets:
            self.wildcard_targets.append(package_or_namespace)

    def resolve(self, local_name: str) -> str | None:
        return self.aliases.get(local_name)


@dataclass
class InstanceTypeMap:
    """local variable -> fully-qualified class name, scoped to one function."""

    bindings: dict[str, str] = field(default_factory=dict)

    def bind(self, var_name: str, qualified_class: str) -> None:
        self.bindings[var_name] = qualified_class

    def resolve(self, var_name: str) -> str | None:
        return self.bindings.get(var_name)


# --------------------------------------------------------------------- #
# Barrel-file / re-export resolution (Issues #6/#7).
#
# `LocalImportMap` (above) is deliberately a purely textual, per-file
# mapping: `from app import OrderService` records `"OrderService" ->
# "app.OrderService"` from the import statement's own text alone, with no
# idea whether `app.OrderService` is really where `OrderService` is
# *defined* versus merely where it happens to be *re-exported from* - a
# barrel/index module (`app/__init__.py` doing `from .order_service import
# OrderService`, a TS `index.ts` doing `export { OrderService } from
# './order_service'`) makes exactly that distinction matter: the real
# definition lives at `app.order_service.OrderService`, and a consumer
# that only ever imports through the barrel (`from app import
# OrderService`) would otherwise resolve to a phantom `app.OrderService`
# node that Pass 1 never actually registered. `ExportRegistry` tracks
# every file's own "what did I bring into scope, and from where" — the
# same information `LocalImportMap` already carries per-file — but
# persistently, keyed by the *exporting* module rather than discarded once
# that one file's Pass 2 pass finishes, so `resolve_export` can walk the
# chain across as many intermediate barrel files as necessary.
# --------------------------------------------------------------------- #
#: Recursive `resolve_export` hop limit - matches the audit spec's own
#: bound exactly (Issue #6.2: "`if depth > 5`"). A real barrel-file chain
#: nesting five re-export hops deep is already pathological; this exists
#: to guarantee termination on a circular import chain (`a` re-exports
#: from `b`, `b` re-exports from `a`) without needing cycle detection to
#: be the *only* thing standing between this and infinite recursion.
EXPORT_RESOLUTION_MAX_DEPTH = 5


class ExportRegistry:
    """Persistent, repo-wide "module -> what it re-exports, and from
    where" registry, built once per file (see
    `ConcreteGraphBuilder._register_exports`) from the same import-parsing
    machinery that already populates each file's own `LocalImportMap`.
    """

    def __init__(self) -> None:
        # (module, exported_simple_name) -> (origin_module, origin_name)
        self._explicit: dict[tuple[str, str], tuple[str, str]] = {}
        # module -> [origin_module, ...], in declared order - Python
        # `from .x import *` / TS `export * from './x'` / a Java
        # `import pkg.*`-shaped wildcard, all folded into the same shape.
        self._wildcards: dict[str, list[str]] = {}
        # module -> explicit __all__ whitelist, when statically known
        # (an ast.List/Tuple of string literals) - None means "no
        # explicit __all__ was ever declared for this module" (distinct
        # from an empty whitelist, `frozenset()`, meaning "__all__ = []").
        self._all_whitelist: dict[str, frozenset[str] | None] = {}
        # module -> True iff this module's own __all__ is dynamically
        # modified (`__all__.extend(...)`, `__all__ += [...]`, a list
        # comprehension, ...) rather than a static literal - Issue #6.3's
        # `PARTIAL_EXPORT_MAP` flag: `resolve_export` falls back to
        # "any public (non-underscore) name" for such a module instead of
        # strictly honoring a whitelist it can't fully determine statically.
        self._partial_export_modules: set[str] = set()

    def add_explicit(self, module: str, exported_name: str, origin_module: str, origin_name: str) -> None:
        self._explicit[(module, exported_name)] = (origin_module, origin_name)

    def add_wildcard(self, module: str, origin_module: str) -> None:
        targets = self._wildcards.setdefault(module, [])
        if origin_module not in targets:
            targets.append(origin_module)

    def set_all_whitelist(self, module: str, names: frozenset[str]) -> None:
        self._all_whitelist[module] = names

    def mark_partial_export(self, module: str) -> None:
        self._partial_export_modules.add(module)

    def is_partial_export(self, module: str) -> bool:
        return module in self._partial_export_modules

    def all_whitelist(self, module: str) -> frozenset[str] | None:
        return self._all_whitelist.get(module)

    def wildcard_targets(self, module: str) -> list[str]:
        return list(self._wildcards.get(module, []))

    def explicit_origin(self, module: str, exported_name: str) -> tuple[str, str] | None:
        return self._explicit.get((module, exported_name))

    def permits_wildcard_export(self, module: str, name: str) -> bool:
        """Whether `name` would actually be visible through `module`'s own
        `from module import *` / `export * from module` - honors a static
        `__all__` whitelist when one is known, degrades to "any public
        (non-underscore) name" when `__all__` is absent or only partially
        determinable (`PARTIAL_EXPORT_MAP`)."""
        whitelist = self._all_whitelist.get(module)
        if whitelist is not None and not self.is_partial_export(module):
            return name in whitelist
        return not name.startswith("_")


def resolve_export(
    module: str,
    symbol: str,
    export_registry: "ExportRegistry",
    symbol_table: GlobalSymbolTable,
    visited: set[tuple[str, str]] | None = None,
    depth: int = 0,
) -> str | None:
    """Recursively resolve `symbol` as it would actually be seen by a
    consumer importing it from `module` - walking through as many
    intermediate barrel/re-export files as necessary (Issue #6.2), with
    cycle prevention (`visited`) and a hard depth bound
    (`EXPORT_RESOLUTION_MAX_DEPTH`) guaranteeing termination on a circular
    import chain (Invariant #3: "resolve_export() terminates
    deterministically within <= 5 hops without recursion errors").

    Resolution order at each hop, matching the audit spec exactly:
      1. A direct explicit export in the registry (`from .x import Y`,
         `export { Y } from './x'`).
      2. Wildcard exports, tried in declared order, honoring the target
         module's own `__all__` whitelist if any.
      3. A local symbol actually defined in `module` itself (the base
         case - not a re-export at all, just the real definition).
    """
    if visited is None:
        visited = set()
    if depth > EXPORT_RESOLUTION_MAX_DEPTH or (module, symbol) in visited:
        return None
    visited = visited | {(module, symbol)}

    explicit = export_registry.explicit_origin(module, symbol)
    if explicit is not None:
        origin_module, origin_name = explicit
        # The origin might itself be nothing but another re-export hop
        # (a barrel re-exporting a barrel) - recurse to chase it to its
        # real definition. A verified real symbol always wins; an
        # *unverified* one-hop guess (`f"{origin_module}.{origin_name}"`
        # when it isn't actually in `symbol_table`) is deliberately never
        # returned - fabricating a string that was never Pass-1-registered
        # would just reintroduce, one level further down the chain, the
        # exact phantom-node problem this function exists to eliminate,
        # and would also defeat both the cycle guard and the depth bound
        # (a circular or over-deep chain would still "resolve" to a
        # made-up name instead of terminating with `None`, as Invariant #3
        # requires).
        candidate = f"{origin_module}.{origin_name}"
        if candidate in symbol_table:
            return candidate
        return resolve_export(origin_module, origin_name, export_registry, symbol_table, visited, depth + 1)

    for wildcard_module in export_registry.wildcard_targets(module):
        if not export_registry.permits_wildcard_export(wildcard_module, symbol):
            continue
        # The recursive call's own base case already tries
        # `symbol_table.resolve_in_module(wildcard_module, symbol)`, so
        # nothing further is needed here beyond trying the next
        # wildcard target on a miss.
        deeper = resolve_export(wildcard_module, symbol, export_registry, symbol_table, visited, depth + 1)
        if deeper is not None:
            return deeper

    return symbol_table.resolve_in_module(module, symbol)
