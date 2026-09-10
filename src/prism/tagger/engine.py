"""Stage 3: Deterministic Tag Grounding Engine.

Evaluates the rules in `prism.tagger.rules` against every function/method
subtree collected by the `ConcreteGraphBuilder`, producing the bipartite
matrix `M` (as a `qualified_name -> set[tag]` mapping, and mirrored onto the
`tags` node attribute of `G_C` for convenience).
"""
from __future__ import annotations

import os

from tree_sitter import Node

from prism.graph.call_site import has_dynamic_hazard_construct
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.parser.lang_config import (
    ASSIGNMENT_NODE_TYPE,
    CALL_NODE_TYPE,
    CATCH_NODE_TYPES,
    DECORATED_WRAPPER_TYPES,
    RAISE_NODE_TYPE,
    RETURN_STATEMENT_NODE_TYPE,
    SELF_TOKEN_TEXT,
    TRY_NODE_TYPES,
    call_callee_segments,
    find_all,
    flatten_reference_chain,
    iter_scoped_nodes,
)
from prism.parser.tree_sitter_loader import LanguageID, ParsedFile, node_text
from prism.tagger.rules import (
    AUTH_GUARD_RULE,
    CALL_SINK_RULES,
    DECORATOR_RULES,
    DYNAMIC_ATTRIBUTE_TAG,
    DYNAMIC_HAZARD_TAG,
    ENTRYPOINT_FILENAMES,
    ENTRYPOINT_PATH_SEGMENTS,
    ENTRYPOINT_TAG,
    ERROR_HANDLER_TAG,
    GO_ERROR_CHECK_PATTERN,
    IO_SINK_TAG,
    IO_SINK_TEXT_PATTERNS,
    PROPERTY_DECORATOR_PATTERNS,
    PROPERTY_TAG,
    PURE_TRANSFORM_TAG,
    STATE_MUTATION_TAG,
    import_roots,
)

_DUNDER_DICT_ATTR = "__dict__"


class TaggingEngine:
    """Computes `sigma(u)` for every concrete-graph node `u`."""

    def tag_symbol(self, def_node: Node, parsed: ParsedFile) -> set[str]:
        lang = parsed.language_id
        tags: set[str] = set()

        decorator_texts = self._collect_decorator_texts(def_node, parsed)
        for rule in DECORATOR_RULES:
            if any(pattern in text for text in decorator_texts for pattern in rule.decorator_patterns):
                tags.add(rule.tag)
        if any(pattern in text for text in decorator_texts for pattern in PROPERTY_DECORATOR_PATTERNS):
            tags.add(PROPERTY_TAG)

        roots = import_roots(self._collect_import_texts(parsed))
        self_tokens = SELF_TOKEN_TEXT[lang]

        call_type = CALL_NODE_TYPE[lang]
        for call_node in iter_scoped_nodes(def_node, {call_type}, lang):
            segments = call_callee_segments(call_node, parsed.source, lang)
            if not segments:
                continue
            method_name = segments[-1]
            for rule in CALL_SINK_RULES:
                if method_name not in rule.method_patterns:
                    continue
                if not rule.require_import or (roots & {t.lower() for t in rule.import_triggers}):
                    tags.add(rule.tag)
            if method_name in AUTH_GUARD_RULE.call_name_patterns:
                tags.add(AUTH_GUARD_RULE.tag)
            # `setattr(self, name, value)` - a dynamically-named attribute
            # `ConcreteGraphBuilder._collect_attribute_definitions`'s
            # literal `self.<name> = ...` match can never index (Issue #16).
            if (
                method_name == "setattr"
                and len(segments) == 1
                and self._first_call_arg_is_self(call_node, parsed, self_tokens)
            ):
                tags.add(DYNAMIC_ATTRIBUTE_TAG)

        raise_type = RAISE_NODE_TYPE.get(lang)
        if raise_type:
            for raise_node in iter_scoped_nodes(def_node, {raise_type}, lang):
                exc_name = self._extract_exception_name(raise_node, parsed)
                if exc_name and any(p in exc_name for p in AUTH_GUARD_RULE.exception_name_patterns):
                    tags.add(AUTH_GUARD_RULE.tag)

        assign_type = ASSIGNMENT_NODE_TYPE.get(lang)
        if assign_type:
            for assign in iter_scoped_nodes(def_node, {assign_type}, lang):
                target = assign.child_by_field_name("left")
                if target is None:
                    continue
                segments = flatten_reference_chain(target, parsed.source, lang)
                if segments and len(segments) >= 2 and segments[0] in self_tokens:
                    tags.add(STATE_MUTATION_TAG)
                elif self._is_dunder_dict_subscript_target(target, parsed, lang, self_tokens):
                    # `self.__dict__[key] = value` - the other common
                    # dynamically-named-attribute idiom (Issue #16),
                    # structurally a subscript assignment rather than a
                    # `self.<name> = ...` chain, so it needs its own check
                    # (`flatten_reference_chain` above only ever matches a
                    # literal attribute chain, never a subscript target).
                    tags.add(DYNAMIC_ATTRIBUTE_TAG)

        if has_dynamic_hazard_construct(def_node, parsed):
            tags.add(DYNAMIC_HAZARD_TAG)

        return tags

    @staticmethod
    def _first_call_arg_is_self(call_node: Node, parsed: ParsedFile, self_tokens: set[str]) -> bool:
        args_node = call_node.child_by_field_name("arguments") or call_node.child_by_field_name("argument_list")
        if args_node is None or not args_node.named_children:
            return False
        first_arg = args_node.named_children[0]
        return node_text(first_arg, parsed.source) in self_tokens

    @staticmethod
    def _is_dunder_dict_subscript_target(target: Node, parsed: ParsedFile, lang: str, self_tokens: set[str]) -> bool:
        # `__dict__` is a Python-only concept.
        if lang != LanguageID.PYTHON or target.type != "subscript":
            return False
        base = target.child_by_field_name("value")
        if base is None:
            return False
        base_segments = flatten_reference_chain(base, parsed.source, lang)
        return bool(base_segments) and len(base_segments) == 2 and base_segments[0] in self_tokens and base_segments[1] == _DUNDER_DICT_ATTR

    def tag_graph(self, builder: ConcreteGraphBuilder) -> dict[str, set[str]]:
        """Populate `M` for every function/method the builder discovered."""
        matrix: dict[str, set[str]] = {}
        for symbol in builder.symbol_table:
            if symbol.kind not in ("function", "method"):
                continue
            def_node = builder.def_node(symbol.qualified_name)
            parsed = builder.parsed_file(symbol.file)
            if def_node is None or parsed is None:
                continue
            tags = self.tag_symbol(def_node, parsed)
            matrix[symbol.qualified_name] = tags
            if symbol.qualified_name in builder.graph:
                builder.graph.nodes[symbol.qualified_name]["tags"] = tags
        self._tag_sentinel_nodes(builder, matrix)
        self._apply_topological_role_tags(builder, matrix)
        return matrix

    def _apply_topological_role_tags(self, builder: ConcreteGraphBuilder, matrix: dict[str, set[str]]) -> None:
        """Item 8 (second post-implementation audit): Topological Graph
        Role Inference - supplements the static, per-symbol rules above
        (decorator text, call-sink method names, ...) with signals from
        the symbol's *position in the call graph* and a lightweight
        source-text scan, purely additive (never removes a tag the rules
        above already assigned).
        """
        g_c = builder.calls_graph
        for symbol in builder.symbol_table:
            if symbol.kind not in ("function", "method") or symbol.qualified_name not in g_c:
                continue
            qname = symbol.qualified_name
            def_node = builder.def_node(qname)
            parsed = builder.parsed_file(symbol.file)
            if def_node is None or parsed is None:
                continue
            in_degree = g_c.in_degree(qname)
            out_degree = g_c.out_degree(qname)
            new_tags: set[str] = set()

            if in_degree == 0 and out_degree > 0 and self._is_entrypoint_module(symbol.file):
                new_tags.add(ENTRYPOINT_TAG)

            if in_degree > 0 and out_degree == 0 and self._has_value_return(def_node):
                new_tags.add(PURE_TRANSFORM_TAG)

            source_text = self._node_source_text(def_node, parsed)
            if any(pattern in source_text for pattern in IO_SINK_TEXT_PATTERNS):
                new_tags.add(IO_SINK_TAG)

            if self._is_error_handler(def_node, parsed, source_text):
                new_tags.add(ERROR_HANDLER_TAG)

            if not new_tags:
                continue
            tags = matrix.setdefault(qname, set())
            tags |= new_tags
            matrix[qname] = tags
            if qname in builder.graph:
                builder.graph.nodes[qname]["tags"] = tags

    @staticmethod
    def _is_entrypoint_module(file_path: str) -> bool:
        filename = os.path.basename(file_path)
        if filename in ENTRYPOINT_FILENAMES:
            return True
        parts = os.path.normpath(file_path).split(os.sep)
        return any(segment in ENTRYPOINT_PATH_SEGMENTS for segment in parts)

    @staticmethod
    def _node_source_text(def_node: Node, parsed: ParsedFile) -> str:
        return node_text(def_node, parsed.source)

    @staticmethod
    def _has_value_return(def_node: Node) -> bool:
        """At least one `return <expr>` (not a bare `return`/implicit
        end-of-function fall-through) anywhere in `def_node`'s own body -
        `RETURN_STATEMENT_NODE_TYPE` is identical across every supported
        grammar, so this needs no per-language branching."""
        for ret in find_all(def_node, {RETURN_STATEMENT_NODE_TYPE}):
            if ret.named_child_count > 0:
                return True
        return False

    @staticmethod
    def _is_error_handler(def_node: Node, parsed: ParsedFile, source_text: str) -> bool:
        lang = parsed.language_id
        has_try = bool(find_all(def_node, TRY_NODE_TYPES.get(lang, set()) | CATCH_NODE_TYPES.get(lang, set())))
        has_go_err_check = lang == LanguageID.GO and GO_ERROR_CHECK_PATTERN in source_text
        if not (has_try or has_go_err_check):
            return False
        # "logs and returns early, or re-raises" - a real error-handling
        # shape, not merely a try/except-shaped block with an empty or
        # unrelated body (a bare `except: pass` swallow, for instance).
        has_return = bool(find_all(def_node, {RETURN_STATEMENT_NODE_TYPE}))
        raise_type = RAISE_NODE_TYPE.get(lang)
        has_raise = raise_type is not None and bool(find_all(def_node, {raise_type}))
        return has_return or has_raise

    def _tag_sentinel_nodes(self, builder: ConcreteGraphBuilder, matrix: dict[str, set[str]]) -> None:
        """Conservative Tag Unioning (Task 1.3): an `UnresolvedPolymorphicNode`
        is not itself a real symbol - `tag_graph`'s main loop above (which
        only ever walks `builder.symbol_table`) never reaches it - so its
        own tag set is computed here instead, as the union of every
        candidate's already-computed tags: Tags(Unresolved) = union over
        C in Candidates of Tags(C). A `DynamicEdgeSentinel` has no
        candidates to union at all - it always carries exactly
        `DYNAMIC_HAZARD_TAG`, the same conservative-worst-case signal.
        """
        for node, data in builder.graph.nodes(data=True):
            sentinel_type = data.get("sentinel_type")
            if sentinel_type == "unresolved_polymorphic":
                union: set[str] = set()
                for candidate in data.get("candidates", []):
                    union |= matrix.get(candidate, set())
            elif sentinel_type == "dynamic_edge":
                union = {DYNAMIC_HAZARD_TAG}
            else:
                continue
            matrix[node] = union
            builder.graph.nodes[node]["tags"] = union

    # -- helpers ---------------------------------------------------------- #
    _ANNOTATION_CONTAINER_TYPES = {"modifiers", "attribute_list"}
    _ANNOTATION_NODE_TYPES = {"annotation", "marker_annotation", "attribute"}

    def _collect_decorator_texts(self, def_node: Node, parsed: ParsedFile) -> list[str]:
        lang = parsed.language_id
        if lang in (LanguageID.JAVA, LanguageID.CSHARP):
            return self._collect_annotation_texts(def_node, parsed)

        wrapper_types = DECORATED_WRAPPER_TYPES.get(lang, set())
        if def_node.parent is None or def_node.parent.type not in wrapper_types:
            return []
        texts: list[str] = []
        for deco in def_node.parent.children:
            if deco.type != "decorator":
                continue
            target = None
            for c in deco.children:
                if c.type != "@":
                    target = c
                    break
            if target is None:
                continue
            if target.type == CALL_NODE_TYPE.get(lang):
                target = target.child_by_field_name("function") or target
            segments = flatten_reference_chain(target, parsed.source, lang)
            texts.append(".".join(segments) if segments else node_text(target, parsed.source))
        return texts

    def _collect_annotation_texts(self, def_node: Node, parsed: ParsedFile) -> list[str]:
        """Java annotations (`@PreAuthorize`) and C# attributes
        (`[Authorize]`) are inline children of the definition itself - a
        `modifiers`/`attribute_list` node holding one or more
        `annotation`/`marker_annotation`/`attribute` nodes - not a separate
        wrapper node around the definition the way Python's
        `@decorator\\ndef f()` is. `attribute_list` can itself hold several
        attributes (`[HttpPost, Authorize]`), each its own `attribute` node.
        """
        texts: list[str] = []
        for container in def_node.children:
            if container.type not in self._ANNOTATION_CONTAINER_TYPES:
                continue
            for node in container.children:
                if node.type not in self._ANNOTATION_NODE_TYPES:
                    continue
                name_node = node.child_by_field_name("name")
                texts.append(node_text(name_node if name_node is not None else node, parsed.source))
        return texts

    def _extract_exception_name(self, raise_node: Node, parsed: ParsedFile) -> str | None:
        lang = parsed.language_id
        call_type = CALL_NODE_TYPE.get(lang)
        for child in raise_node.children:
            if child.type in ("raise", "from", "throw"):
                continue
            if child.type == call_type:
                segments = call_callee_segments(child, parsed.source, lang)
                return segments[-1] if segments else None
            if child.type == "object_creation_expression":
                # Java/C#: `throw new PermissionDeniedException("...")` -
                # the exception type name lives in the "type" field, not a
                # call's "function" field (this isn't a call node at all).
                type_node = child.child_by_field_name("type")
                if type_node is not None:
                    return node_text(type_node, parsed.source)
                continue
            segments = flatten_reference_chain(child, parsed.source, lang)
            if segments:
                return segments[-1]
        return None

    def _collect_import_texts(self, parsed: ParsedFile) -> set[str]:
        lang = parsed.language_id
        src = parsed.source
        texts: set[str] = set()
        if lang == LanguageID.PYTHON:
            for stmt in find_all(parsed.root_node, {"import_statement", "import_from_statement"}):
                if stmt.type == "import_statement":
                    for name_node in stmt.children_by_field_name("name"):
                        texts.add(self._python_import_name_text(name_node, src))
                else:
                    module_name_node = stmt.child_by_field_name("module_name")
                    if module_name_node is not None:
                        texts.add(node_text(module_name_node, src))
        elif lang in (LanguageID.JAVASCRIPT, LanguageID.TYPESCRIPT, LanguageID.TSX):
            for stmt in find_all(parsed.root_node, {"import_statement"}):
                source_node = stmt.child_by_field_name("source")
                if source_node is not None:
                    texts.add(node_text(source_node, src).strip("'\""))
        elif lang == LanguageID.GO:
            for spec in find_all(parsed.root_node, {"import_spec"}):
                for c in spec.children:
                    if c.type == "interpreted_string_literal":
                        texts.add(node_text(c, src).strip('"'))
        elif lang == LanguageID.JAVA:
            for stmt in find_all(parsed.root_node, {"import_declaration"}):
                for c in stmt.children:
                    if c.type == "scoped_identifier":
                        texts.add(node_text(c, src))
        elif lang == LanguageID.CSHARP:
            for stmt in find_all(parsed.root_node, {"using_directive"}):
                for c in stmt.children:
                    if c.type in ("qualified_name", "identifier"):
                        texts.add(node_text(c, src))
        return texts

    @staticmethod
    def _python_import_name_text(name_node: Node, src: bytes) -> str:
        if name_node.type == "aliased_import":
            target = name_node.child_by_field_name("name")
            return node_text(target, src) if target is not None else node_text(name_node, src)
        return node_text(name_node, src)
