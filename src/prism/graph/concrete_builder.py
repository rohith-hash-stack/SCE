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

import os

import networkx as nx
from tree_sitter import Node

from prism.parser.lang_config import (
    ASSIGNMENT_NODE_TYPE,
    CALL_NODE_TYPE,
    CLASS_NODE_TYPES,
    DECORATED_WRAPPER_TYPES,
    SELF_TOKEN_TEXT,
    call_callee_segments,
    find_all,
    flatten_reference_chain,
    iter_scoped_nodes,
)
from prism.parser.queries import run_query
from prism.parser.tree_sitter_loader import LanguageID, ParsedFile, node_text, parse_file
from prism.graph.call_site import (
    DynamicEdgeSentinel,
    compute_call_site_context,
    detect_call_site_hazard,
    dynamic_edge_sentinel_id,
)
from prism.graph.symbol_table import (
    ExportRegistry,
    GlobalSymbolTable,
    InstanceTypeMap,
    LocalImportMap,
    SymbolInfo,
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
# without it" failure mode to justify the same trade.
TRAVERSABLE_RELATIONS = frozenset({"CALLS", "INSTANTIATES", "EXTENDS", "IMPLEMENTS", "OVERRIDES"})


class ConcreteGraphBuilder:
    """Builds `G_C` from a set of source files via the two-pass linker."""

    def __init__(self, repo_root: str, symbol_table: GlobalSymbolTable | None = None) -> None:
        self.repo_root = repo_root
        self.symbol_table = symbol_table if symbol_table is not None else GlobalSymbolTable()
        self.graph = nx.DiGraph()
        self._parsed_files: dict[str, ParsedFile] = {}
        self._def_nodes: dict[str, Node] = {}
        self._methods_by_class: dict[str, list[str]] = {}
        self._calls_graph_cache: nx.DiGraph | None = None
        #: Barrel-file / re-export tracking (Issues #6/#7) - see
        #: `prism.graph.symbol_table.ExportRegistry`.
        self.export_registry = ExportRegistry()

    def parsed_file(self, path: str) -> ParsedFile | None:
        return self._parsed_files.get(path)

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
        """
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
            parsed = parse_file(path)
            if parsed is None:
                continue
            self._parsed_files[path] = parsed
            module = self._module_for_file(parsed)
            self._collect_definitions_in_file(parsed, module)

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
        def_nodes: list[tuple[Node, bool]] = []
        for n in captures.get("def.class", []):
            def_nodes.append((n, True))
        for n in captures.get("def.function", []):
            def_nodes.append((n, False))
        def_nodes.sort(key=lambda t: t[0].start_byte)
        for node, is_class in def_nodes:
            self._register_definition(node, is_class, parsed, module)
        if lang == LanguageID.PYTHON:
            self._collect_attribute_definitions(parsed, module)

    def _register_definition(self, node: Node, is_class: bool, parsed: ParsedFile, module: str) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = node_text(name_node, parsed.source)
        lang = parsed.language_id
        class_types = CLASS_NODE_TYPES[lang]

        enclosing_class: str | None = None
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
            kind = "class" if is_class else "function"

        outer = node
        wrapper_types = DECORATED_WRAPPER_TYPES.get(lang, set())
        if node.parent is not None and node.parent.type in wrapper_types:
            outer = node.parent

        line_range = (outer.start_point[0] + 1, outer.end_point[0] + 1)
        symbol = SymbolInfo(
            qualified_name=qualified_name,
            kind=kind,
            file=parsed.path,
            line_range=line_range,
            language_id=lang,
            module=module,
            enclosing_class=enclosing_class,
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
            if symbol.kind == "class":
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
            module = self._module_for_file(parsed)
            import_map = self._build_import_map(parsed, module)
            import_maps[path] = import_map
            self._register_exports(parsed, module, import_map)
            self._link_class_relations(classes_by_file.get(path, []), parsed, module, import_map)

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
            if parsed is None:
                continue
            module = self._module_for_file(parsed)
            import_map = import_maps[path]
            file_symbols = [
                qname
                for qname in self._def_nodes
                if (symbol := self.symbol_table.get(qname)) is not None
                and symbol.file == path
                and symbol.kind in ("function", "method")
            ]
            instance_binding_langs = (LanguageID.PYTHON, LanguageID.JAVA, LanguageID.CSHARP)
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
                value = assign.child_by_field_name("right")
                if target is None or value is None:
                    continue
                ctor_segments = _constructor_call_segments(value, parsed.language_id, parsed.source)
                if ctor_segments is None:
                    continue
                target_segments = flatten_reference_chain(target, parsed.source, parsed.language_id)
                if not target_segments or len(target_segments) != 2 or target_segments[0] not in self_tokens:
                    continue
                resolved_class = self._resolve_reference_chain(ctor_segments, module, import_map)
                if self._is_known_class(resolved_class):
                    instance_map.bind(".".join(target_segments), resolved_class)
        return instance_map

    def _is_known_class(self, qualified_name: str | None) -> bool:
        if not qualified_name:
            return False
        symbol = self.symbol_table.get(qualified_name)
        return symbol is not None and symbol.kind == "class"

    # -- EXTENDS / IMPLEMENTS ---------------------------------------------- #
    def _link_class_relations(self, class_qnames: list[str], parsed: ParsedFile, module: str, import_map: LocalImportMap) -> None:
        """`class Foo(Base): ...` / `class Foo extends Base implements
        I: ...` - real, verified for Python (a `superclasses` field holding
        an `argument_list`, `metaclass=`-shaped keyword arguments filtered
        out) and JS/TS (a `class_heritage` node holding separate
        `extends_clause`/`implements_clause` children - JS's grammar never
        produces an `implements_clause` at all, so that half degrades
        cleanly to "no interfaces" there rather than needing its own
        branch). Not yet implemented for Go/Java/C# - out of scope for this
        pass, not silently claimed.
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
                segments = flatten_reference_chain(name_node, parsed.source, parsed.language_id)
                if not segments:
                    continue
                target = self._resolve_reference_chain(segments, module, import_map)
                if self._is_known_class(target):
                    self._add_relation_edge(class_qname, target, relation)

    def _add_relation_edge(self, source: str, target: str, relation: str) -> None:
        if target not in self.graph:
            self.graph.add_node(target, external=False)
        self.graph.add_edge(source, target, relation=relation)

    # -- OVERRIDES + Python C3-ish MRO (Issue #9) ------------------------- #
    def _mro_ancestors(self, class_qname: str) -> list[str]:
        """`class_qname`'s own ancestor chain via its `EXTENDS` edges,
        nearest-first, depth-first, left-to-right in declared base order,
        each visited at most once - a practical approximation of Python's
        real C3 linearization: exact for single inheritance and for
        ordinary (non-diamond) multiple inheritance, which is the
        overwhelming majority of real code; a genuine diamond
        (`class D(B, C)` where both `B` and `C` extend `A`) may order
        differently from true C3's consistency-corrected linearization -
        out of scope for this pass, not silently claimed as exact.
        """
        ordered: list[str] = []
        seen: set[str] = {class_qname}

        def visit(node: str) -> None:
            for _source, target, data in self.graph.out_edges(node, data=True):
                if data.get("relation") != "EXTENDS" or target in seen:
                    continue
                seen.add(target)
                ordered.append(target)
                visit(target)

        visit(class_qname)
        return ordered

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
                    continue
                resolved_class = self._resolve_reference_chain(ctor_segments, module, import_map)
                if self._is_known_class(resolved_class):
                    instance_map.bind(node_text(target, parsed.source), resolved_class)

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
        return instance_map

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
            segments = call_callee_segments(call_node, parsed.source, lang)
            if not segments:
                continue
            target = self._resolve_segments(
                segments, module, enclosing_class, import_map, class_instance_map, func_instance_map, self_tokens
            )
            if target is None:
                self._resolve_ambiguous_call(caller_qname, call_node, parsed, module, import_map, segments)
                continue
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
        `prism.graph.symbol_table.score_candidate`. A single (or zero)
        same-named candidate is an ordinary resolution gap, unrelated to
        this task, and is left exactly as before (silently unlinked).
        """
        simple_name = segments[-1]
        candidates = self.symbol_table.candidates_for_simple_name(simple_name)
        if len(candidates) < 2:
            return

        args_node = call_node.child_by_field_name("arguments") or call_node.child_by_field_name("argument_list")
        call_args_count = len(args_node.named_children) if args_node is not None else 0
        caller_file = parsed.path

        best_candidate: SymbolInfo | None = None
        best_score = -1.0
        for candidate in candidates:
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
                candidates=[c.qualified_name for c in candidates],
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
    ) -> str | None:
        if len(segments) == 1:
            return self._resolve_reference_chain(segments, module, import_map)

        method = segments[-1]
        receiver_segments = segments[:-1]
        receiver_key = ".".join(receiver_segments)

        if receiver_segments[0] in self_tokens:
            candidate = func_instance_map.resolve(receiver_key) or class_instance_map.resolve(receiver_key)
            if candidate:
                return f"{candidate}.{method}"
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
        if candidate:
            return f"{candidate}.{method}"

        resolved_receiver = self._resolve_reference_chain(receiver_segments, module, import_map)
        if resolved_receiver:
            return f"{resolved_receiver}.{method}"
        return None


_LOCAL_VAR_DECLARATOR_TYPE: dict[str, str] = {
    LanguageID.JAVA: "variable_declarator",
    LanguageID.CSHARP: "variable_declarator",
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


def _constructor_call_segments(value: Node, lang: str, source: bytes) -> list[str] | None:
    """The dotted segments naming the class a constructor-shaped
    assignment's right-hand side invokes, for Rule A instance binding -
    either a bare call (`Foo()`, Python/JS's constructor idiom) or
    Java/C#'s `object_creation_expression` (`new Foo()`), the only way
    either language actually constructs objects.
    """
    call_type = CALL_NODE_TYPE.get(lang)
    if value.type == call_type:
        ctor = value.child_by_field_name("function")
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
