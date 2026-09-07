"""Stage 3: Deterministic Tag Grounding Engine.

Evaluates the rules in `sce.tagger.rules` against every function/method
subtree collected by the `ConcreteGraphBuilder`, producing the bipartite
matrix `M` (as a `qualified_name -> set[tag]` mapping, and mirrored onto the
`tags` node attribute of `G_C` for convenience).
"""
from __future__ import annotations

from tree_sitter import Node

from sce.graph.concrete_builder import ConcreteGraphBuilder
from sce.parser.lang_config import (
    ASSIGNMENT_NODE_TYPE,
    CALL_NODE_TYPE,
    DECORATED_WRAPPER_TYPES,
    RAISE_NODE_TYPE,
    SELF_TOKEN_TEXT,
    find_all,
    flatten_reference_chain,
    iter_scoped_nodes,
)
from sce.parser.tree_sitter_loader import LanguageID, ParsedFile, node_text
from sce.tagger.rules import (
    AUTH_GUARD_RULE,
    CALL_SINK_RULES,
    DECORATOR_RULES,
    STATE_MUTATION_TAG,
    import_roots,
)


class TaggingEngine:
    """Computes `sigma(u)` for every concrete-graph node `u`."""

    def tag_symbol(self, def_node: Node, parsed: ParsedFile) -> set[str]:
        lang = parsed.language_id
        tags: set[str] = set()

        decorator_texts = self._collect_decorator_texts(def_node, parsed)
        for rule in DECORATOR_RULES:
            if any(pattern in text for text in decorator_texts for pattern in rule.decorator_patterns):
                tags.add(rule.tag)

        roots = import_roots(self._collect_import_texts(parsed))

        call_type = CALL_NODE_TYPE[lang]
        for call_node in iter_scoped_nodes(def_node, {call_type}, lang):
            func_node = call_node.child_by_field_name("function")
            if func_node is None:
                continue
            segments = flatten_reference_chain(func_node, parsed.source, lang)
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

        raise_type = RAISE_NODE_TYPE.get(lang)
        if raise_type:
            for raise_node in iter_scoped_nodes(def_node, {raise_type}, lang):
                exc_name = self._extract_exception_name(raise_node, parsed)
                if exc_name and any(p in exc_name for p in AUTH_GUARD_RULE.exception_name_patterns):
                    tags.add(AUTH_GUARD_RULE.tag)

        assign_type = ASSIGNMENT_NODE_TYPE.get(lang)
        if assign_type:
            self_tokens = SELF_TOKEN_TEXT[lang]
            for assign in iter_scoped_nodes(def_node, {assign_type}, lang):
                target = assign.child_by_field_name("left")
                if target is None:
                    continue
                segments = flatten_reference_chain(target, parsed.source, lang)
                if segments and len(segments) >= 2 and segments[0] in self_tokens:
                    tags.add(STATE_MUTATION_TAG)

        return tags

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
        return matrix

    # -- helpers ---------------------------------------------------------- #
    def _collect_decorator_texts(self, def_node: Node, parsed: ParsedFile) -> list[str]:
        lang = parsed.language_id
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

    def _extract_exception_name(self, raise_node: Node, parsed: ParsedFile) -> str | None:
        lang = parsed.language_id
        call_type = CALL_NODE_TYPE.get(lang)
        for child in raise_node.children:
            if child.type in ("raise", "from"):
                continue
            if child.type == call_type:
                func = child.child_by_field_name("function")
                if func is None:
                    return None
                segments = flatten_reference_chain(func, parsed.source, lang)
                return segments[-1] if segments else node_text(func, parsed.source)
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
        return texts

    @staticmethod
    def _python_import_name_text(name_node: Node, src: bytes) -> str:
        if name_node.type == "aliased_import":
            target = name_node.child_by_field_name("name")
            return node_text(target, src) if target is not None else node_text(name_node, src)
        return node_text(name_node, src)
