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

from sce.parser.lang_config import (
    ASSIGNMENT_NODE_TYPE,
    CALL_NODE_TYPE,
    CLASS_NODE_TYPES,
    DECORATED_WRAPPER_TYPES,
    SELF_TOKEN_TEXT,
    find_all,
    flatten_reference_chain,
    iter_scoped_nodes,
)
from sce.parser.queries import run_query
from sce.parser.tree_sitter_loader import LanguageID, ParsedFile, node_text, parse_file
from sce.graph.symbol_table import (
    GlobalSymbolTable,
    InstanceTypeMap,
    LocalImportMap,
    SymbolInfo,
    path_to_module,
)


class ConcreteGraphBuilder:
    """Builds `G_C` from a set of source files via the two-pass linker."""

    def __init__(self, repo_root: str, symbol_table: GlobalSymbolTable | None = None) -> None:
        self.repo_root = repo_root
        self.symbol_table = symbol_table if symbol_table is not None else GlobalSymbolTable()
        self.graph = nx.DiGraph()
        self._parsed_files: dict[str, ParsedFile] = {}
        self._def_nodes: dict[str, Node] = {}
        self._methods_by_class: dict[str, list[str]] = {}

    def parsed_file(self, path: str) -> ParsedFile | None:
        return self._parsed_files.get(path)

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
            module = path_to_module(path, self.repo_root)
            self._collect_definitions_in_file(parsed, module)

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

    # ------------------------------------------------------------------ #
    # Pass 2: Scoped Resolution & Edge Assembly
    # ------------------------------------------------------------------ #
    def pass2_resolve_calls(self, files: list[str]) -> None:
        for path in sorted(files):
            parsed = self._parsed_files.get(path)
            if parsed is None:
                continue
            module = path_to_module(path, self.repo_root)
            import_map = self._build_import_map(parsed, module)
            file_symbols = [
                qname
                for qname in self._def_nodes
                if (symbol := self.symbol_table.get(qname)) is not None
                and symbol.file == path
                and symbol.kind in ("function", "method")
            ]
            for qualified_name in file_symbols:
                symbol = self.symbol_table.get(qualified_name)
                def_node = self._def_nodes[qualified_name]
                class_instance_map = InstanceTypeMap()
                if symbol.enclosing_class and parsed.language_id == LanguageID.PYTHON:
                    class_instance_map = self._build_class_instance_map(
                        symbol.enclosing_class, parsed, module, import_map
                    )
                func_instance_map = InstanceTypeMap()
                if parsed.language_id == LanguageID.PYTHON:
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
        return import_map

    def _parse_python_imports(self, parsed: ParsedFile, module: str, import_map: LocalImportMap) -> None:
        src = parsed.source
        for stmt in find_all(parsed.root_node, {"import_statement", "import_from_statement"}):
            if stmt.type == "import_statement":
                for name_node in stmt.children_by_field_name("name"):
                    self._handle_python_import_name(name_node, src, import_map, from_module=None)
            else:
                module_name_node = stmt.child_by_field_name("module_name")
                base_module = (
                    self._python_module_ref_text(module_name_node, src, module)
                    if module_name_node is not None
                    else module
                )
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

    def _python_module_ref_text(self, node: Node, src: bytes, current_module: str) -> str:
        if node.type == "relative_import":
            dots = 0
            suffix: str | None = None
            for child in node.children:
                if child.type == "import_prefix":
                    dots = node_text(child, src).count(".")
                elif child.type == "dotted_name":
                    suffix = node_text(child, src)
            return self._resolve_relative_module(current_module, dots, suffix)
        return node_text(node, src)

    @staticmethod
    def _resolve_relative_module(current_module: str, dots: int, suffix: str | None) -> str:
        parts = current_module.split(".") if current_module else []
        package_parts = parts[:-1]
        levels_up = max(dots - 1, 0)
        if levels_up:
            package_parts = package_parts[: max(len(package_parts) - levels_up, 0)]
        base = ".".join(package_parts)
        if suffix:
            return f"{base}.{suffix}" if base else suffix
        return base

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

    # -- Instance bindings (Python only) --------------------------------- #
    def _resolve_reference_chain(self, segments: list[str] | None, module: str, import_map: LocalImportMap) -> str | None:
        if not segments:
            return None
        root, *rest = segments
        resolved_root = import_map.resolve(root)
        if resolved_root is None:
            resolved_root = self.symbol_table.resolve_in_module(module, root)
        if resolved_root is None:
            return None
        return ".".join([resolved_root, *rest]) if rest else resolved_root

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
                if target is None or value is None or value.type != CALL_NODE_TYPE[parsed.language_id]:
                    continue
                target_segments = flatten_reference_chain(target, parsed.source, parsed.language_id)
                if not target_segments or len(target_segments) != 2 or target_segments[0] not in self_tokens:
                    continue
                ctor = value.child_by_field_name("function")
                ctor_segments = (
                    flatten_reference_chain(ctor, parsed.source, parsed.language_id) if ctor is not None else None
                )
                resolved_class = self._resolve_reference_chain(ctor_segments, module, import_map)
                if self._is_known_class(resolved_class):
                    instance_map.bind(".".join(target_segments), resolved_class)
        return instance_map

    def _is_known_class(self, qualified_name: str | None) -> bool:
        if not qualified_name:
            return False
        symbol = self.symbol_table.get(qualified_name)
        return symbol is not None and symbol.kind == "class"

    def _build_function_instance_map(
        self, def_node: Node, parsed: ParsedFile, module: str, import_map: LocalImportMap
    ) -> InstanceTypeMap:
        instance_map = InstanceTypeMap()
        assign_type = ASSIGNMENT_NODE_TYPE.get(parsed.language_id)
        if assign_type is None:
            return instance_map
        for assign in iter_scoped_nodes(def_node, {assign_type}, parsed.language_id):
            target = assign.child_by_field_name("left")
            value = assign.child_by_field_name("right")
            if target is None or value is None or target.type != "identifier" or value.type != CALL_NODE_TYPE[parsed.language_id]:
                continue
            var_name = node_text(target, parsed.source)
            ctor = value.child_by_field_name("function")
            ctor_segments = (
                flatten_reference_chain(ctor, parsed.source, parsed.language_id) if ctor is not None else None
            )
            resolved_class = self._resolve_reference_chain(ctor_segments, module, import_map)
            if self._is_known_class(resolved_class):
                instance_map.bind(var_name, resolved_class)
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
            func_node = call_node.child_by_field_name("function")
            if func_node is None:
                continue
            segments = flatten_reference_chain(func_node, parsed.source, lang)
            if not segments:
                continue
            target = self._resolve_segments(
                segments, module, enclosing_class, import_map, class_instance_map, func_instance_map, self_tokens
            )
            if target is None:
                continue
            if target not in self.graph:
                self.graph.add_node(target, external=target not in self.symbol_table)
            self.graph.add_edge(caller_qname, target, relation="CALLS")

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
                return f"{enclosing_class}.{method}"
            return None

        candidate = func_instance_map.resolve(receiver_key) or class_instance_map.resolve(receiver_key)
        if candidate:
            return f"{candidate}.{method}"

        resolved_receiver = self._resolve_reference_chain(receiver_segments, module, import_map)
        if resolved_receiver:
            return f"{resolved_receiver}.{method}"
        return None
