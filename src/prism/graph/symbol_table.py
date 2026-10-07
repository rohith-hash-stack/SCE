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
from enum import Enum


class SymbolRole(str, Enum):
    """A symbol's behavioral role, orthogonal to `SymbolInfo.kind`
    (class/function/method/interface/attribute - its *structural* shape).
    Computed once during Pass 1 (`ConcreteGraphBuilder._register_definition`)
    from deterministic AST/naming signals - no LLM, no embeddings, no
    hardcoded repo- or framework-specific path/package strings (see
    `prism.graph.concrete_builder`'s own role-classification helpers for
    the exact signal set and why a pure path-substring blacklist - the
    mechanism this replaces - has a real, measured gap a structural
    signal does not).

    `IMPLEMENTATION` is the default for every symbol that doesn't
    positively match a `VERIFICATION`/`INTERFACE` signal - production
    code is the common case, not a specially-detected one.
    """

    IMPLEMENTATION = "implementation"
    #: A test/assertion-bearing symbol (an xUnit-style test method or
    #: function, a pytest fixture, a Go `TestXxx(t *testing.T)` function) -
    #: a real graph node with real edges, never itself part of a debug/
    #: chain task's own causal pipeline.
    VERIFICATION = "verification"
    #: An abstract/declaration-only symbol with no real executable body
    #: (`pass`/`...`/`raise NotImplementedError` in Python, or no body at
    #: all) - a contract, not an implementation of one.
    INTERFACE = "interface"


#: JS/TS's own "this file *is* the directory it lives in" convention -
#: `core/index.ts` is how `import { x } from './core'` (a directory
#: reference, resolved by the bundler/`tsc` to `core/index.ts`) actually
#: gets satisfied, the exact analogue of Python's `__init__.py` below.
#: Gated on extension (not a bare `"index"` name check) so this can never
#: fire for a language with no such convention - a Python file genuinely
#: named `index.py` is not special to Python and must not be stripped.
_JS_INDEX_FILE_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")


def path_to_module(file_path: str, repo_root: str) -> str:
    """Convert a repo-relative or absolute file path into a dotted module name.

    ``src/auth/jwt.py`` (relative to `repo_root`) -> ``src.auth.jwt``.
    ``src/auth/__init__.py`` -> ``src.auth``.
    ``src/core/index.ts`` -> ``src.core``.

    The `index.ts`/`.tsx`/`.js`/`.jsx` case (added alongside the
    pre-existing `__init__` one) closes a real, confirmed bug: a
    directory-style import (`import { x } from './core'`, resolved to
    `core/index.ts` on disk) computes this same module string
    (`ConcreteGraphBuilder._resolve_js_specifier` never has an `index`
    segment to strip in the first place, since the import specifier
    itself never names it) - but before this fix, `core/index.ts`'s own
    module (used to register whatever it defines or re-exports) was the
    *different* string `"core.index"`, since nothing here stripped the
    trailing `index` segment. The two sides never matched, so every
    barrel-file re-export (`export { x } from './y'` inside an
    `index.ts`) was invisible to `resolve_export`/`ExportRegistry`
    (Issues #6/#7's own barrel-file mechanism) despite that mechanism
    being fully built and working for every case *except* this
    module-key mismatch - confirmed directly: `main.ts` importing
    `initTRPC` from `./core` (a real `core/index.ts` re-exporting it from
    `./initTRPC`) resolved to the non-existent node `"core.initTRPC"`
    instead of the real `"core.initTRPC.initTRPC"`, before this fix.
    """
    rel = os.path.relpath(file_path, repo_root)
    rel_no_ext, ext = os.path.splitext(rel)
    parts = [p for p in rel_no_ext.split(os.sep) if p not in ("", ".")]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    elif parts and parts[-1] == "index" and ext in _JS_INDEX_FILE_EXTENSIONS:
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
    role: SymbolRole = SymbolRole.IMPLEMENTATION


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
        #: Option C (`reports/symbol_table_collision_spike.md`): `{base_
        #: qualified_name: [base_qualified_name, suffixed_key, ...]}` -
        #: populated only when `add()` actually detects a genuine
        #: same-qualified-name collision (two real, different definitions
        #: - never a harmless re-registration of the same one, e.g. a
        #: cache-hit rehydration replaying an already-suffixed key
        #: unchanged). Empty for the overwhelming majority of real repos,
        #: which never collide at all. Queried via `collisions_for`.
        self.collisions: dict[str, list[str]] = {}

    def add(self, symbol: SymbolInfo) -> str:
        """Registers `symbol`, returning the real key it was stored
        under - identical to `symbol.qualified_name` unless a genuine
        collision was detected, in which case a deterministic `#N`
        suffix is appended and *that* key is returned. A caller that
        also maintains its own qualified-name-keyed structure alongside
        this table (`ConcreteGraphBuilder._def_nodes`) must use this
        return value, not `symbol.qualified_name` directly, to stay
        consistent with which real definition this table actually
        stored under which key.

        Collision detection (Option C, `reports/symbol_table_collision_
        spike.md`): two `add()` calls for the same `qualified_name` are
        the *same* real definition - a harmless re-registration, not a
        collision - iff they agree on both `file` and `line_range` (the
        two fields that together uniquely identify a real, physical
        definition site). This is exactly what happens on a cache-hit
        rehydration (`prism.runtime.index_cache`): the cached data
        already has any real collision's own suffix baked into its own
        serialized `qualified_name` string, so replaying it here never
        re-triggers this branch - every rehydrated entry's key is
        already unique by construction. A *genuine* collision (two
        really different definitions - e.g. a module's own real,
        top-level `param` function and an unrelated closure named
        `param` nested inside a different function, `docs/architecture_
        boundaries.md`-adjacent territory this table's own ancestor-walk
        limitation in `ConcreteGraphBuilder._register_definition`
        produces) keeps the *first*-registered definition under the
        clean, unsuffixed name - `get(qualified_name)`'s own contract is
        therefore "the primary/first real declaration for this name",
        never an arbitrary last-write-wins pick - and assigns every
        subsequent different one the next free `f"{qualified_name}#{n}"`
        (n starting at 2 - the base key is implicitly "#1"), recording
        the full real set in `self.collisions` for explicit querying
        rather than leaving it undiscoverable.
        """
        base_key = symbol.qualified_name
        existing = self._symbols.get(base_key)
        simple_name = base_key.rsplit(".", 1)[-1]

        if existing is None or (existing.file, existing.line_range) == (symbol.file, symbol.line_range):
            # First registration, or a harmless re-registration of the
            # exact same physical definition (a cache-hit rehydration
            # replaying an already-unique serialized key). Plain
            # overwrite - a real, pre-existing, unrelated ambiguity this
            # fix isn't scoped to touch: two genuinely different
            # qualified names sharing a bare simple name in one module
            # already resolved "whichever was registered last" before
            # this change and still does.
            key = base_key
            self._symbols[key] = symbol
            self._module_index.setdefault(symbol.module, {})[simple_name] = key
            # Also index by the class-qualified name (Class.method) so `self.method()`
            # resolution can look up `EnclosingClass.method` without the module prefix.
            if symbol.enclosing_class is not None:
                class_simple = symbol.enclosing_class.rsplit(".", 1)[-1]
                local_name = f"{class_simple}.{simple_name}"
                self._module_index.setdefault(symbol.module, {})[local_name] = key
            if symbol.kind in ("function", "method"):
                self._simple_name_index.setdefault(simple_name, []).append(key)
            return key

        # Genuine collision: two real, different definitions share
        # `base_key`. Normally `existing` keeps the canonical,
        # unsuffixed slot (the "first real declaration wins" rule) and
        # `symbol` is assigned the next free `#N` suffix - UNLESS
        # `existing` is a bare interface/stub declaration (the
        # `typing.overload` convention: one or more signature-only
        # stubs, each with an `...`/`pass` body, immediately followed by
        # the real, fully-bodied implementation) and `symbol` is a real
        # implementation, in which case `symbol` *promotes* into the
        # canonical slot instead and `existing` is demoted to the `#N`
        # sibling. Without this, first-wins would make an `@overload`
        # stub - never itself callable - the permanent `get()` answer
        # for names like `Jinja2Templates.__init__`, regressing this
        # table's prior (accidental, but correct for this specific,
        # extremely common pattern) last-write-wins behavior. Two stubs
        # colliding with each other (`symbol` also `INTERFACE`) still
        # resolve via plain first-wins, exactly like any other
        # collision - only a *real* implementation ever promotes, and a
        # stub arriving after a real implementation never bumps it.
        promote = existing.role is SymbolRole.INTERFACE and symbol.role is not SymbolRole.INTERFACE

        n = 2
        while f"{base_key}#{n}" in self._symbols:
            n += 1
        sibling_key = f"{base_key}#{n}"
        self.collisions.setdefault(base_key, [base_key]).append(sibling_key)

        # A suffixed/demoted registration never touches `_module_index` -
        # Rule D's bare-name lookup must keep resolving to the primary
        # declaration's own key exactly like `get()` does. In the
        # `promote` case that key is still `base_key` (only *which*
        # object lives there changes), so `_module_index` - already
        # pointing at `base_key` from the stub's own original
        # registration - needs no update there either.
        if promote:
            self._symbols[sibling_key] = existing
            existing.qualified_name = sibling_key
            self._symbols[base_key] = symbol
            if existing.kind in ("function", "method"):
                self._simple_name_index.setdefault(simple_name, []).append(sibling_key)
            return base_key

        # The stored object's own `qualified_name` must reflect the real
        # key it was registered under - otherwise two colliding siblings
        # report the identical `qualified_name` (the shared `base_key`)
        # despite living under different table keys, and every consumer
        # that reads a `SymbolInfo` back out (Polysemy Disambiguation's
        # `candidates_for_simple_name` included) can't tell them apart.
        symbol.qualified_name = sibling_key
        self._symbols[sibling_key] = symbol
        if symbol.kind in ("function", "method"):
            # Every real candidate for this bare simple name - base key
            # *and* any `#N` collision siblings - so `candidates_for_
            # simple_name` (Polysemy Disambiguation) can see and score
            # between them exactly as it already does for same-named
            # functions in different modules, now extended to same-named
            # functions colliding in the *same* module too.
            self._simple_name_index.setdefault(simple_name, []).append(sibling_key)
        return sibling_key

    def get(self, qualified_name: str) -> SymbolInfo | None:
        return self._symbols.get(qualified_name)

    def collisions_for(self, qualified_name: str) -> list[SymbolInfo]:
        """Every real `SymbolInfo` registered under `qualified_name`'s
        own real collision group - `[get(qualified_name)]` (a single-
        element list) for the overwhelming majority of names, which
        never collide with anything; the real, explicit full set
        (primary declaration first, then each `#N` sibling in
        registration order) for one that does. Never raises, never
        returns an empty list for a name that's genuinely in this table
        - only `[]` for a name that was never registered at all, the
        same "no fabricated answer" contract `get()` itself already
        has."""
        keys = self.collisions.get(qualified_name)
        if keys is None:
            symbol = self._symbols.get(qualified_name)
            return [symbol] if symbol is not None else []
        return [self._symbols[k] for k in keys if k in self._symbols]

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

    # Phase H (Issue #46): this is the whole alias-resolution mechanism -
    # `import my_package.core as mod` / `from utils.tools import helper
    # as h` are both recorded here as `aliases[local_name] = canonical_
    # target` (`_handle_python_import_name`, `concrete_builder.py`), and
    # `ConcreteGraphBuilder._resolve_reference_chain` consults `resolve()`
    # first, before the caller's own module or any wildcard import - a
    # `mod.run()`/`h()` call site resolves directly to its real qualified
    # target and never reaches `_resolve_ambiguous_call` (G44)'s scoring
    # fallback at all. Verified directly against the real resolution
    # pipeline, including against a same-simple-name decoy elsewhere in
    # the repo (an alias always wins, not just "usually scores highest") -
    # see tests/phase_h/test_engine_extensibility.py's
    # test_import_as_alias_resolves_directly/test_from_import_alias_
    # resolves_directly. No production behavior change was needed here;
    # this comment exists so the connection to Issue #46 is documented
    # rather than left to be independently rediscovered.


@dataclass
class InstanceTypeMap:
    """local variable -> fully-qualified class name, scoped to one function."""

    bindings: dict[str, str] = field(default_factory=dict)
    #: Phase B (G41): names bound to two *different* classes across
    #: separate assignments - branching initialization (`if cond: self.x
    #: = Real() else: self.x = Mock()`, or two different methods of the
    #: same class each assigning a different concrete type to the same
    #: attribute name). `resolve()` still returns the most recent
    #: binding unchanged - every existing caller keeps its current
    #: behavior - but a caller that also checks `is_ambiguous` can choose
    #: to link with reduced confidence instead of treating this as a
    #: clean, single-type resolution.
    ambiguous: set[str] = field(default_factory=set)
    #: Builtin-Receiver Exclusion fix: names known, from a real literal
    #: display, comprehension, or bare builtin-factory call in the same
    #: scope (`field_names = set()` / `= {}` / `= [x for x in y]` /
    #: `= collections.deque()`), to hold a Python builtin container/
    #: primitive - never a real, indexed class. Deliberately a separate
    #: set from `bindings` rather than a fake "qualified_class" entry:
    #: there is no real class to resolve `.<method>` against, so a
    #: caller must check `is_builtin` *before* falling back to bare-name/
    #: polysemy resolution for a receiver this set names, and must never
    #: treat membership here as "unresolved, keep trying" (see
    #: `ConcreteGraphBuilder._resolve_segments`/`_resolve_calls_in_function`).
    builtin_bindings: set[str] = field(default_factory=set)

    def bind(self, var_name: str, qualified_class: str, inferred: bool = False) -> None:
        existing = self.bindings.get(var_name)
        if existing is not None and existing != qualified_class:
            self.ambiguous.add(var_name)
        self.bindings[var_name] = qualified_class
        if inferred:
            self.inferred.add(var_name)
        else:
            self.inferred.discard(var_name)
        # A real class binding always supersedes a stale builtin marking
        # from an earlier assignment to the same name (most-recent-
        # assignment-wins, matching `ambiguous`'s own convention above).
        self.builtin_bindings.discard(var_name)

    def bind_builtin(self, var_name: str) -> None:
        self.builtin_bindings.add(var_name)
        # Symmetric with `bind()`: a fresh builtin-container assignment
        # supersedes any earlier real-class binding/ambiguity flag for
        # the same name.
        self.bindings.pop(var_name, None)
        self.ambiguous.discard(var_name)
        self.inferred.discard(var_name)

    def resolve(self, var_name: str) -> str | None:
        return self.bindings.get(var_name)

    def is_ambiguous(self, var_name: str) -> bool:
        return var_name in self.ambiguous

    def is_builtin(self, var_name: str) -> bool:
        return var_name in self.builtin_bindings

    def is_inferred(self, var_name: str) -> bool:
        return var_name in self.inferred

    #: Locals assigned from an attribute access (`opts = model._meta`):
    #: {local name: attribute name}, so a later `opts.get_field(...)` can be
    #: typed through the repo-wide attribute index
    #: (`ConcreteGraphBuilder._resolve_by_attribute`).
    attr_aliases: dict[str, str] = field(default_factory=dict)
    #: Names whose class was *inferred* (from a call's inferred return
    #: class) rather than read off a constructor call or an annotation -
    #: calls through them link as best-effort (`TENTATIVE_CALL`) edges.
    inferred: set[str] = field(default_factory=set)


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
