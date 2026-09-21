"""Bottom layer: the Concrete Graph (G_C).

Implements the two-pass cross-file linker described in HLD section 4.1:

  Pass 1 - Global Definition Collection: walk every supported source file
  with tree-sitter, register every class/function/method into the
  `GlobalSymbolTable`.

  Pass 2 - Scoped Resolution & Edge Assembly: for every function/method,
  build a `LocalImportMap` (aliases -> qualified symbols) and an
  `InstanceTypeMap` (local vars / `self.<attr>` -> qualified class), then
  resolve every call expression against Rules A-D and add a `CALLS` edge to
  `G_C`.

Full precision (Rules A-D, instance binding, relative-import resolution) is
implemented for Python, since that is the language every HLD example uses.
Other supported languages (JavaScript/TypeScript/Go) get Stage 1 definition
collection plus best-effort Stage 2 linking (Rules B/C/D only - no
constructor-based instance binding), which is called out explicitly here
rather than silently pretending to be exact.
"""
from __future__ import annotations

import logging
import os
import re
import threading

import networkx as nx
from tree_sitter import Node

from prism.parser.lang_config import (
    ASSIGNMENT_NODE_TYPE,
    CALL_NODE_TYPE,
    CLASS_NODE_TYPES,
    DECORATED_WRAPPER_TYPES,
    RETURN_STATEMENT_NODE_TYPE,
    SELF_TOKEN_TEXT,
    call_callee_segments,
    collect_decorator_texts,
    find_all,
    flatten_reference_chain,
    is_super_call_node,
    iter_scoped_nodes,
    super_call_method_name,
)
from prism.parser.cache import parse_file_cached
from prism.parser.queries import run_query
from prism.parser.tree_sitter_loader import LanguageID, ParsedFile, node_text
from prism.graph.call_site import (
    DynamicEdgeSentinel,
    compute_call_site_context,
    detect_call_site_hazard,
    dynamic_edge_sentinel_id,
)
from prism.graph.weights import MAX_INHERITANCE_DEPTH
from prism.graph.symbol_table import (
    ExportRegistry,
    GlobalSymbolTable,
    InstanceTypeMap,
    LocalImportMap,
    SymbolInfo,
    SymbolRole,
    arity_match,
    locality_distance,
    namespace_match,
    path_to_module,
    resolve_export,
    score_candidate,
    unresolved_polymorphic_node_id,
    POLYSEMY_THRESHOLD,
)


# Edge relations that represent actual runtime control/data flow reaching
# a symbol - the notion of "reachable from the seed" the distance engine
# and knapsack packer (`prism.slicer`) have always operated on, back when
# `CALLS` (plus, now, `INSTANTIATES` - a bare `Foo()`/`new Foo()`
# constructor invocation was always folded into a plain `CALLS` edge
# before the behavioral-contract work split it into its own relation) was
# the only relation `G_C` had.
#
# `EXTENDS`/`IMPLEMENTS`/`OVERRIDES` (Issue #9) are included as of this
# change - excluding them entirely caused a real "phantom method" failure
# mode of its own: `self.validate()` calling a method only ever defined on
# a base class had no reachable definition at all, since the traversal a
# seed's own blast radius is computed over never crossed an inheritance
# edge to find it. The original exclusion (still true, and still the
# reason `READS_STATE` stays excluded) was a real, measured regression -
# a base class's *entire* call graph flooding in at full priority
# alongside the seed's own direct callees, which blew two real
# regression-test budget assertions. `DistanceEngine._weighted_undirected`
# is what actually protects against a repeat of that this time: an
# EXTENDS/IMPLEMENTS/OVERRIDES hop costs strictly more than a normal
# CALLS/INSTANTIATES hop (see that method's own relation-weight table),
# so an inherited method is reachable and correctly resolvable, but never
# outranks a same-or-fewer-hop behavioral neighbor for a scarce token
# budget. `READS_STATE` stays excluded - a bare attribute read pulls in
# unrelated attribute nodes with no comparable "this is now unreachable
# without it" failure mode to justify the same trade. `EMBEDS` (Item 5)
# is Go's own equivalent addition - anonymous struct embedding, the real
# mechanism Go composition/"inheritance" uses in place of EXTENDS syntax
# it doesn't have - weighted identically to EXTENDS for the same reason.
TRAVERSABLE_RELATIONS = frozenset({"CALLS", "INSTANTIATES", "EXTENDS", "IMPLEMENTS", "OVERRIDES", "EMBEDS"})

# Issue A5 (post-implementation audit): inheritance-graph resolution -
# EXTENDS/IMPLEMENTS edges above, `_mro_ancestors`/`_link_overrides`
# below - is not one uniform capability across languages. Stated
# precisely, per language, so "MRO" is never read as a blanket claim:
#
#   - Python: `_mro_ancestors` walks EXTENDS edges depth-first in
#     declared base order - an approximation of real C3 linearization,
#     exact for single inheritance and ordinary (non-diamond) multiple
#     inheritance, not guaranteed exact for a genuine diamond (see that
#     method's own docstring).
#   - JavaScript/TypeScript/TSX: `_link_ts_heritage` builds the same
#     EXTENDS/IMPLEMENTS edges from `class_heritage` nodes, so the same
#     `_mro_ancestors` walk applies - this is JS/TS *prototype-chain*
#     resolution (a class's `extends` clause), not literal C3 MRO, but
#     structurally single-chain in JS (no multiple inheritance to
#     linearize) so the distinction rarely matters in practice.
#   - Go: no EXTENDS/IMPLEMENTS edges are ever built (`_link_class_relations`
#     returns immediately for Go - Go has no class/inheritance syntax at
#     all). Item 5 adds Go's real equivalent instead: anonymous struct
#     embedding, modeled as its own `EMBEDS` relation (`_link_go_embeds`),
#     with Item 7's BFS-with-depth-tracking method promotion
#     (`_go_promoted_method`) enforcing Go's actual compile-time shadowing
#     rules (nearest-embedding-depth wins; a same-depth collision across
#     two embedded types is `AMBIGUOUS_EMBEDDED_COLLISION`, never
#     guessed) - deliberately not called "Go MRO" anywhere in this
#     codebase's documentation, since Go's real rules are shadowing-by-
#     depth, not C3 linearization.
#   - Java/C#: also excluded by the same early return, despite being
#     single-inheritance languages where a real (simpler-than-Python's)
#     MRO would be well-defined - genuinely unimplemented, not merely
#     untested; see `README.md`'s Language Capability Matrix.


#: Item 4 (second post-implementation audit): logger for per-file
#: indexing failures caught by `pass1_collect_definitions`/
#: `pass2_resolve_calls`'s error boundaries - a caller that configures
#: Python's `logging` module sees these; `ConcreteGraphBuilder.
#: index_errors` (populated regardless of whether logging is configured)
#: is what `prism index`'s own CLI output actually reads.
_LOGGER = logging.getLogger(__name__)


class ConcreteGraphBuilder:
    """Builds `G_C` from a set of source files via the two-pass linker."""

    def __init__(self, repo_root: str, symbol_table: GlobalSymbolTable | None = None) -> None:
        self.repo_root = repo_root
        #: Item 4: `[{"file": path, "category": "RecursionError" | ...,
        #: "message": str, "stage": "pass1" | "pass2"}, ...]` - one entry
        #: per file an error boundary caught and skipped, in the order
        #: encountered. A file appearing here was NOT indexed (Pass 1) or
        #: was partially indexed but not call-resolved (Pass 2) - its
        #: definitions/calls are simply absent from the graph, not
        #: present-but-wrong.
        self.index_errors: list[dict[str, str]] = []
        self.symbol_table = symbol_table if symbol_table is not None else GlobalSymbolTable()
        self.graph = nx.DiGraph()
        self._parsed_files: dict[str, ParsedFile] = {}
        self._def_nodes: dict[str, Node] = {}
        self._methods_by_class: dict[str, list[str]] = {}
        self._calls_graph_cache: nx.DiGraph | None = None
        #: Barrel-file / re-export tracking (Issues #6/#7) - see
        #: `prism.graph.symbol_table.ExportRegistry`.
        self.export_registry = ExportRegistry()
        #: Item 3 Stage 2 (second post-implementation audit): lazily
        #: built, memoized by `_go_method_registry`.
        self._go_method_registry_cache: dict[str, list[str]] | None = None
        #: Item 5 follow-through: lazily built, memoized by
        #: `_go_class_registry`.
        self._go_class_registry_cache: dict[str, list[str]] | None = None
        #: Item 14 (second post-implementation audit): guards the three
        #: lazy-memoization caches above (`_calls_graph_cache`,
        #: `_go_method_registry_cache`, `_go_class_registry_cache`) - the
        #: only mutable state this builder's read path (distance engine,
        #: knapsack packer, compressor, blueprint miner) can still touch
        #: once indexing has completed (see `calls_graph`'s own docstring:
        #: "this builder is only ever fully rebuilt, never incrementally
        #: mutated after indexing completes"). Without a lock, two threads
        #: racing to compute one of these caches for the first time would
        #: each independently build an equivalent (same-content) object
        #: and harmlessly clobber each other's assignment - not a
        #: correctness bug in CPython today, but not a guarantee either;
        #: this makes "first concurrent access is safe and deterministic"
        #: an explicit, tested property instead of an implicit accident of
        #: the GIL. One lock for all three: they're cheap to build and
        #: never contended after warmup, so a single lock is simpler than
        #: three without a measurable cost.
        self._lazy_cache_lock = threading.Lock()
        #: Item 3: set by `_resolve_segments` immediately before it
        #: returns via the Go-only Stage-2 "codebase-unique receiver"
        #: fallback, read by its one caller (`_resolve_calls_in_function`)
        #: right after the call to decide whether to mark the resulting
        #: edge `kind="TENTATIVE_CALL"`. A plain instance flag rather
        #: than a richer return type change is safe here specifically
        #: because call resolution is single-threaded and strictly
        #: sequential (one call-node fully resolved before the next
        #: starts) throughout this class - not a general-purpose pattern.
        self._last_resolution_was_tentative = False
        #: Phase B (G41): set by `_resolve_segments` immediately before it
        #: returns via an attribute-chain resolution (`self.<attr>.<method>`)
        #: whose receiver was bound to more than one concrete class across
        #: different assignments - same single-flag/single-threaded
        #: convention as `_last_resolution_was_tentative` above.
        self._last_resolution_was_ambiguous = False
        #: Builtin-Receiver Exclusion fix: set by `_resolve_segments`
        #: immediately before it returns `None` because the receiver was
        #: tracked as a Python builtin container/primitive
        #: (`InstanceTypeMap.is_builtin`), never a real class - checked by
        #: `_resolve_calls_in_function` to suppress the G44 bare-name/
        #: polysemy fallback for that call entirely (the receiver is
        #: definitively known, not merely unresolved), the same "return
        #: None but flag why" convention `_last_resolution_was_tentative`/
        #: `_last_resolution_was_ambiguous` above already establish.
        self._last_resolution_was_builtin_receiver = False
        #: Item 3: `go_call_resolution_ratio` diagnostic numerator/
        #: denominator - see that property's own docstring.
        self._go_receiver_call_sites_total = 0
        self._go_receiver_call_sites_resolved = 0
        #: Item 7: set by `_go_promoted_method` when it finds a same-
        #: embedding-depth name collision (ambiguous, never guessed) -
        #: `None` otherwise, including "no promoted method found at
        #: all". Same single-threaded/sequential caveat as
        #: `_last_resolution_was_tentative` above.
        self._last_go_embedded_collision: str | None = None

    def parsed_file(self, path: str) -> ParsedFile | None:
        return self._parsed_files.get(path)

    @property
    def go_call_resolution_ratio(self) -> float:
        """Item 3: `Resolved Go Method Calls / Total Identified Go Method
        Call Expressions` - counted during `_resolve_calls_in_function`'s
        Go path over every "receiver-shaped" call site (>= 2 segments,
        base identifier not a known import alias, so `gin.Default()`-
        style package calls are excluded from both numerator and
        denominator - this metric is about receiver method resolution
        specifically, not overall Go call resolution). `1.0` (not an
        error/NaN) when the repository has no such call sites at all.
        Includes Stage 2's tentative resolutions in the numerator (a
        best-effort match still counts as "resolved" for this ratio,
        distinct from - and looser than - a genuinely confident CALLS
        edge; see `kind="TENTATIVE_CALL"` on the edge itself for that
        distinction).
        """
        if self._go_receiver_call_sites_total == 0:
            return 1.0
        return round(self._go_receiver_call_sites_resolved / self._go_receiver_call_sites_total, 4)

    @property
    def calls_graph(self) -> nx.DiGraph:
        """The pure behavioral-call subgraph (`CALLS`/`INSTANTIATES` edges
        only) that `prism.slicer`'s distance engine and knapsack packer
        traverse - see `TRAVERSABLE_RELATIONS`'s own docstring for why this
        must stay separate from `self.graph`'s full, richer edge set.
        Recomputed lazily and cached; invalidated automatically the next
        time this builder's `graph` identity changes is NOT handled here
        (this builder is only ever fully rebuilt, never incrementally
        mutated after indexing completes, so a one-shot cache is safe).

        Item 14 (second post-implementation audit): double-checked
        locking against `_lazy_cache_lock` - the fast path (already
        warm, the overwhelmingly common case once any query has run) never
        takes the lock at all; only the first, one-time build under
        concurrent first access does.
        """
        if self._calls_graph_cache is None:
            with self._lazy_cache_lock:
                if self._calls_graph_cache is None:
                    view = nx.DiGraph()
                    view.add_nodes_from(self.graph.nodes(data=True))
                    for u, v, data in self.graph.edges(data=True):
                        if data.get("relation", "CALLS") in TRAVERSABLE_RELATIONS:
                            view.add_edge(u, v, **data)
                    self._calls_graph_cache = view
        return self._calls_graph_cache

    def apply_runtime_overlay(
        self,
        edge_counts: dict[tuple[str, str], int],
        edge_errors: dict[tuple[str, str], int] | None = None,
        edge_breakdown: dict[tuple[str, str], dict[str, int]] | None = None,
    ) -> None:
        """Non-destructively merges Phase-2 runtime execution telemetry
        (`prism.runtime.trace_ingester.AggregatedTrace`, passed here as
        plain dicts rather than that dataclass to avoid a `graph -> runtime`
        import - `prism.runtime.reconciler` already imports this module the
        other way around) onto this builder's own graph as edge/node
        *attributes only*.

        This is deliberately the one and only thing it does: never adds a
        node, never adds an edge, never removes either - the "Static AST as
        Hard Ground Truth" invariant (Section 2.1.1 of the hybrid-runtime-
        analysis spec). A trace entry whose `(caller, callee)` doesn't
        match a real static edge is silently skipped, not synthesized into
        a new one - that's `prism.runtime.reconciler`'s separate, already-
        existing `RUNTIME_DISCOVERED` mechanism (a deliberately distinct
        code path with its own, much higher bar for creating a new edge
        from dynamic evidence alone), not this method's job.
        """
        errors = edge_errors or {}
        breakdown = edge_breakdown or {}
        observed_targets: set[str] = set()

        for (caller, callee), count in edge_counts.items():
            if not self.graph.has_edge(caller, callee):
                continue
            edge = self.graph.edges[caller, callee]
            edge["runtime_hits"] = edge.get("runtime_hits", 0) + count
            if (caller, callee) in errors:
                edge["runtime_errors"] = edge.get("runtime_errors", 0) + errors[(caller, callee)]
            if (caller, callee) in breakdown:
                edge["runtime_breakdown"] = breakdown[(caller, callee)]
            observed_targets.add(callee)
            # Active Trace Path Prioritization (Issue 4): ACTIVE (real
            # hits, no recorded error), ERROR_SINK (real hits AND at
            # least one recorded error/non-zero-exit/panic observation -
            # a signal to anchor root-cause diagnosis on, not an
            # unobserved fallback branch), UNOBSERVED (statically
            # reachable, zero hits in this trace). Every edge this loop
            # touches has `count > 0` by construction (`edge_counts` only
            # ever holds observed hits), so ACTIVE/ERROR_SINK are the only
            # two outcomes here - UNOBSERVED is assigned below, to every
            # edge this loop never reaches at all.
            edge["execution_status"] = "ERROR_SINK" if edge.get("runtime_errors", 0) > 0 else "ACTIVE"

        for u, v, edge in self.graph.edges(data=True):
            if (u, v) not in edge_counts and edge.get("relation", "CALLS") in TRAVERSABLE_RELATIONS:
                edge.setdefault("execution_status", "UNOBSERVED")

        # Section 2.1.5 - Separation of "Unobserved" vs "Dead": every node
        # already in the static graph keeps `statically_reachable=True`
        # unconditionally; `observed` reflects only whether any accepted
        # trace actually hit it, and a node with `observed=False` is not
        # touched or removed in any other way.
        for node in self.graph.nodes:
            data = self.graph.nodes[node]
            data["statically_reachable"] = True
            if node in observed_targets:
                data["observed"] = True
                data["runtime_hits"] = sum(count for (_caller, callee), count in edge_counts.items() if callee == node)
            else:
                data.setdefault("observed", False)

        # The cached `calls_graph` view copies edge/node attribute dicts at
        # construction time (see that property's own docstring) - mutating
        # `self.graph` in place after it was already built would otherwise
        # leave the cached view holding stale (pre-overlay) attributes.
        self._calls_graph_cache = None

    def def_node(self, qualified_name: str) -> Node | None:
        return self._def_nodes.get(qualified_name)

    # ------------------------------------------------------------------ #
    # Pass 1: Global Definition Collection
    # ------------------------------------------------------------------ #
    def pass1_collect_definitions(self, files: list[str]) -> None:
        for path in sorted(files):
            # Item 4 (second post-implementation audit): a per-file error
            # boundary. Prism indexes arbitrary, potentially adversarial
            # or merely pathological repositories - a single malformed or
            # maliciously-deep file must not abort the whole `index`/
            # `query` run for every other file in the repo. Catches
            # RecursionError (a Tree-sitter-parseable but pathologically
            # nested file defeating this class's own Python-level
            # recursive walks - see `iter_scoped_nodes`'s own
            # MAX_SCOPED_NODE_DEPTH guard for the narrower, already-fixed
            # case this is defense-in-depth for at the whole-file level)
            # and UnicodeDecodeError (a non-UTF-8-decodable file reaching
            # a decode call this deep that isn't already guarded by
            # `errors="replace"` - `open(path, "rb")` itself never raises
            # this, but a downstream `.decode()` without that guard
            # could). Deliberately narrow (not a blanket `except
            # Exception`) - an unexpected error class should still
            # surface as a real bug, not be silently swallowed here.
            try:
                parsed = parse_file_cached(path)
                if parsed is None:
                    continue
                self._parsed_files[path] = parsed
                module = self._module_for_file(parsed)
                self._collect_definitions_in_file(parsed, module)
            except (RecursionError, UnicodeDecodeError) as exc:
                self._record_index_error(path, exc, stage="pass1")

    def _record_index_error(self, path: str, exc: Exception, stage: str) -> None:
        category = exc.__class__.__name__
        message = str(exc) or category
        _LOGGER.error("INDEX_ERROR_SKIPPED [%s] %s (%s): %s", stage, path, category, message)
        self.index_errors.append({"file": path, "category": category, "message": message, "stage": stage})
        # A file that failed pass1 may have partially registered
        # definitions/parsed state - remove it so pass2 (which iterates
        # `self._parsed_files`, not the original file list) doesn't then
        # try to link calls against a half-indexed file.
        self._parsed_files.pop(path, None)

    def _module_for_file(self, parsed: ParsedFile) -> str:
        """The dotted "module" every symbol in this file is qualified
        under. For most languages this is derived from the file's own path
        (import paths mirror the directory tree) - but Java/C# resolve
        symbols by declared `package`/`namespace`, not file location (a
        repo's build layout, e.g. Maven's `src/main/java/...` prefix, is
        not part of the qualified name a real `import`/`using` statement
        ever names), so their own in-file declaration is authoritative
        when present.
        """
        if parsed.language_id in (LanguageID.JAVA, LanguageID.CSHARP):
            declared = _parse_package_or_namespace(parsed)
            if declared is not None:
                return declared
        return path_to_module(parsed.path, self.repo_root)

    def _collect_definitions_in_file(self, parsed: ParsedFile, module: str) -> None:
        lang = parsed.language_id
        captures = run_query(lang, "definitions", parsed.root_node)
        def_nodes: list[tuple[Node, bool, str | None]] = []
        for n in captures.get("def.class", []):
            def_nodes.append((n, True, None))
        # Item 17 (second post-implementation audit): TypeScript's
        # `interface_declaration` - previously not captured by this query
        # at all, so a TS interface was never registered as a symbol -
        # gets its own `kind="interface"`, distinct from `"class"`
        # (TS distinguishes the two at the type-checker level; an
        # interface has no runtime body, can't be `new`'d, and can only
        # ever appear on the right of `implements`, never `extends`, from
        # a class's own perspective - real, checkable differences worth
        # keeping visible rather than flattening both into "class").
        for n in captures.get("def.interface", []):
            def_nodes.append((n, True, "interface"))
        for n in captures.get("def.function", []):
            def_nodes.append((n, False, None))
        def_nodes.sort(key=lambda t: t[0].start_byte)
        for node, is_class, force_kind in def_nodes:
            self._register_definition(node, is_class, parsed, module, force_kind=force_kind)
        if lang == LanguageID.PYTHON:
            self._collect_attribute_definitions(parsed, module)

    def _register_definition(
        self, node: Node, is_class: bool, parsed: ParsedFile, module: str, force_kind: str | None = None
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = node_text(name_node, parsed.source)
        lang = parsed.language_id
        class_types = CLASS_NODE_TYPES[lang]

        enclosing_class: str | None = None
        if lang == LanguageID.GO and node.type == "method_declaration":
            # Issue B1: a Go method has no enclosing class *node* to walk
            # up to at all - `func (c *Context) JSON(...)` is a top-level
            # package declaration with a receiver clause, structurally
            # nothing like Python/JS's nested class body (this is exactly
            # why the ancestor walk below, which every other language
            # relies on, silently registered every Go method as a bare
            # top-level function prior to this fix). The receiver type
            # is read directly from the `receiver:` field instead.
            receiver_type = _go_receiver_type(node, parsed)
            if receiver_type is not None:
                enclosing_class = f"{module}.{receiver_type}"
        else:
            cursor = node.parent
            while cursor is not None:
                if cursor.type in class_types:
                    cls_name_node = cursor.child_by_field_name("name")
                    if cls_name_node is not None:
                        enclosing_class = f"{module}.{node_text(cls_name_node, parsed.source)}"
                    break
                cursor = cursor.parent

        if enclosing_class is not None:
            qualified_name = f"{enclosing_class}.{name}"
            kind = "method"
        else:
            qualified_name = f"{module}.{name}"
            kind = force_kind if force_kind is not None else ("class" if is_class else "function")

        outer = node
        wrapper_types = DECORATED_WRAPPER_TYPES.get(lang, set())
        if node.parent is not None and node.parent.type in wrapper_types:
            outer = node.parent

        line_range = (outer.start_point[0] + 1, outer.end_point[0] + 1)
        enclosing_class_info = self.symbol_table.get(enclosing_class) if enclosing_class is not None else None
        enclosing_class_role = enclosing_class_info.role if enclosing_class_info is not None else None
        role = _classify_symbol_role(node, is_class, parsed, name, enclosing_class_role)
        symbol = SymbolInfo(
            qualified_name=qualified_name,
            kind=kind,
            file=parsed.path,
            line_range=line_range,
            language_id=lang,
            module=module,
            enclosing_class=enclosing_class,
            role=role,
        )
        self.symbol_table.add(symbol)
        self._def_nodes[qualified_name] = node
        self.graph.add_node(
            qualified_name,
            kind=kind,
            file=parsed.path,
            line_range=line_range,
            language_id=lang,
            module=module,
            enclosing_class=enclosing_class,
            role=role,
        )
        if enclosing_class is not None and kind == "method":
            self._methods_by_class.setdefault(enclosing_class, []).append(qualified_name)

    # -- Attribute definitions (module/class/instance-level assignments) - #
    def _collect_attribute_definitions(self, parsed: ParsedFile, module: str) -> None:
        """A fourth symbol kind, alongside class/function/method: a simple
        name bound by assignment at module scope (`db_for_write =
        _router_func("db_for_write")`), class-body scope (`_iterable_class
        = ModelIterable`), or via `self.<attr> = ...` inside any of a
        class's own methods (`self._middleware_chain = handler`). These are
        real, referenceable Python symbols with no `def`/`class` keyword of
        their own - without indexing them, a call or mention referencing
        one is indistinguishable, by simple-name matching, from a genuine
        hallucination. Confirmed via a live gpt-4o-mini run against
        django/django: `QuerySet._iterable_class`,
        `BaseHandler._middleware_chain`, and `router.db_for_write` are all
        real, correct code that a downstream hallucination checker flagged
        as fabricated purely because none of them had ever been indexed.
        """
        assign_type = ASSIGNMENT_NODE_TYPE.get(parsed.language_id)
        if assign_type is None:
            return
        lang = parsed.language_id
        self_tokens = SELF_TOKEN_TEXT[lang]

        for assign in self._direct_assignments(parsed.root_node, assign_type):
            self._register_simple_target_attribute(assign, parsed, module, qualifier=module, enclosing_class=None)

        for class_node in find_all(parsed.root_node, CLASS_NODE_TYPES[lang]):
            name_node = class_node.child_by_field_name("name")
            if name_node is None:
                continue
            class_qname = f"{module}.{node_text(name_node, parsed.source)}"

            body = class_node.child_by_field_name("body")
            if body is not None:
                for assign in self._direct_assignments(body, assign_type):
                    self._register_simple_target_attribute(
                        assign, parsed, module, qualifier=class_qname, enclosing_class=class_qname
                    )

            for method_qname in self._methods_by_class.get(class_qname, []):
                method_node = self._def_nodes.get(method_qname)
                if method_node is None:
                    continue
                for assign in iter_scoped_nodes(method_node, {assign_type}, lang):
                    self._register_self_attribute(assign, parsed, module, class_qname, self_tokens)

    @staticmethod
    def _direct_assignments(container: Node, assign_type: str) -> list[Node]:
        """Assignment nodes that are direct statements of `container` (each
        wrapped in its own `expression_statement` child) - not nested inside
        any function or class defined within it. tree-sitter-python always
        wraps a bare top-level `x = 1` as `expression_statement -> assignment`,
        so this is a one-level unwrap, not a full subtree search.
        """
        found = []
        for child in container.children:
            if child.type != "expression_statement":
                continue
            for grandchild in child.children:
                if grandchild.type == assign_type:
                    found.append(grandchild)
        return found

    def _register_simple_target_attribute(
        self, assign: Node, parsed: ParsedFile, module: str, qualifier: str, enclosing_class: str | None
    ) -> None:
        target = assign.child_by_field_name("left")
        if target is None or target.type != "identifier":
            return
        name = node_text(target, parsed.source)
        self._register_attribute(f"{qualifier}.{name}", assign, parsed, module, enclosing_class)

    def _register_self_attribute(
        self, assign: Node, parsed: ParsedFile, module: str, class_qname: str, self_tokens: set[str]
    ) -> None:
        target = assign.child_by_field_name("left")
        if target is None:
            return
        segments = flatten_reference_chain(target, parsed.source, parsed.language_id)
        if not segments or len(segments) != 2 or segments[0] not in self_tokens:
            return
        self._register_attribute(f"{class_qname}.{segments[1]}", assign, parsed, module, class_qname)

    def _register_attribute(
        self, qualified_name: str, node: Node, parsed: ParsedFile, module: str, enclosing_class: str | None
    ) -> None:
        if qualified_name in self.symbol_table:
            # A real def/class always wins; among multiple attribute
            # assignments to the same name (e.g. re-set in more than one
            # method), the first one found stays authoritative.
            return
        line_range = (node.start_point[0] + 1, node.end_point[0] + 1)
        symbol = SymbolInfo(
            qualified_name=qualified_name,
            kind="attribute",
            file=parsed.path,
            line_range=line_range,
            language_id=parsed.language_id,
            module=module,
            enclosing_class=enclosing_class,
        )
        self.symbol_table.add(symbol)
        self.graph.add_node(
            qualified_name,
            kind="attribute",
            file=parsed.path,
            line_range=line_range,
            language_id=parsed.language_id,
            module=module,
            enclosing_class=enclosing_class,
        )

    # ------------------------------------------------------------------ #
    # Pass 2: Scoped Resolution & Edge Assembly
    # ------------------------------------------------------------------ #
    def pass2_resolve_calls(self, files: list[str]) -> None:
        classes_by_file: dict[str, list[str]] = {}
        for symbol in self.symbol_table:
            # Item 17: "interface" joined this collection so
            # _link_class_relations/_link_go_embeds's per-file loops
            # process TS interfaces too (an interface can itself
            # `extends` another interface - see _link_ts_heritage's own
            # extends_type_clause handling).
            if symbol.kind in ("class", "interface"):
                classes_by_file.setdefault(symbol.file, []).append(symbol.qualified_name)

        # Sub-pass 2a: build every file's own import map and register its
        # exports/re-exports into `self.export_registry` *before* any call
        # resolution happens (Issues #6/#7). This must fully complete
        # first: a file whose calls resolve through a barrel import (`from
        # app import OrderService`) needs `app`'s own re-export of
        # `OrderService` already registered even if `app/__init__.py`
        # happens to sort after this file alphabetically - `resolve_export`
        # has no way to "wait" for a registration that hasn't happened yet.
        import_maps: dict[str, LocalImportMap] = {}
        for path in sorted(files):
            parsed = self._parsed_files.get(path)
            if parsed is None:
                continue
            # Item 4: same per-file error boundary as Pass 1 (see that
            # method's own comment) - a file that crashes here is popped
            # from `self._parsed_files` so sub-pass 2b's own lookup
            # (`import_maps[path]`, which would otherwise KeyError on a
            # file this loop failed to populate) naturally skips it too.
            try:
                module = self._module_for_file(parsed)
                import_map = self._build_import_map(parsed, module)
                import_maps[path] = import_map
                self._register_exports(parsed, module, import_map)
                self._link_class_relations(classes_by_file.get(path, []), parsed, module, import_map)
                self._link_go_embeds(classes_by_file.get(path, []), parsed, module)
            except (RecursionError, UnicodeDecodeError) as exc:
                self._record_index_error(path, exc, stage="pass2a")

        # Sub-pass 2a-ter: OVERRIDES detection (Issue #9) needs every
        # class's own EXTENDS edge already in place - a subclass and its
        # base can live in different files processed in either order, so
        # this can only safely run once every file's own `_link_class_
        # relations` call above has finished, exactly the same ordering
        # requirement `_register_exports` has for barrel files.
        self._link_overrides()

        # Sub-pass 2b: resolve every call site, now that the whole repo's
        # export registry and class-relation graph (EXTENDS/IMPLEMENTS/
        # OVERRIDES) are both complete.
        for path in sorted(files):
            parsed = self._parsed_files.get(path)
            if parsed is None or path not in import_maps:
                continue
            # Item 4: same per-file error boundary, one file's crash here
            # (resolving its own calls) skips only that file's call
            # resolution - its Pass 1 definitions and any EXTENDS/EMBEDS/
            # OVERRIDES edges already linked in sub-pass 2a above are
            # unaffected, so the rest of the repo (including code that
            # calls *into* this file's own symbols) still resolves
            # normally.
            try:
                module = self._module_for_file(parsed)
                import_map = import_maps[path]
                file_symbols = [
                    qname
                    for qname in self._def_nodes
                    if (symbol := self.symbol_table.get(qname)) is not None
                    and symbol.file == path
                    and symbol.kind in ("function", "method")
                ]
                self._resolve_calls_for_file_symbols(file_symbols, parsed, module, import_map)
            except (RecursionError, UnicodeDecodeError) as exc:
                self._record_index_error(path, exc, stage="pass2b")

    def _resolve_calls_for_file_symbols(
        self, file_symbols: list[str], parsed: ParsedFile, module: str, import_map: LocalImportMap
    ) -> None:
        # Issue B1 follow-through: Go joined this tuple once receiver/
        # parameter-typed binding (`_build_function_instance_map`'s
        # Go-specific block, below `_bind_go_typed_parameters`) gave
        # it a real (if narrower - same-package bare types only, no
        # constructor-call tracking) instance-binding mechanism of
        # its own. `_build_class_instance_map` (the other consumer
        # gated by this tuple) stays a safe no-op for Go regardless -
        # `ASSIGNMENT_NODE_TYPE` has no Go entry, so it returns an
        # empty map immediately.
        #
        # Phase J: JS/TS/TSX joined this tuple once `_constructor_call_
        # segments` learned `new_expression` and `_LOCAL_VAR_DECLARATOR_
        # TYPE` gained entries for them (both above) - before that, this
        # gate being false meant `_build_function_instance_map`/
        # `_build_class_instance_map` never ran for these languages at
        # all, so a call through ANY locally-typed variable
        # (`const c = new Circle(); c.area()`) produced no CALLS edge
        # whatsoever, even for a method declared directly on the
        # constructed class with no inheritance involved - a total gap,
        # not merely "the MRO walk doesn't apply to TS."
        instance_binding_langs = (
            LanguageID.PYTHON, LanguageID.JAVA, LanguageID.CSHARP, LanguageID.GO,
            LanguageID.JAVASCRIPT, LanguageID.TYPESCRIPT, LanguageID.TSX,
        )
        for qualified_name in file_symbols:
            symbol = self.symbol_table.get(qualified_name)
            def_node = self._def_nodes[qualified_name]
            class_instance_map = InstanceTypeMap()
            if symbol.enclosing_class and parsed.language_id in instance_binding_langs:
                class_instance_map = self._build_class_instance_map(
                    symbol.enclosing_class, parsed, module, import_map
                )
            func_instance_map = InstanceTypeMap()
            if parsed.language_id in instance_binding_langs:
                func_instance_map = self._build_function_instance_map(def_node, parsed, module, import_map)
            self._resolve_calls_in_function(
                qualified_name, def_node, parsed, module, symbol.enclosing_class,
                import_map, class_instance_map, func_instance_map,
            )

    # -- Import maps ---------------------------------------------------- #
    def _build_import_map(self, parsed: ParsedFile, module: str) -> LocalImportMap:
        import_map = LocalImportMap()
        lang = parsed.language_id
        if lang == LanguageID.PYTHON:
            self._parse_python_imports(parsed, module, import_map)
        elif lang in (LanguageID.JAVASCRIPT, LanguageID.TYPESCRIPT, LanguageID.TSX):
            self._parse_js_imports(parsed, module, import_map)
        elif lang == LanguageID.GO:
            self._parse_go_imports(parsed, import_map)
        elif lang == LanguageID.JAVA:
            self._parse_java_imports(parsed, import_map)
        elif lang == LanguageID.CSHARP:
            self._parse_csharp_imports(parsed, import_map)
        return import_map

    def _parse_python_imports(self, parsed: ParsedFile, module: str, import_map: LocalImportMap) -> None:
        src = parsed.source
        is_package_init = os.path.basename(parsed.path) == "__init__.py"
        for stmt in find_all(parsed.root_node, {"import_statement", "import_from_statement"}):
            if stmt.type == "import_statement":
                for name_node in stmt.children_by_field_name("name"):
                    self._handle_python_import_name(name_node, src, import_map, from_module=None)
            else:
                module_name_node = stmt.child_by_field_name("module_name")
                base_module = (
                    self._python_module_ref_text(module_name_node, src, module, is_package_init)
                    if module_name_node is not None
                    else module
                )
                # `from .x import *` - tree-sitter-python gives this its own
                # distinct `wildcard_import` child, *not* bound to the
                # "name" field the way every other imported name is, so
                # `children_by_field_name("name")` below never sees it at
                # all: previously silently unhandled in its entirety
                # (Issues #6/#7's barrel-file gap), not merely imprecise.
                if any(c.type == "wildcard_import" for c in stmt.children):
                    import_map.add_wildcard(base_module)
                    continue
                for name_node in stmt.children_by_field_name("name"):
                    self._handle_python_import_name(name_node, src, import_map, from_module=base_module)

    def _handle_python_import_name(self, name_node: Node, src: bytes, import_map: LocalImportMap, from_module: str | None) -> None:
        if name_node.type == "aliased_import":
            target_node = name_node.child_by_field_name("name")
            alias_node = name_node.child_by_field_name("alias")
            target_text = node_text(target_node, src)
            alias_text = node_text(alias_node, src)
            qualified = f"{from_module}.{target_text}" if from_module else target_text
            import_map.add(alias_text, qualified)
        else:  # dotted_name
            text = node_text(name_node, src)
            if from_module is None:
                root = text.split(".")[0]
                import_map.add(root, root)
            else:
                import_map.add(text, f"{from_module}.{text}")

    def _python_module_ref_text(self, node: Node, src: bytes, current_module: str, is_package_init: bool = False) -> str:
        if node.type == "relative_import":
            dots = 0
            suffix: str | None = None
            for child in node.children:
                if child.type == "import_prefix":
                    dots = node_text(child, src).count(".")
                elif child.type == "dotted_name":
                    suffix = node_text(child, src)
            return self._resolve_relative_module(current_module, dots, suffix, is_package_init)
        return node_text(node, src)

    @staticmethod
    def _resolve_relative_module(current_module: str, dots: int, suffix: str | None, is_package_init: bool = False) -> str:
        """A single dot (`from . import x` / `from .sibling import y`)
        means "within my own containing package" - which package that is
        depends on whether `current_module` is a *plain* module file
        (`app/order_service.py` -> module `"app.order_service"`, whose
        containing package is `"app"`, found by dropping its own last
        component) or a package's own `__init__.py` (`app/__init__.py` ->
        module `"app"` - already exactly its containing package, nothing
        to drop, since `path_to_module` already strips the `__init__`
        segment). Treating the two the same way (unconditionally dropping
        the last component) previously resolved `app/__init__.py`'s own
        `from .order_service import OrderService` to bare `"order_service"`
        instead of `"app.order_service"` - stripping one package level too
        many - which broke every barrel-file re-export
        (`ExportRegistry`/Issues #6-#7) rooted at a package's own
        `__init__.py`, the single most common place that idiom appears.
        """
        parts = current_module.split(".") if current_module else []
        package_parts = parts if is_package_init else parts[:-1]
        levels_up = max(dots - 1, 0)
        if levels_up:
            package_parts = package_parts[: max(len(package_parts) - levels_up, 0)]
        base = ".".join(package_parts)
        if suffix:
            return f"{base}.{suffix}" if base else suffix
        return base

    # -- Barrel-file / re-export registration (Issues #6/#7) ------------- #
    def _register_exports(self, parsed: ParsedFile, module: str, import_map: LocalImportMap) -> None:
        """Populate `self.export_registry` with everything `module` makes
        available to a *consumer* importing from it - distinct from
        `import_map` itself, which only ever represents what's usable
        *inside* this one file.

        Python has no import/export distinction at all: any name bound at
        module scope - imported or not - is a real attribute of that
        module (`from .order_service import OrderService` inside
        `app/__init__.py` really does make `app.OrderService` valid,
        exactly the barrel-file idiom this exists to resolve), so
        `import_map.aliases` (already exactly that set) is registered
        directly. JS/TS draw a sharp line a plain `import` does *not*
        cross - only an explicit `export { x } from './y'` / `export *
        from './y'` re-export statement makes `x` visible to another
        module through this one - so those get their own, separate scan
        (`_parse_js_reexports`) rather than reusing `import_map.aliases`.
        """
        lang = parsed.language_id
        if lang == LanguageID.PYTHON:
            for local_name, target in import_map.aliases.items():
                if "." not in target:
                    continue
                origin_module, origin_name = target.rsplit(".", 1)
                self.export_registry.add_explicit(module, local_name, origin_module, origin_name)
            for wildcard in import_map.wildcard_targets:
                self.export_registry.add_wildcard(module, wildcard)
            self._parse_python_all(parsed, module)
        elif lang in (LanguageID.JAVASCRIPT, LanguageID.TYPESCRIPT, LanguageID.TSX):
            self._parse_js_reexports(parsed, module)

    def _parse_python_all(self, parsed: ParsedFile, module: str) -> None:
        """`__all__ = [...]`/`(...)` at module scope (Issue #6.3): a
        statically-literal list/tuple of string constants is honored as a
        strict whitelist for `from module import *`; anything else
        (`__all__.extend(...)`, `__all__ += [...]`, a comprehension, a
        name computed at runtime) sets `PARTIAL_EXPORT_MAP` instead,
        falling back to "any public (non-underscore) name" - this pass
        cannot determine the true dynamic whitelist statically, and
        silently treating an unrecognized shape as an *empty* whitelist
        would be strictly worse (it would hide every wildcard-exported
        name, not just the dynamically-computed ones).
        """
        assign_type = ASSIGNMENT_NODE_TYPE.get(LanguageID.PYTHON)
        for assign in self._direct_assignments(parsed.root_node, assign_type):
            target = assign.child_by_field_name("left")
            if target is None or target.type != "identifier" or node_text(target, parsed.source) != "__all__":
                continue
            value = assign.child_by_field_name("right")
            names = self._python_literal_string_list(value, parsed.source)
            if names is not None:
                self.export_registry.set_all_whitelist(module, frozenset(names))
            else:
                self.export_registry.mark_partial_export(module)
            return
        # `__all__.extend(...)` / `__all__ += [...]` - an augmented
        # assignment or a call whose receiver is `__all__`, appearing
        # anywhere at module scope (not necessarily the first binding) -
        # any such mutation after an initial literal assignment makes the
        # final whitelist no longer purely static.
        for node in parsed.root_node.children:
            text = node_text(node, parsed.source)
            if "__all__" in text and ("+=" in text or ".extend(" in text or ".append(" in text):
                self.export_registry.mark_partial_export(module)
                return

    @staticmethod
    def _python_literal_string_list(node: Node | None, source: bytes) -> list[str] | None:
        if node is None or node.type not in ("list", "tuple"):
            return None
        names: list[str] = []
        for child in node.named_children:
            if child.type != "string":
                return None
            text = node_text(child, source)
            # Strip the outer quote characters (tree-sitter-python's
            # `string` node includes them) - a plain single/double-quoted
            # literal only; an f-string or one containing an `escape_sequence`
            # sub-node is conservatively treated as non-literal.
            if any(c.type not in ("string_start", "string_content", "string_end") for c in child.children):
                return None
            inner = "".join(node_text(c, source) for c in child.children if c.type == "string_content")
            names.append(inner)
        return names

    def _parse_js_reexports(self, parsed: ParsedFile, module: str) -> None:
        """`export { x [as y] } from './z'` (named re-export) and
        `export * from './z'` (wildcard re-export) - genuinely distinct
        node shapes from a plain `import_statement`
        (`export_statement` with a `source` field), and entirely
        unhandled before this (Issues #6/#7's TS/JS barrel-file gap).
        `export * as ns from './z'` (a *namespace* re-export, binding a
        single object `ns` rather than re-exporting each name directly) is
        a structurally different idiom - out of scope here, not silently
        folded into the wildcard case it only superficially resembles.
        """
        src = parsed.source
        for stmt in find_all(parsed.root_node, {"export_statement"}):
            source_node = stmt.child_by_field_name("source")
            if source_node is None:
                continue
            specifier = node_text(source_node, src).strip("'\"")
            module_ref = self._resolve_js_specifier(specifier, parsed.path, module)

            export_clause = next((c for c in stmt.children if c.type == "export_clause"), None)
            if export_clause is not None:
                for spec in find_all(export_clause, {"export_specifier"}):
                    name_node = spec.child_by_field_name("name")
                    alias_node = spec.child_by_field_name("alias")
                    if name_node is None:
                        continue
                    origin_name = node_text(name_node, src)
                    exported_name = node_text(alias_node, src) if alias_node is not None else origin_name
                    self.export_registry.add_explicit(module, exported_name, module_ref, origin_name)
                continue

            # A bare `*` child (not wrapped in `namespace_export`, which
            # covers `export * as ns from ...` instead) is the wildcard
            # re-export form.
            if any(c.type == "*" for c in stmt.children):
                self.export_registry.add_wildcard(module, module_ref)

    def _parse_js_imports(self, parsed: ParsedFile, module: str, import_map: LocalImportMap) -> None:
        src = parsed.source
        for stmt in find_all(parsed.root_node, {"import_statement"}):
            source_node = stmt.child_by_field_name("source")
            if source_node is None:
                continue
            specifier = node_text(source_node, src).strip("'\"")
            module_ref = self._resolve_js_specifier(specifier, parsed.path, module)
            for spec in find_all(stmt, {"import_specifier"}):
                name_node = spec.child_by_field_name("name")
                alias_node = spec.child_by_field_name("alias")
                if name_node is None:
                    continue
                imported_name = node_text(name_node, src)
                local_name = node_text(alias_node, src) if alias_node is not None else imported_name
                import_map.add(local_name, f"{module_ref}.{imported_name}")
            for ns in find_all(stmt, {"namespace_import"}):
                alias_node = None
                for c in ns.children:
                    if c.type == "identifier":
                        alias_node = c
                if alias_node is not None:
                    import_map.add(node_text(alias_node, src), module_ref)
            for c in stmt.children:
                if c.type == "import_clause":
                    for cc in c.children:
                        if cc.type == "identifier":
                            import_map.add(node_text(cc, src), f"{module_ref}.default")

    def _resolve_js_specifier(self, specifier: str, file_path: str, current_module: str) -> str:
        if specifier.startswith("."):
            file_dir = os.path.dirname(file_path)
            joined = os.path.normpath(os.path.join(file_dir, specifier))
            return path_to_module(joined, self.repo_root)
        return specifier.replace("/", ".")

    def _parse_go_imports(self, parsed: ParsedFile, import_map: LocalImportMap) -> None:
        src = parsed.source
        for spec in find_all(parsed.root_node, {"import_spec"}):
            path_node = None
            alias_node = spec.child_by_field_name("name")
            for c in spec.children:
                if c.type == "interpreted_string_literal":
                    path_node = c
            if path_node is None:
                continue
            import_path = node_text(path_node, src).strip('"')
            default_alias = import_path.rsplit("/", 1)[-1]
            alias = node_text(alias_node, src) if alias_node is not None else default_alias
            import_map.add(alias, import_path.replace("/", "."))

    def _parse_java_imports(self, parsed: ParsedFile, import_map: LocalImportMap) -> None:
        """`import com.example.models.User;` binds the simple name `User`
        directly (Rule B); `import com.example.models.*;` brings the whole
        package into scope as a wildcard fallback (Rule D-adjacent) - see
        `_resolve_reference_chain`. `import static ...` is left unresolved
        (a static-member import binds a *member* name, not a class one -
        out of scope for this pass, which only ever binds class-shaped
        names).
        """
        src = parsed.source
        for stmt in find_all(parsed.root_node, {"import_declaration"}):
            if any(c.type == "static" for c in stmt.children):
                continue
            is_wildcard = any(c.type == "asterisk" for c in stmt.children)
            scoped = next((c for c in stmt.children if c.type in ("scoped_identifier", "identifier")), None)
            if scoped is None:
                continue
            text = node_text(scoped, src)
            if is_wildcard:
                import_map.add_wildcard(text)
            else:
                import_map.add(text.rsplit(".", 1)[-1], text)

    def _parse_csharp_imports(self, parsed: ParsedFile, import_map: LocalImportMap) -> None:
        """Every `using App.Models;` imports the whole namespace's members
        into scope - C# has no separate per-class `import`/wildcard
        distinction the way Java does, so this always adds a wildcard
        target, never a single bound name. `using X = Y;` (an alias
        directive) and `using static X;` are left unresolved.
        """
        src = parsed.source
        for stmt in find_all(parsed.root_node, {"using_directive"}):
            if any(c.type in ("=", "static") for c in stmt.children):
                continue
            target = next((c for c in stmt.children if c.type in ("qualified_name", "identifier")), None)
            if target is None:
                continue
            import_map.add_wildcard(node_text(target, src))

    # -- Instance bindings (Python only) --------------------------------- #
    def _resolve_reference_chain(self, segments: list[str] | None, module: str, import_map: LocalImportMap) -> str | None:
        if not segments:
            return None
        root, *rest = segments
        from_import = import_map.resolve(root)
        resolved_root = from_import
        if resolved_root is None:
            # Rule D: a bare name defined in the caller's own module - for
            # Java/C#, `module` is the file's declared package/namespace
            # (see `_module_for_file`), not a per-file path, so this alone
            # already covers same-package/namespace implicit visibility
            # (every file in the package shares the same `module` string).
            resolved_root = self.symbol_table.resolve_in_module(module, root)
        if resolved_root is None:
            # Java `import pkg.*;` / any C# `using Namespace;` - try each
            # wildcard-imported package/namespace as its own same-module
            # lookup, in declared order.
            for wildcard in import_map.wildcard_targets:
                resolved_root = self.symbol_table.resolve_in_module(wildcard, root)
                if resolved_root is not None:
                    break
        if resolved_root is None:
            return None
        target = ".".join([resolved_root, *rest]) if rest else resolved_root

        # Barrel-file / re-export fallback (Issues #6/#7): `root` was
        # imported (`from_import` is not None), but the naive
        # `<from_module>.<imported_name>` target this import statement's
        # own text implies isn't a symbol Pass 1 actually registered -
        # most likely because `root` is really defined somewhere *else*
        # and merely re-exported from `from_import`'s module through a
        # barrel/index file. Chase the real origin through the export
        # registry rather than returning (or falling through to) a
        # phantom target.
        if from_import is not None and target not in self.symbol_table and "." in from_import:
            origin_module, origin_name = from_import.rsplit(".", 1)
            resolved = resolve_export(origin_module, origin_name, self.export_registry, self.symbol_table)
            if resolved is not None:
                return ".".join([resolved, *rest]) if rest else resolved

        # Phase I: a second, structurally distinct barrel case the fix
        # above doesn't cover - `root` resolved correctly as a *package*
        # (`from django import forms` -> `resolved_root = "django.forms"`,
        # a real module, not a re-exported symbol), but the attribute
        # accessed on it (`forms.DateField`) is only reachable through
        # that package's own `__init__.py` re-exporting it (`from
        # django.forms.fields import *`) - `DateField`'s real qualified
        # name is `django.forms.fields.DateField`, never `django.forms.
        # DateField` itself. Confirmed as a real, repo-wide gap: this
        # exact shape (`<package>.<ReExportedClass>()`) is Django's own
        # single most common way of constructing a forms.Field subclass,
        # and every such call site previously left its receiver variable
        # completely unbound (the instantiation target itself was never
        # found in `symbol_table`), so downstream method calls on it
        # (`f.clean(...)`) had no known type to resolve against at all.
        # `resolved_root` is itself the "module" `resolve_export` needs
        # here - a different call shape from the block above (which
        # checks whether `from_import`'s own *containing* module
        # re-exports `from_import`'s trailing segment as a symbol), not
        # a broadening of it.
        if rest and target not in self.symbol_table:
            resolved_attr = resolve_export(resolved_root, rest[0], self.export_registry, self.symbol_table)
            if resolved_attr is not None:
                return ".".join([resolved_attr, *rest[1:]]) if len(rest) > 1 else resolved_attr

        return target

    def _build_class_instance_map(
        self, enclosing_class: str, parsed: ParsedFile, module: str, import_map: LocalImportMap
    ) -> InstanceTypeMap:
        instance_map = InstanceTypeMap()
        assign_type = ASSIGNMENT_NODE_TYPE.get(parsed.language_id)
        if assign_type is None:
            return instance_map
        self_tokens = SELF_TOKEN_TEXT[parsed.language_id]
        for method_qname in self._methods_by_class.get(enclosing_class, []):
            method_node = self._def_nodes.get(method_qname)
            if method_node is None:
                continue
            for assign in iter_scoped_nodes(method_node, {assign_type}, parsed.language_id):
                target = assign.child_by_field_name("left")
                if target is None:
                    continue
                target_segments = flatten_reference_chain(target, parsed.source, parsed.language_id)
                if not target_segments or len(target_segments) != 2 or target_segments[0] not in self_tokens:
                    continue
                value = assign.child_by_field_name("right")
                if value is not None:
                    ctor_segments = _constructor_call_segments(value, parsed.language_id, parsed.source)
                    if ctor_segments is None:
                        if _is_builtin_container_expr(value, parsed.language_id, parsed.source):
                            instance_map.bind_builtin(".".join(target_segments))
                        continue
                    resolved_class = self._resolve_reference_chain(ctor_segments, module, import_map)
                    if not self._is_known_class(resolved_class) and _is_builtin_container_expr(
                        value, parsed.language_id, parsed.source
                    ):
                        instance_map.bind_builtin(".".join(target_segments))
                        continue
                else:
                    # Phase B (G41): a bare type-annotated attribute with
                    # no constructor call at all (`self.attr:
                    # ServiceClient`, tree-sitter-python's "assignment"
                    # node with a "type" field but no "right" child) -
                    # the annotation alone is enough to bind the
                    # receiver's type when it names a single, simple
                    # class directly. A subscripted/generic annotation
                    # (`Optional[ServiceClient]`, `list[ServiceClient]`)
                    # is not attempted - its own "type" child is not a
                    # bare `identifier`, so it falls through and is left
                    # unresolved rather than guessed.
                    # tree-sitter-python wraps the annotation in its own
                    # "type" grammar node (`type_node.type == "type"`),
                    # one level above the actual `identifier` - confirmed
                    # directly against the parse tree, not assumed.
                    type_node = assign.child_by_field_name("type")
                    if type_node is not None and len(type_node.named_children) == 1:
                        type_node = type_node.named_children[0]
                    if type_node is None or type_node.type != "identifier":
                        continue
                    resolved_class = self._resolve_reference_chain(
                        [node_text(type_node, parsed.source)], module, import_map
                    )
                if self._is_known_class(resolved_class):
                    instance_map.bind(".".join(target_segments), resolved_class)
        return instance_map

    def _is_known_class(self, qualified_name: str | None) -> bool:
        if not qualified_name:
            return False
        symbol = self.symbol_table.get(qualified_name)
        return symbol is not None and symbol.kind == "class"

    def _is_known_type(self, qualified_name: str | None) -> bool:
        """Item 17: like `_is_known_class`, but also accepts
        `kind="interface"` - used specifically by `_link_ts_heritage`'s
        target-existence check, since both a class's `extends`/
        `implements` clause and an interface's own `extends` clause can
        legitimately name either a class or another interface. Kept
        separate from `_is_known_class` (not widened in place) so every
        other, non-TS-heritage caller of that check keeps its existing,
        narrower "class" semantics unchanged.
        """
        if not qualified_name:
            return False
        symbol = self.symbol_table.get(qualified_name)
        return symbol is not None and symbol.kind in ("class", "interface")

    def _resolve_go_type_name(self, local_module: str, type_name: str) -> str | None:
        """Item 5 follow-through (second post-implementation audit):
        resolves a bare Go type name (a struct-embedding field, a
        parameter type, a `:=` composite-literal type) to its real
        qualified class - trying the same-file-derived module first
        (`f"{local_module}.{type_name}"`, cheap and unambiguous when it
        hits), then falling back to a repo-wide search by simple name.

        The fallback exists because of a real, structural fact about how
        this builder derives Go "modules": `_module_for_file` names a Go
        module after the *file's own path* (`path_to_module`), not its
        `package` declaration - correct for Python's real per-file
        module semantics, but Go packages routinely span many files in
        one directory (`gin.go` and `routergroup.go` are both `package
        gin`, yet register as modules `"gin"` and `"routergroup"`
        respectively - confirmed directly: real gin-gonic/gin's own
        `Engine` embeds `RouterGroup` from a different file in the same
        package, and the naive same-module lookup alone never finds it).
        Reworking Go's module derivation to be package-based instead
        (matching Java/C#'s already-different treatment) would be a much
        larger, riskier change than this fallback - out of scope here.

        Like `_go_unique_receiver_for_method`, only ever returns a
        result when the repo-wide search finds *exactly one* same-named
        Go class - a genuine cross-package name collision (rare, but
        real) is left unresolved rather than guessed.
        """
        same_module = f"{local_module}.{type_name}"
        if self._is_known_class(same_module):
            return same_module
        registry = self._go_class_registry()
        candidates = registry.get(type_name)
        if candidates is not None and len(candidates) == 1:
            return candidates[0]
        return None

    def _go_class_registry(self) -> dict[str, list[str]]:
        """Simple Go struct/class name -> every qualified class of that
        name anywhere in the repo - the type-level counterpart to
        `_go_method_registry`, backing `_resolve_go_type_name`'s
        cross-file fallback. Built once, lazily, and cached - Item 14:
        double-checked against `_lazy_cache_lock`, same as `calls_graph`.
        """
        if self._go_class_registry_cache is None:
            with self._lazy_cache_lock:
                if self._go_class_registry_cache is None:
                    registry: dict[str, list[str]] = {}
                    for symbol in self.symbol_table:
                        if symbol.kind == "class" and symbol.language_id == LanguageID.GO:
                            simple_name = symbol.qualified_name.rsplit(".", 1)[-1]
                            registry.setdefault(simple_name, []).append(symbol.qualified_name)
                    self._go_class_registry_cache = registry
        return self._go_class_registry_cache

    # -- EXTENDS / IMPLEMENTS ---------------------------------------------- #
    def _link_class_relations(self, class_qnames: list[str], parsed: ParsedFile, module: str, import_map: LocalImportMap) -> None:
        """`class Foo(Base): ...` / `class Foo extends Base implements
        I: ...` - real, verified for Python (a `superclasses` field holding
        an `argument_list`, `metaclass=`-shaped keyword arguments filtered
        out) and JS/TS (a `class_heritage` node holding separate
        `extends_clause`/`implements_clause` children - JS's grammar never
        produces an `implements_clause` at all, so that half degrades
        cleanly to "no interfaces" there rather than needing its own
        branch). Not implemented for Go (which has no EXTENDS/IMPLEMENTS
        syntax at all - see `_link_go_embeds` for its real mechanism,
        struct embedding, a distinct relation: `EMBEDS`, Item 5) or
        Java/C# - out of scope for this pass, not silently claimed.
        """
        lang = parsed.language_id
        if lang not in (LanguageID.PYTHON, LanguageID.JAVASCRIPT, LanguageID.TYPESCRIPT, LanguageID.TSX):
            return
        for class_qname in class_qnames:
            class_node = self._def_nodes.get(class_qname)
            if class_node is None:
                continue
            if lang == LanguageID.PYTHON:
                self._link_python_bases(class_qname, class_node, parsed, module, import_map)
            else:
                self._link_ts_heritage(class_qname, class_node, parsed, module, import_map)

    def _link_go_embeds(self, class_qnames: list[str], parsed: ParsedFile, module: str) -> None:
        """Item 5 (second post-implementation audit): Go achieves
        composition/"inheritance" via anonymous struct embedding
        (`type Engine struct { RouterGroup; ... }`), not EXTENDS/
        IMPLEMENTS syntax - a genuinely different relation
        (`EMBEDS`), same structural edge weight as EXTENDS
        (`RELATION_STRUCTURAL_WEIGHT["EMBEDS"] = 0.85` in
        `prism.slicer.distance`) since both represent the same kind of
        "this node's own methods aren't the whole story" traversal need.
        An embedded field is a `field_declaration` with no `name` field
        (Go's grammar - a named field always has one, an anonymous/
        embedded one never does) whose `type` field is a bare
        `type_identifier` (same-package - `pkg.Other`-shaped cross-
        package embeds are out of scope, same boundary every other
        same-package-only Go resolution in this class already has) or a
        pointer to one (`*Pool` - `type` already unwraps this in Go's
        grammar, unlike a parameter's `pointer_type` wrapper, confirmed
        directly).
        """
        if parsed.language_id != LanguageID.GO:
            return
        for class_qname in class_qnames:
            type_spec = self._def_nodes.get(class_qname)
            if type_spec is None:
                continue
            struct_type = type_spec.child_by_field_name("type")
            if struct_type is None or struct_type.type != "struct_type":
                continue
            # `field_declaration_list` has no field name of its own on
            # `struct_type` (confirmed directly - just a positional
            # child, unlike e.g. `type_spec`'s own "type"/"name" fields).
            field_list = next((c for c in struct_type.children if c.type == "field_declaration_list"), None)
            if field_list is None:
                continue
            for field_decl in field_list.named_children:
                if field_decl.type != "field_declaration" or field_decl.child_by_field_name("name") is not None:
                    continue
                embedded_type = _go_type_identifier_text(field_decl.child_by_field_name("type"), parsed.source)
                if embedded_type is None:
                    continue
                target = self._resolve_go_type_name(module, embedded_type)
                if target is not None:
                    self._add_relation_edge(class_qname, target, "EMBEDS")

    def _link_python_bases(self, class_qname: str, class_node: Node, parsed: ParsedFile, module: str, import_map: LocalImportMap) -> None:
        superclasses = class_node.child_by_field_name("superclasses")
        if superclasses is None:
            return
        for child in superclasses.named_children:
            if child.type == "keyword_argument":  # `metaclass=Meta` - not a base class
                continue
            segments = flatten_reference_chain(child, parsed.source, LanguageID.PYTHON)
            if not segments:
                continue
            target = self._resolve_reference_chain(segments, module, import_map)
            if self._is_known_class(target):
                self._add_relation_edge(class_qname, target, "EXTENDS")

    def _link_ts_heritage(self, class_qname: str, class_node: Node, parsed: ParsedFile, module: str, import_map: LocalImportMap) -> None:
        # Item 17 (second post-implementation audit): a TS `interface`'s
        # own `extends` is a structurally different grammar shape from a
        # class's (`extends_type_clause`, not `class_heritage` ->
        # `extends_clause`/`implements_clause` - confirmed directly; a
        # class's own `implements OrderService` still goes through the
        # ordinary `class_heritage` path below unchanged, since it's the
        # implementing *class* being processed there, not the interface).
        if class_node.type == "interface_declaration":
            extends_clause = next((c for c in class_node.children if c.type == "extends_type_clause"), None)
            if extends_clause is None:
                return
            for type_node in extends_clause.named_children:
                self._link_ts_heritage_target(class_qname, type_node, parsed, module, import_map, "EXTENDS")
            return

        heritage = next((c for c in class_node.children if c.type == "class_heritage"), None)
        if heritage is None:
            return
        for clause in heritage.children:
            if clause.type == "extends_clause":
                relation = "EXTENDS"
            elif clause.type == "implements_clause":
                relation = "IMPLEMENTS"
            else:
                continue
            for name_node in clause.named_children:
                self._link_ts_heritage_target(class_qname, name_node, parsed, module, import_map, relation)

    def _link_ts_heritage_target(
        self, class_qname: str, type_node: Node, parsed: ParsedFile, module: str, import_map: LocalImportMap, relation: str
    ) -> None:
        # A generic type reference (`BaseService<Order>`) names its own
        # base type via a "name" field - flatten_reference_chain's
        # identifier/attribute-chain walk doesn't recognize
        # `generic_type` itself, so unwrap it first; a plain
        # (non-generic) reference is passed through unchanged.
        if type_node.type == "generic_type":
            name_node = type_node.child_by_field_name("name")
            if name_node is None:
                return
            type_node = name_node
        segments = flatten_reference_chain(type_node, parsed.source, parsed.language_id)
        if not segments:
            return
        target = self._resolve_reference_chain(segments, module, import_map)
        if self._is_known_type(target):
            self._add_relation_edge(class_qname, target, relation)

    def _add_relation_edge(self, source: str, target: str, relation: str) -> None:
        if target not in self.graph:
            self.graph.add_node(target, external=False)
        self.graph.add_edge(source, target, relation=relation)

    # -- OVERRIDES + Python C3-ish MRO (Issue #9) ------------------------- #
    def _mro_ancestors(self, class_qname: str) -> list[str]:
        """`class_qname`'s own ancestor chain via its `EXTENDS` edges,
        nearest-first, depth-first, each visited at most once - a
        practical approximation of Python's real C3 linearization: exact
        for single inheritance and for ordinary (non-diamond) multiple
        inheritance, which is the overwhelming majority of real code; a
        genuine diamond (`class D(B, C)` where both `B` and `C` extend
        `A`) may order differently from true C3's consistency-corrected
        linearization - out of scope for this pass, not silently claimed
        as exact. `A` itself is still yielded exactly once either way
        (from whichever of `B`/`C` reaches it first in canonical sibling
        order below) - `seen` already guarantees that; a true diamond
        does not require multiple visits to resolve correctly, only a
        genuine cycle (`A` extends `B` extends `A`) needs the recursion
        to stop, which the same `seen` set also already does (a cyclic
        `EXTENDS` graph can't happen in real Python source, but a
        synthetic/test graph built by hand can construct one).

        Phase B (G40) determinism: sibling bases at each level are
        expanded in canonical `(file, line, qualified_name)` order rather
        than raw graph edge-insertion order, so the same source always
        produces the same traversal order regardless of how EXTENDS edges
        happened to be added to the graph or in what order files were
        scanned.
        """
        ordered: list[str] = []
        seen: set[str] = {class_qname}
        # Issue C2 (security audit): `seen` already guarantees termination
        # (bounded by the repo's total class count) against a cyclic
        # EXTENDS graph, but not against a stack overflow from a
        # genuinely very long *linear* chain (`class C600(C599): ...`
        # 600 levels deep) - confirmed this class of recursive walk can
        # raise a real `RecursionError` elsewhere in this module
        # (`iter_scoped_nodes`, `src/prism/parser/lang_config.py`) well
        # before Python's default stack limit in a real call-stack
        # context.
        #
        # Phase B (G40): the cap is now `MAX_INHERITANCE_DEPTH` (10), not
        # the original 300 - a deliberate narrowing, not just a tighter
        # anti-crash margin. `_link_overrides`/self.method() resolution
        # (this method's only two callers) will no longer resolve an
        # override or an inherited method past 10 EXTENDS hops from the
        # starting class, where they previously would have up to 300. A
        # single-inheritance hierarchy genuinely 11+ levels deep (rare,
        # but not impossible in real frameworks) silently stops
        # resolving beyond level 10 - see the commit introducing this
        # change for the concrete trade-off being made.
        max_depth = MAX_INHERITANCE_DEPTH

        def _sibling_sort_key(qname: str) -> tuple[str, int, str]:
            info = self.symbol_table.get(qname)
            if info is None:
                return ("", 0, qname)
            return (info.file, info.line_range[0], qname)

        def visit(node: str, depth: int) -> None:
            if depth >= max_depth:
                return
            siblings = {
                target
                for _source, target, data in self.graph.out_edges(node, data=True)
                if data.get("relation") == "EXTENDS"
            }
            for target in sorted(siblings, key=_sibling_sort_key):
                if target in seen:
                    continue
                seen.add(target)
                ordered.append(target)
                visit(target, depth + 1)

        visit(class_qname, 0)
        return ordered

    # -- Python `super()` call resolution (Phase I) ------------------------ #
    #
    # `_is_super_call_node`/`_super_call_method_name` themselves now live
    # in `prism.parser.lang_config` (`is_super_call_node`/`super_call_
    # method_name`) - `prism.traversal._data_flow_common._resolve_call_
    # sites` needed the exact same detection (it independently re-derives
    # call-site resolution from `call_callee_segments` for provenance
    # purposes, rather than trusting this module's own graph edges) and
    # can't import it from here (this module already imports from
    # `lang_config`, not the reverse). This class only keeps the part
    # that's genuinely its own: resolving the method name against a real
    # enclosing class's MRO, which only a `ConcreteGraphBuilder` (holding
    # the whole repo's `symbol_table`/`EXTENDS` graph) can do.

    def _resolve_super_method(self, enclosing_class: str | None, method_name: str) -> str | None:
        """`super().<method_name>(...)`'s real target: the nearest MRO
        ancestor of `enclosing_class` that actually defines `method_
        name` - the same "nearest ancestor with a same-named method"
        lookup `_link_overrides` already uses to build `OVERRIDES`
        edges, reapplied here to resolve a real `CALLS` edge instead.
        `None` if there's no enclosing class (a `super()` call outside
        any method is not valid Python, but this pass doesn't assume
        the source it's given is error-free) or no ancestor defines it.
        """
        if enclosing_class is None:
            return None
        for ancestor in self._mro_ancestors(enclosing_class):
            candidate = f"{ancestor}.{method_name}"
            if candidate in self.symbol_table:
                return candidate
        return None

    def _link_overrides(self) -> None:
        """For every class with at least one `EXTENDS` ancestor, an
        `OVERRIDES` edge from each of its own directly-declared methods to
        the *nearest* MRO ancestor that also defines a method of the same
        simple name (Issue #9) - the same shadowing semantics Python's
        real attribute lookup uses, computed once the whole repo's
        `EXTENDS`/`IMPLEMENTS` graph is fully built (see the sub-pass
        ordering note in `pass2_resolve_calls`).
        """
        for class_qname, method_qnames in list(self._methods_by_class.items()):
            ancestors = self._mro_ancestors(class_qname)
            if not ancestors:
                continue
            for method_qname in method_qnames:
                simple_name = method_qname.rsplit(".", 1)[-1]
                for ancestor in ancestors:
                    base_method = f"{ancestor}.{simple_name}"
                    if base_method in self.symbol_table:
                        self._add_relation_edge(method_qname, base_method, "OVERRIDES")
                        break

    def _build_function_instance_map(
        self, def_node: Node, parsed: ParsedFile, module: str, import_map: LocalImportMap
    ) -> InstanceTypeMap:
        instance_map = InstanceTypeMap()
        lang = parsed.language_id
        assign_type = ASSIGNMENT_NODE_TYPE.get(lang)
        if assign_type is not None:
            for assign in iter_scoped_nodes(def_node, {assign_type}, lang):
                target = assign.child_by_field_name("left")
                value = assign.child_by_field_name("right")
                if target is None or value is None or target.type != "identifier":
                    continue
                ctor_segments = _constructor_call_segments(value, lang, parsed.source)
                if ctor_segments is None:
                    if _is_builtin_container_expr(value, lang, parsed.source):
                        instance_map.bind_builtin(node_text(target, parsed.source))
                    continue
                resolved_class = self._resolve_reference_chain(ctor_segments, module, import_map)
                if self._is_known_class(resolved_class):
                    instance_map.bind(node_text(target, parsed.source), resolved_class)
                elif _is_builtin_container_expr(value, lang, parsed.source):
                    instance_map.bind_builtin(node_text(target, parsed.source))

        # Java/C# construct almost exclusively through a *typed local
        # variable declaration* (`OrderValidator v = new OrderValidator();`),
        # not a bare reassignment - a structurally different node from
        # `assignment_expression` above (no untyped "declare a new local"
        # form exists in either language), so it needs its own scan.
        declarator_type = _LOCAL_VAR_DECLARATOR_TYPE.get(lang)
        if declarator_type is not None:
            for declarator in iter_scoped_nodes(def_node, {declarator_type}, lang):
                name_node = declarator.child_by_field_name("name")
                value = _declarator_value_node(declarator)
                if name_node is None or value is None:
                    continue
                ctor_segments = _constructor_call_segments(value, lang, parsed.source)
                if ctor_segments is None:
                    continue
                resolved_class = self._resolve_reference_chain(ctor_segments, module, import_map)
                if self._is_known_class(resolved_class):
                    instance_map.bind(node_text(name_node, parsed.source), resolved_class)

        if lang == LanguageID.GO:
            self._bind_go_typed_parameters(def_node, parsed, module, instance_map)
            self._bind_go_short_var_declarations(def_node, parsed, module, instance_map)
        return instance_map

    def _bind_go_short_var_declarations(
        self, def_node: Node, parsed: ParsedFile, module: str, instance_map: InstanceTypeMap
    ) -> None:
        """Item 3 (second post-implementation audit) Stage 1: Go's
        idiomatic local-variable construction is a short variable
        declaration (`r := &Router{}`, `c := Context{}`), not a bare call
        assignment the way Python/JS's `x = Foo()` is - `gin.Default()`-
        shaped constructor-*function* calls are deliberately NOT resolved
        here (their return type isn't visible at the call site without
        real Go type inference, which this textual/CST-based linker does
        not do), only the two composite-literal shapes a Go value/pointer
        struct literal actually parses as. `e, f := Context{}, 1` (a
        multi-value declaration) binds each left identifier to its
        positionally-corresponding right expression independently.
        """
        for decl in iter_scoped_nodes(def_node, {"short_var_declaration"}, LanguageID.GO):
            left = decl.child_by_field_name("left")
            right = decl.child_by_field_name("right")
            if left is None or right is None:
                continue
            names = [c for c in left.named_children if c.type == "identifier"]
            values = list(right.named_children)
            for name_node, value_node in zip(names, values):
                type_name = _go_composite_literal_type(value_node, parsed.source)
                if type_name is None:
                    continue
                resolved_class = self._resolve_go_type_name(module, type_name)
                if resolved_class is not None:
                    instance_map.bind(node_text(name_node, parsed.source), resolved_class)

    def _bind_go_typed_parameters(
        self, def_node: Node, parsed: ParsedFile, module: str, instance_map: InstanceTypeMap
    ) -> None:
        """Issue B1 follow-through: receiver-qualified method registration
        alone doesn't make an ordinary Go method call resolve - `c.JSON(...)`
        still needs to know `c`'s type. Go has no constructor-call-based
        local-binding idiom (`x = NewFoo()`) to reuse the two blocks above
        for; a receiver/parameter's type is declared directly in the
        signature instead (`func (c *Context) JSON(...)`, `func
        handler(c *Context)`), so both the `receiver` and `parameters`
        fields of `def_node` (a `method_declaration` has both; a plain
        `function_declaration` has only `parameters`) are scanned
        directly here. Same-package bare types only (`_go_type_identifier_
        text` returns `None` for an imported `pkg.Type`, a generic, or a
        slice/map/interface type) - out of scope, not silently guessed.
        """
        param_lists = [
            p for p in (def_node.child_by_field_name("receiver"), def_node.child_by_field_name("parameters"))
            if p is not None
        ]
        for param_list in param_lists:
            for param_decl in param_list.named_children:
                if param_decl.type != "parameter_declaration":
                    continue
                type_name = _go_type_identifier_text(param_decl.child_by_field_name("type"), parsed.source)
                if type_name is None:
                    continue
                resolved_class = self._resolve_go_type_name(module, type_name)
                if resolved_class is None:
                    continue
                # A `parameter_declaration` can name more than one
                # parameter sharing a single trailing type (`a, b
                # *Context`) - every `identifier` child preceding the
                # `type` field is a bound name, not just the one field
                # tree-sitter's grammar happens to expose via `name`.
                for child in param_decl.children:
                    if child.type == "identifier":
                        instance_map.bind(node_text(child, parsed.source), resolved_class)

    # -- Call resolution -------------------------------------------------- #
    def _resolve_calls_in_function(
        self,
        caller_qname: str,
        def_node: Node,
        parsed: ParsedFile,
        module: str,
        enclosing_class: str | None,
        import_map: LocalImportMap,
        class_instance_map: InstanceTypeMap,
        func_instance_map: InstanceTypeMap,
    ) -> None:
        lang = parsed.language_id
        call_type = CALL_NODE_TYPE[lang]
        self_tokens = SELF_TOKEN_TEXT[lang]
        for call_node in iter_scoped_nodes(def_node, {call_type}, lang):
            if lang == LanguageID.GO:
                generic_target = self._resolve_go_generic_call(call_node, module, import_map, parsed.source)
                if generic_target is not None:
                    if generic_target not in self.graph:
                        self.graph.add_node(generic_target, external=False)
                    edge_kwargs = {"relation": "CALLS"}
                    edge_kwargs.update(compute_call_site_context(call_node, def_node, lang, parsed.source).to_dict())
                    self.graph.add_edge(caller_qname, generic_target, **edge_kwargs)
                    continue
            hazard = detect_call_site_hazard(call_node, lang, parsed.source, parsed.path)
            if hazard is not None:
                self._emit_dynamic_edge_sentinel(caller_qname, hazard)
                continue
            # Phase I: Python `super()` needs its own handling on both
            # sides of this pair before falling into the ordinary
            # segments-based path below. `super(...)` itself (the inner
            # call in `super().clean(value)`, but also visited here in
            # its own right by `iter_scoped_nodes`) is never an
            # ordinary resolvable call site - suppressed outright so it
            # can never fall through to the G44 bare-name fallback and
            # bind to an unrelated same-named symbol elsewhere in the
            # repo (confirmed as a real, repo-wide bug: every `super()`
            # call was resolving to `django.template.loader_tags.
            # BlockNode.super`, a real but wholly unrelated Django
            # symbol that happens to share the bare name "super").
            # `super().<method>(...)` (an attribute call whose object
            # is that inner call) is resolved separately here, against
            # the enclosing class's own MRO - `flatten_reference_chain`
            # always returns `None` for it otherwise (its root is a
            # call, "a dynamic root" by that function's own docstring),
            # so without this the real target was simply never captured
            # at all.
            if is_super_call_node(call_node, lang, parsed.source):
                continue
            super_method_name = super_call_method_name(call_node, parsed.source, lang)
            if super_method_name is not None:
                super_target = self._resolve_super_method(enclosing_class, super_method_name)
                if super_target is not None:
                    if super_target not in self.graph:
                        self.graph.add_node(super_target, external=super_target not in self.symbol_table)
                    edge_kwargs = {"relation": "CALLS"}
                    edge_kwargs.update(compute_call_site_context(call_node, def_node, lang, parsed.source).to_dict())
                    self.graph.add_edge(caller_qname, super_target, **edge_kwargs)
                continue
            segments = call_callee_segments(call_node, parsed.source, lang)
            if not segments:
                continue
            # Item 3: go_call_resolution_ratio's denominator - a Go call
            # is "receiver-shaped" (as opposed to a bare function call or
            # a package-qualified one like `gin.Default()`) when it has
            # >= 2 segments and its base identifier isn't a known import
            # alias, the same test `_resolve_segments`' own Stage 2
            # fallback uses.
            if lang == LanguageID.GO and len(segments) >= 2 and import_map.resolve(segments[0]) is None:
                self._go_receiver_call_sites_total += 1
            target = self._resolve_segments(
                segments, module, enclosing_class, import_map, class_instance_map, func_instance_map, self_tokens, lang
            )
            if target is None:
                # Builtin-Receiver Exclusion fix: a receiver definitively
                # known to be a Python builtin container/primitive is not
                # "unresolved" in the sense the G44 bare-name/polysemy
                # fallback exists for - there is no real target to guess
                # at, and guessing was exactly the bug (a call like
                # `field_names.add(...)` binding to an unrelated same-
                # named method elsewhere in the repo). Suppressed the
                # same way the `super()` call-site is suppressed just
                # above, for the same "never let this reach G44" reason.
                if not self._last_resolution_was_builtin_receiver:
                    self._resolve_ambiguous_call(caller_qname, call_node, parsed, module, import_map, segments)
                continue
            if lang == LanguageID.GO and len(segments) >= 2 and import_map.resolve(segments[0]) is None:
                self._go_receiver_call_sites_resolved += 1
            if target not in self.graph:
                self.graph.add_node(target, external=target not in self.symbol_table)
            target_symbol = self.symbol_table.get(target)
            # A bare `Foo()` call resolving to a known *class* is
            # construction, not behavioral delegation (Python's and JS's
            # shared "call the class to build an instance" idiom - the same
            # distinction `_constructor_call_segments`/`_is_known_class`
            # already draw for instance binding, reused here as an edge
            # relation instead of a lookup).
            relation = "INSTANTIATES" if target_symbol is not None and target_symbol.kind == "class" else "CALLS"
            edge_kwargs = {"relation": relation}
            if relation == "CALLS":
                edge_kwargs.update(compute_call_site_context(call_node, def_node, lang, parsed.source).to_dict())
                # Item 3 Stage 2: a call resolved only via the Go
                # codebase-unique-receiver fallback is a best-effort
                # guess, not a confidently-linked call - marked so
                # `prism.slicer.distance` prices the hop more expensively
                # (never as cheap as a normal CALLS edge).
                if self._last_resolution_was_tentative:
                    edge_kwargs["kind"] = "TENTATIVE_CALL"
                # Phase B (G41): an attribute-chain call resolved through
                # a receiver bound to more than one concrete class - same
                # reduced-confidence marker as the Go tentative-receiver
                # guess above (see _resolve_segments's own comment for
                # why this reuses TENTATIVE_CALL rather than a separate,
                # unwired kind). The two conditions are mutually
                # exclusive in practice (one is Go-only, this one only
                # fires through class_instance_map/func_instance_map,
                # which the Go path never populates) but `elif` makes
                # that non-overlap explicit rather than relying on it.
                elif self._last_resolution_was_ambiguous:
                    edge_kwargs["kind"] = "TENTATIVE_CALL"
            self.graph.add_edge(caller_qname, target, **edge_kwargs)

        self._link_new_expression_instantiations(caller_qname, def_node, parsed, module, import_map)
        if lang == LanguageID.PYTHON:
            self._link_state_reads(caller_qname, def_node, parsed, module, enclosing_class, self_tokens)

    # -- Go generic instantiation vs. real subscript dispatch (Task 2) ---- #
    def _resolve_go_generic_call(
        self, call_node: Node, module: str, import_map: LocalImportMap, source: bytes
    ) -> str | None:
        """Go's grammar produces the exact same `index_expression` shape for
        a generic function instantiation (`getTyped[string](c, key)`) as it
        does for a real map/dispatch-table index-then-call
        (`handlers[action]()`) - confirmed directly against tree-sitter-go:
        both the callable base (`getTyped`/`handlers`) and the bracketed
        argument (`string`/`action`) parse as plain `identifier` nodes
        either way, with no distinguishing node type. The one signal this
        no-type-inference pass *can* use is whether the base identifier
        itself resolves to a real, known function - a genuine dispatch
        table's base is a local variable/map, never something the symbol
        table indexes as a callable definition. Returns the resolved
        target for a genuine generic instantiation (so it gets a normal
        `CALLS` edge, same as any other resolved call), or `None` -
        meaning "not a generic call; try the dynamic-hazard path instead".
        """
        func_field = call_node.child_by_field_name("function")
        if func_field is None or func_field.type != "index_expression":
            return None
        base = func_field.child_by_field_name("operand")
        if base is None or base.type != "identifier":
            return None
        target = self._resolve_reference_chain([node_text(base, source)], module, import_map)
        if target is None:
            return None
        symbol = self.symbol_table.get(target)
        if symbol is None or symbol.kind not in ("function", "method"):
            return None
        return target

    # -- Dynamic Dispatch Sentinel (Task 2) -------------------------------- #
    def _emit_dynamic_edge_sentinel(self, caller_qname: str, hazard: DynamicEdgeSentinel) -> None:
        """Converts a call site whose real target only exists at runtime
        (`getattr`/`setattr`, `eval`/`exec`, a subscript/map-dispatched
        callee) into an explicit `DynamicEdgeSentinel` graph node instead
        of silently dropping the edge - see `call_site.detect_call_site_
        hazard`. The sentinel is a terminal leaf by construction: nothing
        here ever adds an outgoing edge *from* it, so no traversal guard
        elsewhere is needed to stop blast-radius expansion at this
        boundary (Task 3.1).
        """
        sentinel_id = dynamic_edge_sentinel_id(hazard.call_site_file, hazard.call_site_line, hazard.hazard_type)
        if sentinel_id not in self.graph:
            self.graph.add_node(
                sentinel_id,
                sentinel_type="dynamic_edge",
                call_site_expr=hazard.call_site_expr,
                target_object=hazard.target_object,
                hazard_type=hazard.hazard_type,
                call_site_file=hazard.call_site_file,
                call_site_line=hazard.call_site_line,
                external=True,
            )
        self.graph.add_edge(caller_qname, sentinel_id, relation="CALLS")

    # -- Polysemy Disambiguation (Task 1) ---------------------------------- #
    def _param_count(self, qualified_name: str, lang: str, is_method: bool) -> int | None:
        def_node = self._def_nodes.get(qualified_name)
        if def_node is None:
            return None
        params = def_node.child_by_field_name("parameters")
        if params is None:
            return None
        count = len(params.named_children)
        # Python is the only supported language whose grammar makes the
        # receiver (`self`) an explicit declared parameter - JS/TS/Go/
        # Java/C# methods never count their implicit receiver this way -
        # so only it needs the count adjusted to compare against a call
        # site's own (receiver-excluded) `args_passed_count`.
        if is_method and lang == LanguageID.PYTHON and count > 0:
            count -= 1
        return count

    def _resolve_ambiguous_call(
        self,
        caller_qname: str,
        call_node: Node,
        parsed: ParsedFile,
        module: str,
        import_map: LocalImportMap,
        segments: list[str],
    ) -> None:
        """A call site the normal import-map/instance-map/same-module rules
        (`_resolve_segments`) couldn't bind. If two or more repo-local
        functions/methods share this call's bare simple name, that's
        genuine polysemy (`Close()`, `Validate()`, ...) worth scoring
        rather than silently dropping - see
        `prism.graph.symbol_table.score_candidate`. Zero same-named
        candidates is an ordinary resolution gap, unrelated to this task,
        and is left exactly as before (silently unlinked) - the callee is
        presumably external/builtin, and there is nothing repo-local to
        even consider.

        Phase B (G44): exactly one same-named candidate repo-wide used to
        also fall through to the same silent-unlink path as zero - a
        completely unresolved receiver whose method name happens to be
        unique across the whole repo is a real, common case (this is
        the *only* place in the Python/JS resolution path this can occur;
        for count>=2 the scored path below already exists and already has
        its own real, tested "genuinely ambiguous -> UnresolvedPolymorphic
        sentinel" behavior when scoring can't clear the threshold - G44's
        "never guess among multiple candidates" is already satisfied
        there and is deliberately left untouched by this change, not
        replaced with a blunter "count>1 -> zero edges" rule that would
        have discarded that existing, more informative sentinel).
        """
        simple_name = segments[-1]
        candidates = self.symbol_table.candidates_for_simple_name(simple_name)
        if len(candidates) == 1:
            # G44 Conservative Candidate Fallback: a repo-wide receiver-
            # type-unknown call whose method name is unique - link it,
            # but as a best-effort guess, not a confidently-resolved
            # call. Reuses kind="TENTATIVE_CALL", the same already-wired
            # RELATION_TENTATIVE_CALL_WEIGHT=0.60 discount the Go-only
            # unique-receiver fallback already uses (see that fallback's
            # own docstring) - never a new, unrecognized kind that
            # prism.slicer.distance would otherwise price at full CALLS
            # confidence by default.
            #
            # Invariant 4.4 scope guard: never bridge across unrelated
            # subsystems on a bare repo-wide name match alone. Checked
            # here via top-level package/module prefix only
            # (_shares_package_scope) - the brief's other permitted
            # bridge condition ("substance tag overlap") is NOT checked:
            # four-axis tags (prism.semantics.substance et al.) are
            # computed from the *completed* concrete graph, since Role
            # depends on real fan-in/fan-out counts - they do not exist
            # yet during this Pass 2 call-resolution walk, which is what
            # builds that same graph. A known, documented gap, not a
            # silently-skipped check.
            candidate = candidates[0]
            caller_info = self.symbol_table.get(caller_qname)
            if caller_info is not None and not _shares_package_scope(caller_info.module, candidate.module):
                return
            target = candidate.qualified_name
            if target not in self.graph:
                self.graph.add_node(target, external=False)
            self.graph.add_edge(caller_qname, target, relation="CALLS", kind="TENTATIVE_CALL")
            return
        if len(candidates) < 2:
            return

        args_node = call_node.child_by_field_name("arguments") or call_node.child_by_field_name("argument_list")
        call_args_count = len(args_node.named_children) if args_node is not None else 0
        caller_file = parsed.path

        # Zero-Debt Hardening Pass (Task 4): `score_candidate` is a
        # discrete weighted sum over a small number of possible inputs
        # (namespace in {0,1}, locality in {1.0, 0.6, 0.2}, a discrete
        # arity match) - two candidates neither the caller's own file/
        # package nor an imported module score *identically*, which is
        # the common case, not an edge case, for a call site whose real
        # candidates are all equally unrelated (every function sharing a
        # bare 1-2 character name across a minified JS vendor file, say -
        # exactly the scenario this real, measured nondeterminism was
        # found in: prism.semantics.extractor.compute_feature_masks_
        # cached's own cache-parity test flaking on symbols reached
        # through exactly this ambiguous-call path). `> best_score`
        # alone leaves a tie's winner to whichever candidate happened to
        # be first in `candidates` - itself Pass 1's own symbol-
        # registration order, never guaranteed stable run to run. Sorting
        # candidates canonically first makes the winner of a genuine tie
        # deterministic (the alphabetically-first qualified name),
        # without changing which candidate wins a real, non-tied score.
        best_candidate: SymbolInfo | None = None
        best_score = -1.0
        for candidate in sorted(candidates, key=lambda c: c.qualified_name):
            ns = namespace_match(candidate, module, import_map)
            loc = locality_distance(candidate, caller_file, module)
            param_count = self._param_count(candidate.qualified_name, candidate.language_id, candidate.kind == "method")
            arity = arity_match(param_count, call_args_count)
            score = score_candidate(ns, arity, loc)
            if score > best_score:
                best_score = score
                best_candidate = candidate

        line = call_node.start_point[0] + 1
        if best_candidate is not None and best_score >= POLYSEMY_THRESHOLD:
            target = best_candidate.qualified_name
            if target not in self.graph:
                self.graph.add_node(target, external=False)
            self.graph.add_edge(caller_qname, target, relation="CALLS")
            return

        sentinel_id = unresolved_polymorphic_node_id(simple_name, caller_file, line)
        if sentinel_id not in self.graph:
            self.graph.add_node(
                sentinel_id,
                sentinel_type="unresolved_polymorphic",
                identifier=simple_name,
                candidates=sorted(c.qualified_name for c in candidates),
                call_site_file=caller_file,
                call_site_line=line,
                external=True,
            )
        self.graph.add_edge(caller_qname, sentinel_id, relation="CALLS")

    # -- INSTANTIATES via `new X()` (JS/TS/Java/C#) ----------------------- #
    _NEW_EXPRESSION_TYPES = frozenset({"new_expression", "object_creation_expression"})

    def _link_new_expression_instantiations(
        self, caller_qname: str, def_node: Node, parsed: ParsedFile, module: str, import_map: LocalImportMap
    ) -> None:
        lang = parsed.language_id
        new_types = {t for t in self._NEW_EXPRESSION_TYPES if t != CALL_NODE_TYPE.get(lang)}
        if not new_types:
            return
        for new_node in iter_scoped_nodes(def_node, new_types, lang):
            ctor_segments = _constructor_call_segments(new_node, lang, parsed.source)
            if ctor_segments is None and new_node.type == "new_expression":
                ctor_node = new_node.child_by_field_name("constructor")
                if ctor_node is not None:
                    ctor_segments = flatten_reference_chain(ctor_node, parsed.source, lang)
            if not ctor_segments:
                continue
            target = self._resolve_reference_chain(ctor_segments, module, import_map)
            if not self._is_known_class(target):
                continue
            if target not in self.graph:
                self.graph.add_node(target, external=False)
            self.graph.add_edge(caller_qname, target, relation="INSTANTIATES")

    # -- READS_STATE (Python only - see module docstring) ------------------ #
    def _link_state_reads(
        self, caller_qname: str, def_node: Node, parsed: ParsedFile, module: str, enclosing_class: str | None,
        self_tokens: set[str],
    ) -> None:
        if enclosing_class is None:
            return
        lang = parsed.language_id
        attr_type = "attribute"
        assign_type = ASSIGNMENT_NODE_TYPE.get(lang)
        call_type = CALL_NODE_TYPE.get(lang)
        for attr_node in iter_scoped_nodes(def_node, {attr_type}, lang):
            segments = flatten_reference_chain(attr_node, parsed.source, lang)
            if not segments or len(segments) != 2 or segments[0] not in self_tokens:
                continue
            parent = attr_node.parent
            if parent is None:
                continue
            # Skip write targets (`self.x = ...`) and call callees
            # (`self.x()`) - both are already CALLS/attribute-definition
            # edges elsewhere; READS_STATE is specifically for reading an
            # attribute's *value* (`if self.x:`, `return self.x`, `y =
            # self.x`, ...).
            # tree-sitter's Python bindings return a fresh wrapper object
            # per access, so `is` never matches even for the identical
            # underlying node - `.id` (or `==`, which tree-sitter defines
            # to compare the same way) is the correct identity check here.
            left = parent.child_by_field_name("left") if parent.type == assign_type else None
            if left is not None and left.id == attr_node.id:
                continue
            func = parent.child_by_field_name("function") if parent.type == call_type else None
            if func is not None and func.id == attr_node.id:
                continue
            target = f"{enclosing_class}.{segments[1]}"
            if target not in self.symbol_table:
                continue
            if target not in self.graph:
                self.graph.add_node(target, external=False)
            self.graph.add_edge(caller_qname, target, relation="READS_STATE")

    def _resolve_segments(
        self,
        segments: list[str],
        module: str,
        enclosing_class: str | None,
        import_map: LocalImportMap,
        class_instance_map: InstanceTypeMap,
        func_instance_map: InstanceTypeMap,
        self_tokens: set[str],
        lang: str | None = None,
    ) -> str | None:
        self._last_resolution_was_tentative = False
        self._last_resolution_was_ambiguous = False
        self._last_resolution_was_builtin_receiver = False
        if len(segments) == 1:
            return self._resolve_reference_chain(segments, module, import_map)

        method = segments[-1]
        receiver_segments = segments[:-1]
        receiver_key = ".".join(receiver_segments)

        if receiver_segments[0] in self_tokens:
            # Phase B (G41): `func_instance_map` wins over `class_instance_map`
            # on a hit, same precedence as before - ambiguity is checked
            # against whichever map actually supplied the candidate, not
            # both unconditionally (a name ambiguous in one scope but not
            # the one that actually resolved it should not be flagged).
            if func_instance_map.resolve(receiver_key) is not None:
                candidate = func_instance_map.resolve(receiver_key)
                ambiguous = func_instance_map.is_ambiguous(receiver_key)
            else:
                candidate = class_instance_map.resolve(receiver_key)
                ambiguous = class_instance_map.is_ambiguous(receiver_key)
            if candidate:
                if ambiguous:
                    # G41 Invariant 3: branching initialization bound
                    # `self.<attr>` to more than one concrete class -
                    # reduced confidence, not a clean single-type
                    # resolution. Reuses the existing kind="TENTATIVE_CALL"
                    # marker (see phase-b-g44's commit for why: any kind
                    # `prism.slicer.distance` doesn't recognize silently
                    # defaults to full CALLS-level confidence, the opposite
                    # of "reduced" - there is no separate "AMBIGUOUS_CHAIN"
                    # discount wired into that module, and adding one is
                    # out of Phase B's scope).
                    self._last_resolution_was_ambiguous = True
                return f"{candidate}.{method}"
            if func_instance_map.is_builtin(receiver_key) or class_instance_map.is_builtin(receiver_key):
                # Builtin-Receiver Exclusion fix: `self.<attr>` is known
                # to hold a Python builtin container/primitive
                # (`self.field_names = set()`), never a real class -
                # `<attr>.<method>()` must never fall through to the
                # enclosing class's own MRO lookup below (which exists
                # for the different case of `self.<method>()` itself)
                # or, back in `_resolve_calls_in_function`, to the G44
                # bare-name/polysemy fallback.
                self._last_resolution_was_builtin_receiver = True
                return None
            if len(receiver_segments) == 1 and enclosing_class:
                direct = f"{enclosing_class}.{method}"
                if direct in self.symbol_table:
                    return direct
                # `self.<method>()` where `<method>` isn't declared
                # directly on `enclosing_class` itself - a real, common
                # case for an inherited (and not overridden) method
                # (Issue #9's "phantom method" failure: `self.validate()`
                # calling a base class's `validate` had no reachable
                # definition). Walk the MRO ancestor chain for the
                # nearest real definition rather than returning a guessed
                # name that isn't actually in the symbol table.
                for ancestor in self._mro_ancestors(enclosing_class):
                    inherited = f"{ancestor}.{method}"
                    if inherited in self.symbol_table:
                        return inherited
                return direct
            return None

        candidate = func_instance_map.resolve(receiver_key) or class_instance_map.resolve(receiver_key)
        if func_instance_map.is_builtin(receiver_key) or class_instance_map.is_builtin(receiver_key):
            # Builtin-Receiver Exclusion fix: a local variable known to
            # hold a Python builtin container/primitive
            # (`field_names = set()`) - never a real class, so
            # `<var>.<method>()` must never fall through to
            # `_resolve_reference_chain`'s module-level lookup below or,
            # back in `_resolve_calls_in_function`, to the G44 bare-name/
            # polysemy fallback (the real bug this fix targets: a call
            # like `field_names.add(...)`/`field_names.difference(...)`
            # was resolving, with high confidence, to an unrelated
            # same-named method elsewhere in a large corpus - e.g.
            # `GeometryCollection.add`/`QuerySet.difference` for a call
            # inside `django.db.models.base.Model.save`).
            self._last_resolution_was_builtin_receiver = True
            return None
        if candidate:
            direct_candidate = f"{candidate}.{method}"
            # Item 5/7 (second post-implementation audit): a Go struct's
            # own type is known here (Item 3 Stage 1's parameter/short-
            # var-decl binding), but the method called isn't declared
            # directly on it - exactly the "promoted method via struct
            # embedding" case, resolved before falling back to this
            # function's pre-existing (every-language) behavior of
            # returning the direct-candidate name regardless of whether
            # it actually exists (the caller registers it as an
            # `external` node when it doesn't - unchanged for every
            # other language, and for Go too when promotion also fails).
            if lang == LanguageID.GO and direct_candidate not in self.symbol_table:
                promoted = self._go_promoted_method(candidate, method)
                if promoted is not None:
                    return promoted
            if direct_candidate not in self.symbol_table:
                # Phase I: Issue #9's "phantom method" fix (see the
                # `self.<method>()` branch above, lines ~2006-2018)
                # generalized from "self" to any instance-typed local
                # variable/parameter this class already tracks via
                # `func_instance_map`/`class_instance_map`. `f =
                # DateField(); f.clean(x)`, where `DateField` inherits
                # `clean` from `Field` without overriding it, previously
                # returned the guessed-but-nonexistent `DateField.clean`
                # unconditionally (registered as an `external` node) -
                # confirmed as a real, repo-wide undercount: Django's
                # own `django.forms.fields.Field.clean` blast-radius
                # ground truth task (37 real callers, mostly exactly
                # this "typed local variable calling an inherited
                # method" shape) was only finding 2 before this fix. A
                # no-op for Go (no EXTENDS/IMPLEMENTS edges are ever
                # built there - `_mro_ancestors` always returns `[]`),
                # so this never interferes with the Go-specific
                # promoted-method check just above.
                for ancestor in self._mro_ancestors(candidate):
                    inherited = f"{ancestor}.{method}"
                    if inherited in self.symbol_table:
                        return inherited
            return direct_candidate

        resolved_receiver = self._resolve_reference_chain(receiver_segments, module, import_map)
        if resolved_receiver:
            return f"{resolved_receiver}.{method}"

        # Item 3 (second post-implementation audit) Stage 2: Go-only
        # Codebase-Unique Receiver Fallback. Reached only when Stage 1
        # (parameter/short-var-declaration type tracking) couldn't
        # resolve `receiver_key`'s type locally - if the call site is
        # still receiver-shaped (`receiver_segments[0]` isn't a known
        # import alias, ruling out a package-qualified call like
        # `gin.Default()`) and *exactly one* struct type anywhere in the
        # repository defines a method of this exact simple name, bind to
        # it as a best-effort guess rather than dropping the call
        # entirely - flagged as tentative (lower structural weight, see
        # `RELATION_TENTATIVE_CALL_WEIGHT` in `prism.slicer.distance`) so
        # it never outranks a confidently-resolved neighbor. Multiple
        # same-named methods on different structs is exactly the
        # ambiguous case this does *not* guess through.
        if lang == LanguageID.GO and import_map.resolve(receiver_segments[0]) is None:
            unique = self._go_unique_receiver_for_method(method)
            if unique is not None:
                self._last_resolution_was_tentative = True
                return unique
        return None

    def _go_promoted_method(self, struct_qname: str, method: str) -> str | None:
        """Item 7 (second post-implementation audit): Go's real method-
        resolution shadowing rules, via breadth-first search over
        `EMBEDS` edges with explicit depth tracking - deliberately not
        called MRO anywhere (Go's rules are shadowing-by-depth, not C3
        linearization):

        - A method declared directly on `struct_qname` itself always
          wins (checked by this function's one caller *before* calling
          it, via `direct_candidate in self.symbol_table` - not
          re-checked here).
        - Among embedded types, the *nearest* embedding depth wins -
          `type T struct { A; B }` where only `A` (depth 1) defines
          `M()` resolves `t.M()` to `A.M`, even if `A` itself embeds
          another type at depth 2 that also defines `M()`.
        - A collision *at the same depth* (two directly-embedded types
          both defining the same method name) is ambiguous and is never
          guessed - real Go itself refuses to compile `t.M()` in that
          shape without explicit qualification
          (`self._last_go_embedded_collision` is set to `method` for
          this case specifically, so a caller/test can distinguish "no
          promoted method exists at all" from "found, but ambiguous",
          both of which return `None` here).
        """
        self._last_go_embedded_collision = None
        visited = {struct_qname}
        queue = [(struct_qname, 0)]
        by_depth: dict[int, list[str]] = {}
        while queue:
            node, depth = queue.pop(0)
            for _source, target, data in self.graph.out_edges(node, data=True):
                if data.get("relation") != "EMBEDS" or target in visited:
                    continue
                visited.add(target)
                candidate = f"{target}.{method}"
                if candidate in self.symbol_table:
                    by_depth.setdefault(depth + 1, []).append(candidate)
                queue.append((target, depth + 1))
        if not by_depth:
            return None
        nearest = by_depth[min(by_depth)]
        if len(nearest) == 1:
            return nearest[0]
        self._last_go_embedded_collision = method
        return None

    def _go_unique_receiver_for_method(self, method: str) -> str | None:
        """The single qualified Go method `<Module>.<Type>.<method>` if
        exactly one such method exists anywhere in the whole repository's
        symbol table, else `None` (zero or multiple candidates - never
        guessed). Backs Item 3 Stage 2's tentative-call fallback; marked
        specially by the caller (`_resolve_calls_in_function`) so the
        resulting edge carries `kind="TENTATIVE_CALL"`, not treated the
        same as a normal resolved call.
        """
        registry = self._go_method_registry()
        candidates = registry.get(method)
        if candidates is None or len(candidates) != 1:
            return None
        return candidates[0]

    def _go_method_registry(self) -> dict[str, list[str]]:
        """Simple method name -> every qualified Go method of that name
        anywhere in the repo (`Context.JSON` -> `["main.Context.JSON",
        "otherpkg.Handler.JSON"]` if two unrelated structs both happen to
        define a `JSON` method) - built once, lazily, and cached; Pass 1
        (global definition collection) must be complete before this is
        ever called, which it always is by the time Pass 2 call
        resolution runs. Item 14: double-checked against
        `_lazy_cache_lock`, same as `calls_graph`.
        """
        if self._go_method_registry_cache is None:
            with self._lazy_cache_lock:
                if self._go_method_registry_cache is None:
                    registry: dict[str, list[str]] = {}
                    for symbol in self.symbol_table:
                        if symbol.kind == "method" and symbol.language_id == LanguageID.GO:
                            simple_name = symbol.qualified_name.rsplit(".", 1)[-1]
                            registry.setdefault(simple_name, []).append(symbol.qualified_name)
                    self._go_method_registry_cache = registry
        return self._go_method_registry_cache


_LOCAL_VAR_DECLARATOR_TYPE: dict[str, str] = {
    LanguageID.JAVA: "variable_declarator",
    LanguageID.CSHARP: "variable_declarator",
    # Phase J: JS/TS/TSX's `const c = new Circle()`/`let c = new Circle()`
    # is the same "typed local variable declaration" shape as Java/C#'s
    # own `variable_declarator` (tree-sitter's grammar happens to reuse
    # the identical node-type name), not a bare reassignment
    # (`ASSIGNMENT_NODE_TYPE`, which only matches `c = new Circle()`
    # with no preceding `const`/`let`/`var`) - both loops in
    # `_build_function_instance_map` already run for every language in
    # `instance_binding_langs` below, so adding this entry is what
    # actually turns the existing declarator-scanning loop on for these
    # three languages, rather than needing a separate one.
    LanguageID.JAVASCRIPT: "variable_declarator",
    LanguageID.TYPESCRIPT: "variable_declarator",
    LanguageID.TSX: "variable_declarator",
}


def _declarator_value_node(declarator: Node) -> Node | None:
    """The initializer expression of a `variable_declarator`
    (`Type name = <value>;`), if any. Java's grammar exposes it as a
    "value" field; C#'s grammar does not assign the initializer a field
    name at all (confirmed empirically - `child_by_field_name("value")`
    returns None even though the child node is present as the third
    positional child, after the name identifier and the "=" token) - so
    this falls back to the second *named* child, since "=" itself is
    unnamed and the declared name is always first.
    """
    value = declarator.child_by_field_name("value")
    if value is not None:
        return value
    named = [c for c in declarator.children if c.is_named]
    return named[1] if len(named) > 1 else None


def _go_type_identifier_text(type_node: Node | None, source: bytes) -> str | None:
    """The bare `type_identifier` text of a Go parameter/receiver `type`
    field, unwrapping one level of `pointer_type` if present (`*Context`
    and `Context` both resolve to `"Context"` - Go's method-set rules
    treat a pointer and value receiver of the same named type as
    methods of that one type, so the qualified name this feeds into
    (Issue B1) should not fork on the pointer sigil). Returns `None` for
    anything else this doesn't model - a qualified type from another
    package (`pkg.Context`), a generic instantiation, a slice/map/
    interface type - rather than guessing.
    """
    if type_node is None:
        return None
    if type_node.type == "pointer_type":
        type_node = type_node.named_children[0] if type_node.named_children else None
    if type_node is None or type_node.type != "type_identifier":
        return None
    return node_text(type_node, source)


def _go_receiver_type(method_node: Node, parsed: ParsedFile) -> str | None:
    """The base type name a Go `method_declaration`'s receiver clause
    names (Issue B1) - e.g. `"Context"` for both `func (c *Context)
    M()` (pointer receiver) and `func (c Context) M()` (value
    receiver). `None` for a receiver this doesn't recognize (an
    anonymous/unnamed receiver's type still resolves fine since only the
    `type` field is read; a generic receiver type parameter does not).
    """
    receiver = method_node.child_by_field_name("receiver")
    if receiver is None:
        return None
    param_decl = next((c for c in receiver.named_children if c.type == "parameter_declaration"), None)
    if param_decl is None:
        return None
    return _go_type_identifier_text(param_decl.child_by_field_name("type"), parsed.source)


def _go_composite_literal_type(value_node: Node, source: bytes) -> str | None:
    """The bare type name of a Go composite-literal construction (`Type{}`
    or, unwrapping one level of `&`, `&Type{}`) - Item 3's local
    short-var-declaration binding. `None` for anything else (a bare
    call like `gin.Default()`, a qualified `pkg.Type{}` literal, a
    slice/map literal, ...) - deliberately narrow, not guessed.
    """
    node = value_node
    if node.type == "unary_expression":
        node = node.child_by_field_name("operand")
    if node is None or node.type != "composite_literal":
        return None
    return _go_type_identifier_text(node.child_by_field_name("type"), source)


#: Builtin-Receiver Exclusion fix: Python's own literal-display and
#: comprehension node types that always construct a builtin container or
#: primitive, never a repo-defined class - confirmed directly against a
#: real tree-sitter-python parse (`set()`, not `{1, 2}` empty-set
#: literal syntax, is the only builtin container with no dedicated
#: literal node; it is always a `call` node, handled separately by
#: `_BUILTIN_FACTORY_NAMES` below).
_PYTHON_BUILTIN_LITERAL_NODE_TYPES = frozenset({
    "dictionary", "list", "tuple", "string",
    "set_comprehension", "list_comprehension", "dictionary_comprehension", "generator_expression",
})

#: Bare (unqualified) builtin factory call names - `set()`, `list()`, ...
#: Checked only when the call resolves to nothing else real (see
#: `_is_builtin_container_expr`), so a local class that happens to share
#: one of these names is never misclassified as the builtin.
_BUILTIN_FACTORY_NAMES = frozenset({"set", "list", "dict", "tuple", "frozenset", "bytearray", "bytes", "str"})

#: `collections.<name>(...)`-qualified factories - restricted to this
#: exact, literal module prefix (not resolved through import aliasing)
#: to avoid ever misclassifying an unrelated same-named local class.
_COLLECTIONS_BUILTIN_FACTORY_NAMES = frozenset({"deque", "defaultdict", "OrderedDict", "Counter", "ChainMap"})


# -- Symbol Role Classification (Phase B: repo-agnostic test-symbol -------- #
# demotion, replacing the hardcoded `_NEVER_PIPELINE_MODULE_PREFIXES`
# module-path blacklist that used to live in
# `prism.packer.submodular_knapsack`). Every pattern below is a naming
# *convention* shared across mainstream xUnit-style test frameworks in
# multiple languages, or a structural AST-shape check - never a literal
# repository path, package name, or framework name (e.g. never
# `"django.test"` or `"tests/"`). See `SymbolRole`'s own docstring
# (`prism.graph.symbol_table`) for why this replaced a path-substring
# check: the old check only fired when a candidate's knapsack novelty
# score was exactly zero, so a test symbol that happened to carry a
# fresh four-axis feature bit sailed straight through it - the confirmed
# root cause of the django_t02_017 budget-crowding case. This
# classification is unconditional on novelty, closing that gap by
# construction rather than by widening the old check's threshold.
#
# Honesty note (not overclaimed): "this is a test" is itself a naming
# convention in every mainstream language - there is no purely
# structural AST primitive for it the way there is for, say, "has a
# return statement." What these patterns avoid is a specific
# repository's or framework's literal string (`"django.test"`); they
# still rely on *conventions* (`TestCase`-suffixed base classes,
# `test_`-prefixed names, `assert`-prefixed calls) that are common to
# unittest/pytest/JUnit/xUnit/Go's `testing` package alike, not to one
# specific project.
_TEST_BASE_CLASS_PATTERN = re.compile(r"^Test|TestCase$")
_TEST_DECORATOR_PATTERN = re.compile(
    r"^(pytest\.)?(fixture|mark\.\w+)$|^parametrize$"
    r"|^(Test|Fact|Theory|Before|After|BeforeEach|AfterEach|BeforeClass|AfterClass)$"
)
_TEST_NAME_PATTERN = re.compile(r"^test_|Test$")
#: Matches Python/JS/TS `assert*`, `self.assert*`, and Go's `t.Fatal(f)?`/
#: `t.Error(f)?` (trailing-segment match via `call_callee_segments`, the
#: same convention `prism.tagger.rules.CALL_SINK_RULES` already uses).
_ASSERTION_CALL_PATTERN = re.compile(r"^assert|^(Fatal|Error)f?$")
_ASSERTION_DENSITY_THRESHOLD = 0.15
#: Go's `func TestXxx(t *testing.T)` / `BenchmarkXxx(b *testing.B)` -
#: structural signal (parameter type text), not a naming-only guess,
#: since Go has neither decorators nor test base classes to check instead.
_GO_TEST_PARAM_TYPE_PATTERN = re.compile(r"testing\.(T|B)\b")
_GO_TEST_FUNC_NAME_PREFIXES = ("Test", "Benchmark", "Example")


def _is_test_shaped_base_classes(class_node: Node, parsed: ParsedFile) -> bool:
    """Python-only (the one language here with a real `superclasses`
    field exposing base-class references directly) - any base class
    whose own simple name matches `_TEST_BASE_CLASS_PATTERN`
    (`unittest.TestCase`, `django.test.TestCase`, a bare `TestX`
    mixin, pytest's `Test*` class convention). Only the base's simple
    name is checked - `flatten_reference_chain` is used purely to strip
    any qualifying prefix (`unittest.TestCase` -> `TestCase`), never
    resolved to a qualified target, so an unresolvable/aliased import
    doesn't suppress the signal the way full resolution would.
    """
    if parsed.language_id != LanguageID.PYTHON:
        return False
    superclasses = class_node.child_by_field_name("superclasses")
    if superclasses is None:
        return False
    for child in superclasses.named_children:
        if child.type == "keyword_argument":
            continue
        segments = flatten_reference_chain(child, parsed.source, LanguageID.PYTHON)
        simple_name = segments[-1] if segments else node_text(child, parsed.source)
        if _TEST_BASE_CLASS_PATTERN.search(simple_name):
            return True
    return False


def _is_go_test_function(node: Node, name: str, parsed: ParsedFile) -> bool:
    """`func TestXxx(t *testing.T)` / `BenchmarkXxx(b *testing.B)` /
    `ExampleXxx()` - Go's own idiomatic test-function shape. Checks both
    the name-prefix convention *and* (for Test/Benchmark) the real
    parameter type text, so an ordinary function that merely happens to
    start with "Test" but takes no `*testing.T` isn't misclassified.
    """
    if parsed.language_id != LanguageID.GO or not name.startswith(_GO_TEST_FUNC_NAME_PREFIXES):
        return False
    if name.startswith("Example"):
        return True  # Example functions take no testing.T/B parameter at all
    params = node.child_by_field_name("parameters")
    if params is None:
        return False
    return bool(_GO_TEST_PARAM_TYPE_PATTERN.search(node_text(params, parsed.source)))


def _is_assertion_shaped_body(body: Node | None, lang: str, source: bytes) -> bool:
    """`True` if `body` is a function body whose call sites are
    substantially assertion calls (`_ASSERTION_DENSITY_THRESHOLD` of all
    call sites in the body), or that asserts at all while never
    returning a value - the "assertion density vs. return presence"
    pair of signals: a real implementation function usually returns
    something and rarely calls `assert*`/`t.Fatal` more than
    incidentally; a verification function usually does the reverse.
    """
    if body is None:
        return False
    call_type = CALL_NODE_TYPE.get(lang)
    if call_type is None:
        return False
    calls = find_all(body, {call_type})
    if not calls:
        return False
    assertion_calls = 0
    for call in calls:
        segments = call_callee_segments(call, source, lang)
        if segments and _ASSERTION_CALL_PATTERN.match(segments[-1]):
            assertion_calls += 1
    if assertion_calls == 0:
        return False
    if (assertion_calls / len(calls)) >= _ASSERTION_DENSITY_THRESHOLD:
        return True
    has_return = bool(find_all(body, {RETURN_STATEMENT_NODE_TYPE}))
    return not has_return


#: A Python body of exactly `pass`, `...`, a docstring followed by
#: either, or a bare `raise NotImplementedError(...)` (optionally after
#: a docstring) - an abstract/declaration-only method, not a real
#: implementation. Other languages' equivalent shapes (Java/C#
#: interface methods with no body at all) are covered by the
#: `body is None` branch below instead.
_INTERFACE_TRAILING_TYPES = frozenset({"pass_statement", "ellipsis"})


def _is_interface_shaped_body(body: Node | None, lang: str, source: bytes) -> bool:
    if body is None:
        return True
    if lang != LanguageID.PYTHON:
        return False
    stmts = [c for c in body.named_children if c.type != "comment"]
    if not stmts:
        return False
    # Skip a leading docstring (a bare string-literal expression statement).
    if stmts[0].type == "expression_statement" and len(stmts[0].named_children) == 1 and stmts[0].named_children[0].type == "string":
        stmts = stmts[1:]
    if len(stmts) != 1:
        return False
    only = stmts[0]
    if only.type == "pass_statement":
        return True
    if only.type == "expression_statement" and only.named_children and only.named_children[0].type == "ellipsis":
        return True
    if only.type == "raise_statement" and "NotImplementedError" in node_text(only, source):
        return True
    return False


def _classify_symbol_role(
    node: Node,
    is_class: bool,
    parsed: ParsedFile,
    name: str,
    enclosing_class_role: SymbolRole | None,
) -> SymbolRole:
    """Deterministic, AST/naming-convention-based role classification -
    see the module-level comment above `_TEST_BASE_CLASS_PATTERN` for
    why this exists and what it deliberately does and doesn't claim.
    Called once per symbol from `_register_definition`, in the same
    Pass-1 pass every other `SymbolInfo` field is already computed in.
    """
    lang = parsed.language_id

    if is_class:
        if _is_test_shaped_base_classes(node, parsed):
            return SymbolRole.VERIFICATION
        if _TEST_NAME_PATTERN.search(name):
            return SymbolRole.VERIFICATION
        return SymbolRole.IMPLEMENTATION

    # A method on a test-shaped class is verification code regardless of
    # its own name/decorators/body shape (a plain `setUp`/helper method
    # included) - the class-level signal dominates.
    if enclosing_class_role == SymbolRole.VERIFICATION:
        return SymbolRole.VERIFICATION

    if any(_TEST_DECORATOR_PATTERN.match(text) for text in collect_decorator_texts(node, parsed)):
        return SymbolRole.VERIFICATION

    if _is_go_test_function(node, name, parsed):
        return SymbolRole.VERIFICATION

    body = node.child_by_field_name("body")
    if _is_assertion_shaped_body(body, lang, parsed.source):
        return SymbolRole.VERIFICATION

    if _TEST_NAME_PATTERN.search(name):
        return SymbolRole.VERIFICATION

    if _is_interface_shaped_body(body, lang, parsed.source):
        return SymbolRole.INTERFACE

    return SymbolRole.IMPLEMENTATION


def _is_builtin_container_expr(value: Node, lang: str, source: bytes) -> bool:
    """`True` iff `value` (an assignment's RHS) is a Python expression
    that is *always* a builtin container/primitive - a literal display,
    a comprehension, or a bare/`collections.`-qualified factory call
    (`set()`, `collections.deque()`) - never a real, repo-indexed class.
    Python-only: verified directly against tree-sitter-python's real
    node shapes; other languages' equivalent literals are out of scope
    for this fix (not silently claimed equally precise, matching this
    module's own established per-language-coverage disclosure convention
    elsewhere).

    Callers must try the ordinary `_constructor_call_segments`/
    `_resolve_reference_chain`/`_is_known_class` resolution path *first*
    and only fall back to this check on that path's failure - protecting
    the rare case of a real, indexed local class that happens to share a
    builtin factory's bare name (e.g. a repo defining its own `Counter`
    class), which the ordinary path will correctly resolve before this
    purely name-based heuristic is ever consulted.
    """
    if lang != LanguageID.PYTHON:
        return False
    if value.type in _PYTHON_BUILTIN_LITERAL_NODE_TYPES:
        return True
    if value.type != CALL_NODE_TYPE.get(lang):
        return False
    callee = value.child_by_field_name("function")
    if callee is None:
        return False
    segments = flatten_reference_chain(callee, source, lang)
    if not segments:
        return False
    if len(segments) == 1 and segments[0] in _BUILTIN_FACTORY_NAMES:
        return True
    if len(segments) == 2 and segments[0] == "collections" and segments[1] in _COLLECTIONS_BUILTIN_FACTORY_NAMES:
        return True
    return False


def _constructor_call_segments(value: Node, lang: str, source: bytes) -> list[str] | None:
    """The dotted segments naming the class a constructor-shaped
    assignment's right-hand side invokes, for Rule A instance binding -
    a bare call (`Foo()`, Python's own constructor idiom - it has no
    separate `new` keyword), JS/TS's own `new_expression` (`new Foo()`/
    `new pkg.Foo()`), or Java/C#'s `object_creation_expression` (also
    `new Foo()`, but its own distinct grammar node) - the only ways any
    of these languages actually construct objects.
    """
    call_type = CALL_NODE_TYPE.get(lang)
    if value.type == call_type:
        ctor = value.child_by_field_name("function")
        return flatten_reference_chain(ctor, source, lang) if ctor is not None else None
    if value.type == "new_expression":
        # Phase J: JS/TS's own `new Foo()`/`new pkg.Foo()` - a distinct
        # grammar node from both the bare-call idiom above (Python) and
        # `object_creation_expression` below (Java/C#), previously
        # unhandled entirely, which meant `const c = new Circle()`
        # never bound `c`'s type at all - confirmed as a real, total
        # gap (not merely "MRO not walked"): even a call to a method
        # declared directly on `Circle` itself, no inheritance
        # involved, produced no CALLS edge. `flatten_reference_chain`
        # handles the identifier/attribute-chain constructor target
        # (`Foo`/`pkg.Foo`) the same way the bare-call branch already
        # does - a generic type argument (`new Foo<T>()`) is not
        # handled by that helper and correctly falls through to `None`
        # rather than being guessed at.
        ctor = value.child_by_field_name("constructor")
        return flatten_reference_chain(ctor, source, lang) if ctor is not None else None
    if value.type == "object_creation_expression":
        type_node = value.child_by_field_name("type")
        if type_node is None:
            return None
        # Java/C#'s "type" field is a simple (`type_identifier`/
        # `identifier`) or generic node, not one
        # flatten_reference_chain's identifier/attribute-chain walk
        # recognizes - take its literal text as a single segment
        # (qualified/generic types are rare here; the class is normally
        # already resolvable via the file's own imports/package).
        # Defensively strips `<...>` generic type arguments if present.
        return [node_text(type_node, source).split("<")[0].strip()]
    return None


def _parse_package_or_namespace(parsed: ParsedFile) -> str | None:
    """The file's own declared `package`/`namespace`, if any - Java always
    declares one at most, at the top level; C# may use either the
    file-scoped form (`namespace App.Services;`, C# 10+) or the
    block form (`namespace App.Services { ... }`, unbounded nesting in
    principle, but this repo's target frameworks - ASP.NET Core
    controllers/services - never nest namespaces in practice, so only the
    first top-level declaration is used). Returns None for Java's default
    (unnamed) package or a C# file with no namespace declaration at all,
    in which case the caller falls back to the file-path-derived module.
    """
    lang = parsed.language_id
    for node in parsed.root_node.children:
        if lang == LanguageID.JAVA and node.type == "package_declaration":
            for child in node.children:
                if child.type in ("scoped_identifier", "identifier"):
                    return node_text(child, parsed.source)
        elif lang == LanguageID.CSHARP and node.type in ("namespace_declaration", "file_scoped_namespace_declaration"):
            for child in node.children:
                if child.type in ("qualified_name", "identifier"):
                    return node_text(child, parsed.source)
    return None


def _shares_package_scope(caller_module: str, candidate_module: str) -> bool:
    """G44's scope guard (Invariant 4.4): `caller_module` and
    `candidate_module` share the same top-level package/namespace segment
    (`"django.contrib.auth"` and `"django.forms"` both start with
    `"django"`; `"django.contrib.auth"` and `"stripe_client"` do not) - a
    coarse, cheap proxy for "the same subsystem", available at Pass 2 call-
    resolution time (unlike substance-tag overlap, which needs the
    completed graph - see this function's one caller). The same module
    trivially shares scope with itself.
    """
    return caller_module.split(".")[0] == candidate_module.split(".")[0]
