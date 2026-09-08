"""Behavioral contract extraction: deterministic, AST-derived metadata for
every function/method Prism indexes - signatures, purity, thrown
exceptions, I/O effects, cyclomatic complexity, and doc/visibility/
deprecation metadata - computed once per symbol during indexing so a
context-slicing consumer (`prism.serializers.markdown`) can hand an LLM a
compact contract instead of a symbol's full body for anything beyond the
query's own target (see that module's docstring).

Mirrors `prism.tagger.engine.TaggingEngine`'s own shape deliberately (same
per-symbol `extract`/per-repo `extract_all` split, same lang_config helper
reuse, same "pure AST predicate, no type inference" philosophy) rather than
inventing a second, different pattern for what is structurally the same
kind of pass.

**Language coverage**: Python and JavaScript/TypeScript/TSX get real,
per-construct-verified extraction (every node type this module reads was
confirmed against a real parse - see the module's own test fixtures in
`tests/test_behavioral_contracts.py`). Go/Java/C# get whatever the shared
`lang_config` tables already cover for a given field (e.g. `is_async`,
purity's self/this-mutation check) and leave the rest at safe defaults
(empty effects/exceptions, complexity still counts real branch nodes) -
called out here rather than silently claimed as equally precise, the same
policy `concrete_builder`'s own module docstring already states for call
linking.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from tree_sitter import Node

from prism.parser.lang_config import (
    ASSIGNMENT_NODE_TYPE,
    ASYNC_KEYWORD_NODE_TYPES,
    CALL_NODE_TYPE,
    CATCH_NODE_TYPES,
    CONDITIONAL_NODE_TYPES,
    DECORATED_WRAPPER_TYPES,
    EXPORT_WRAPPER_TYPES,
    GLOBAL_NONLOCAL_STATEMENT_TYPES,
    LOOP_NODE_TYPES,
    SELF_TOKEN_TEXT,
    TERNARY_NODE_TYPES,
    call_callee_segments,
    flatten_reference_chain,
    iter_scoped_nodes,
)
from prism.parser.tree_sitter_loader import LanguageID, ParsedFile, node_text
from prism.graph.call_site import HofCallbackContract, detect_hof_callback_params
from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.effects_rules import classify_effects

DOC_SUMMARY_MAX_CHARS = 120


@dataclass(frozen=True)
class Parameter:
    name: str
    type: str | None = None
    default: str | None = None

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type, "default": self.default}

    @classmethod
    def from_dict(cls, d: dict) -> "Parameter":
        return cls(name=d["name"], type=d.get("type"), default=d.get("default"))

    def render(self) -> str:
        """`name: type` / `name: type = default` / bare `name` - the
        compact, single-line form the YAML contract block renders params
        as (see serializers/markdown.py).
        """
        text = self.name
        if self.type:
            text += f": {self.type}"
        if self.default is not None:
            text += f" = {self.default}"
        return text


@dataclass
class BehavioralContract:
    qualified_name: str
    params: list[Parameter] = field(default_factory=list)
    return_type: str | None = None
    is_async: bool = False
    purity: str = "impure"  # "pure" | "impure"
    state_mutations: list[str] = field(default_factory=list)
    thrown_exceptions: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    cyclomatic_complexity: int = 1
    doc_summary: str | None = None
    visibility: str = "public"  # "public" | "private" | "exported"
    is_deprecated: bool = False
    #: Higher-Order Function & Callback Signature Contracts
    #: (`prism.graph.call_site.detect_hof_callback_params`) - one entry
    #: per callback-typed parameter this symbol declares, since a
    #: callback parameter never produces a `CALLS` edge of its own (the
    #: concrete function is supplied by the caller) and would otherwise be
    #: invisible beyond its bare type name.
    hof_callbacks: list["HofCallbackContract"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "qualified_name": self.qualified_name,
            "params": [p.to_dict() for p in self.params],
            "return_type": self.return_type,
            "is_async": self.is_async,
            "purity": self.purity,
            "state_mutations": self.state_mutations,
            "thrown_exceptions": self.thrown_exceptions,
            "effects": self.effects,
            "cyclomatic_complexity": self.cyclomatic_complexity,
            "doc_summary": self.doc_summary,
            "visibility": self.visibility,
            "is_deprecated": self.is_deprecated,
            "hof_callbacks": [c.to_dict() for c in self.hof_callbacks],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BehavioralContract":
        return cls(
            qualified_name=d["qualified_name"],
            params=[Parameter.from_dict(p) for p in d.get("params", [])],
            return_type=d.get("return_type"),
            is_async=d.get("is_async", False),
            purity=d.get("purity", "impure"),
            state_mutations=list(d.get("state_mutations", [])),
            thrown_exceptions=list(d.get("thrown_exceptions", [])),
            effects=list(d.get("effects", [])),
            cyclomatic_complexity=d.get("cyclomatic_complexity", 1),
            doc_summary=d.get("doc_summary"),
            visibility=d.get("visibility", "public"),
            is_deprecated=d.get("is_deprecated", False),
            hof_callbacks=[HofCallbackContract(**c) for c in d.get("hof_callbacks", [])],
        )


# I/O-shaped effects that disqualify a symbol from "pure" regardless of
# state mutation - matches BehavioralContract's own docstring definition
# ("does not mutate global/closure variables, modify `this`, or execute
# I/O"). ASSERTS and ASYNC_WAIT are deliberately excluded: an assertion or
# an awaited call is neither a mutation nor I/O by itself (the thing being
# awaited might be, but that's a transitive property this AST-only,
# non-type-inferring pass doesn't attempt to resolve - the same
# no-transitive-analysis limitation `#auth_guard` tagging already
# documents for itself).
_IO_EFFECTS = frozenset({"DOM_MUTATION", "DOM_READ", "DISK_IO", "NETWORK_HTTP"})

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_DEPRECATED_RE = re.compile(r"@deprecated\b|\bdeprecated\b", re.IGNORECASE)
_PYTHON_DOCSTRING_QUOTES_RE = re.compile(r'^[a-zA-Z]{0,2}(?:"""|\'\'\'|"|\')|(?:"""|\'\'\'|"|\')$')

# In-place container mutators, lowercased - covers Python list/dict/set
# methods and their JS/TS Array/Map/Set/DOM-storage equivalents. A method
# invoked on a `self.*`/`this.*` receiver counts as a state mutation of
# that attribute even though no `=` appears anywhere in the call.
_MUTATING_METHOD_NAMES = frozenset({
    "append", "extend", "insert", "remove", "pop", "clear", "update", "add", "discard",
    "sort", "reverse", "setdefault", "popitem",
    "push", "shift", "unshift", "splice", "delete", "set", "fill", "copywithin",
})


class ContractExtractor:
    """Computes `sigma_contract(u)` for every function/method `G_C` node,
    the behavioral-contract counterpart to `TaggingEngine.tag_graph`."""

    def extract_symbol(self, def_node: Node, parsed: ParsedFile, enclosing_class: str | None, qualified_name: str) -> BehavioralContract:
        lang = parsed.language_id
        params = self._extract_params(def_node, parsed)
        return_type = self._extract_return_type(def_node, parsed)
        is_async = self._is_async(def_node, parsed)
        effects, thrown = classify_effects(def_node, parsed)
        state_mutations = self._state_mutations(def_node, parsed)
        complexity = self._cyclomatic_complexity(def_node, lang)
        doc_summary = self._doc_summary(def_node, parsed)
        visibility = self._visibility(def_node, parsed, enclosing_class, qualified_name)
        is_deprecated = self._is_deprecated(def_node, parsed, doc_summary)

        has_mutation = bool(state_mutations)
        has_global = self._has_global_nonlocal(def_node, lang)
        purity = "impure" if (has_mutation or has_global or (_IO_EFFECTS & set(effects))) else "pure"
        hof_callbacks = detect_hof_callback_params(def_node, parsed)

        return BehavioralContract(
            qualified_name=qualified_name,
            params=params,
            return_type=return_type,
            is_async=is_async,
            purity=purity,
            state_mutations=state_mutations,
            thrown_exceptions=thrown,
            effects=effects,
            cyclomatic_complexity=complexity,
            doc_summary=doc_summary,
            visibility=visibility,
            is_deprecated=is_deprecated,
            hof_callbacks=hof_callbacks,
        )

    def extract_all(self, builder: ConcreteGraphBuilder) -> dict[str, BehavioralContract]:
        contracts: dict[str, BehavioralContract] = {}
        for symbol in builder.symbol_table:
            if symbol.kind not in ("function", "method"):
                continue
            def_node = builder.def_node(symbol.qualified_name)
            parsed = builder.parsed_file(symbol.file)
            if def_node is None or parsed is None:
                continue
            contract = self.extract_symbol(def_node, parsed, symbol.enclosing_class, symbol.qualified_name)
            contracts[symbol.qualified_name] = contract
            if symbol.qualified_name in builder.graph:
                builder.graph.nodes[symbol.qualified_name]["contract"] = contract
        return contracts

    # -- signature ---------------------------------------------------- #
    def _extract_params(self, def_node: Node, parsed: ParsedFile) -> list[Parameter]:
        lang = parsed.language_id
        params_node = def_node.child_by_field_name("parameters")
        if params_node is None:
            return []
        src = parsed.source
        self_tokens = SELF_TOKEN_TEXT.get(lang, set())
        result: list[Parameter] = []
        for child in params_node.children:
            param = self._one_param(child, src, lang)
            if param is None:
                continue
            if param.name in self_tokens or param.name == "self":
                continue
            result.append(param)
        return result

    @staticmethod
    def _one_param(node: Node, src: bytes, lang: str) -> Parameter | None:
        if node.type == "identifier":
            return Parameter(name=node_text(node, src))
        if node.type in ("typed_parameter",):  # Python: `name: type`
            name_node = next((c for c in node.children if c.type == "identifier"), None)
            type_node = node.child_by_field_name("type")
            if name_node is None:
                return None
            return Parameter(name=node_text(name_node, src), type=node_text(type_node, src) if type_node else None)
        if node.type == "default_parameter":  # Python: `name = value`
            name_node = node.child_by_field_name("name")
            value_node = node.child_by_field_name("value")
            if name_node is None:
                return None
            return Parameter(name=node_text(name_node, src), default=node_text(value_node, src) if value_node else None)
        if node.type == "typed_default_parameter":  # Python: `name: type = value`
            name_node = node.child_by_field_name("name")
            type_node = node.child_by_field_name("type")
            value_node = node.child_by_field_name("value")
            if name_node is None:
                return None
            return Parameter(
                name=node_text(name_node, src),
                type=node_text(type_node, src) if type_node else None,
                default=node_text(value_node, src) if value_node else None,
            )
        if node.type in ("list_splat_pattern", "dictionary_splat_pattern"):  # *args / **kwargs
            return Parameter(name=node_text(node, src))
        if node.type in ("required_parameter", "optional_parameter"):  # TS/JS
            pattern = node.child_by_field_name("pattern")
            type_node = node.child_by_field_name("type")
            value_node = node.child_by_field_name("value")
            if pattern is None:
                return None
            name = node_text(pattern, src)
            type_text = None
            if type_node is not None:
                # `type_node` is a `type_annotation` (`: number | null`) -
                # strip the leading colon for a bare type expression.
                type_text = node_text(type_node, src).lstrip(":").strip()
            if node.type == "optional_parameter" and type_text is None:
                type_text = None
            return Parameter(name=name, type=type_text, default=node_text(value_node, src) if value_node else None)
        if node.type in ("parameter_declaration", "parameter"):  # Go/Java/C#: best-effort, name + raw type text
            name_node = node.child_by_field_name("name")
            type_node = node.child_by_field_name("type")
            if name_node is None:
                return None
            return Parameter(name=node_text(name_node, src), type=node_text(type_node, src) if type_node else None)
        return None

    def _extract_return_type(self, def_node: Node, parsed: ParsedFile) -> str | None:
        return_type_node = def_node.child_by_field_name("return_type")
        if return_type_node is not None:
            text = node_text(return_type_node, parsed.source)
            return text.lstrip(":").strip() or None
        return None

    def _is_async(self, def_node: Node, parsed: ParsedFile) -> bool:
        lang = parsed.language_id
        async_types = ASYNC_KEYWORD_NODE_TYPES.get(lang, set())
        if not async_types:
            return False
        wrapper_types = DECORATED_WRAPPER_TYPES.get(lang, set())
        candidates = [def_node]
        if def_node.parent is not None and def_node.parent.type in wrapper_types:
            candidates.append(def_node.parent)
        for candidate in candidates:
            for child in candidate.children:
                if child.type in async_types:
                    return True
        return False

    # -- purity / state mutation ---------------------------------------- #
    _MUTATING_GLOBAL_ROOTS = frozenset({"localStorage", "sessionStorage", "globalThis"})

    def _state_mutations(self, def_node: Node, parsed: ParsedFile) -> list[str]:
        lang = parsed.language_id
        assign_type = ASSIGNMENT_NODE_TYPE.get(lang)
        self_tokens = SELF_TOKEN_TEXT.get(lang, set())
        mutations: set[str] = set()

        if assign_type:
            for assign in iter_scoped_nodes(def_node, {assign_type}, lang):
                target = assign.child_by_field_name("left")
                if target is None:
                    continue
                # `self.cache[key] = value` / `this.cache[key] = value` -
                # the assignment target is a subscript/index expression
                # wrapping the real attribute chain, not the chain itself;
                # unwrap to the base object before flattening. Python's
                # `subscript` carries no field name for its base (positional
                # first child); JS/TS's `subscript_expression` does
                # ("object").
                if target.type in ("subscript", "subscript_expression", "index_expression"):
                    target = target.child_by_field_name("object") or (target.children[0] if target.children else None)
                if target is None:
                    continue
                segments = flatten_reference_chain(target, parsed.source, lang)
                if segments and len(segments) >= 2 and segments[0] in self_tokens:
                    mutations.add(".".join(segments[:2]))
                elif segments and len(segments) >= 1 and segments[0] in self._MUTATING_GLOBAL_ROOTS:
                    mutations.add(segments[0])

        call_type = CALL_NODE_TYPE.get(lang)
        call_type_calls = iter_scoped_nodes(def_node, {call_type}, lang) if call_type else []
        for call_node in call_type_calls:
            segments = call_callee_segments(call_node, parsed.source, lang)
            if not segments:
                continue
            if segments[0] in self._MUTATING_GLOBAL_ROOTS:
                mutations.add(segments[0])
                continue
            # A mutating container method (`self.items.append(x)`,
            # `this.cache.delete(k)`) invoked on a `self.*`/`this.*`
            # receiver mutates that attribute just as much as a direct
            # assignment does, even though the assignment-shape check
            # above never sees it (no `=` anywhere in the statement).
            if len(segments) >= 3 and segments[0] in self_tokens and segments[-1].lower() in _MUTATING_METHOD_NAMES:
                mutations.add(".".join(segments[:2]))

        return sorted(mutations)

    def _has_global_nonlocal(self, def_node: Node, lang: str) -> bool:
        types = GLOBAL_NONLOCAL_STATEMENT_TYPES.get(lang, set())
        if not types:
            return False
        return bool(iter_scoped_nodes(def_node, types, lang))

    # -- complexity ------------------------------------------------------ #
    def _cyclomatic_complexity(self, def_node: Node, lang: str) -> int:
        branch_types = (
            CONDITIONAL_NODE_TYPES.get(lang, set())
            | LOOP_NODE_TYPES.get(lang, set())
            | CATCH_NODE_TYPES.get(lang, set())
            | TERNARY_NODE_TYPES.get(lang, set())
        )
        if not branch_types:
            return 1
        return 1 + len(iter_scoped_nodes(def_node, branch_types, lang))

    # -- doc / visibility / deprecation ---------------------------------- #
    def _doc_summary(self, def_node: Node, parsed: ParsedFile) -> str | None:
        lang = parsed.language_id
        raw = self._raw_doc_text(def_node, parsed, lang)
        if not raw:
            return None
        raw = raw.strip()
        if lang == LanguageID.PYTHON:
            # Python string literal delimiters: an optional prefix
            # (r/u/b/f, any case) then a triple- or single-quote, matched
            # and stripped from both ends - a plain `.strip('"\'')` would
            # also eat quote characters that are part of the actual prose.
            raw = _PYTHON_DOCSTRING_QUOTES_RE.sub("", raw)
        else:
            raw = raw.strip("/*").strip()
        # Strip JSDoc leading `*` line-continuations (a no-op for Python,
        # which never has them) before sentence-splitting.
        lines = [ln.strip().lstrip("*").strip() for ln in raw.splitlines()]
        text = " ".join(ln for ln in lines if ln).strip()
        if not text:
            return None
        sentences = _SENTENCE_SPLIT_RE.split(text)
        first = sentences[0].strip() if sentences else text
        if len(first) > DOC_SUMMARY_MAX_CHARS:
            first = first[: DOC_SUMMARY_MAX_CHARS - 1].rstrip() + "…"
        return first or None

    @staticmethod
    def _raw_doc_text(def_node: Node, parsed: ParsedFile, lang: str) -> str | None:
        src = parsed.source
        if lang == LanguageID.PYTHON:
            body = def_node.child_by_field_name("body")
            if body is None or not body.children:
                return None
            first = body.children[0]
            if first.type == "expression_statement" and first.children and first.children[0].type == "string":
                return node_text(first.children[0], src)
            return None
        if lang in (LanguageID.JAVASCRIPT, LanguageID.TYPESCRIPT, LanguageID.TSX):
            # A JSDoc `/** ... */` comment immediately preceding the
            # definition (or its `export`/decorator wrapper) in the source -
            # tree-sitter exposes comments as ordinary siblings, not
            # attached to the node they document, so this walks the
            # previous sibling chain rather than reading a field.
            anchor = def_node
            wrapper_types = EXPORT_WRAPPER_TYPES.get(lang, set())
            if anchor.parent is not None and anchor.parent.type in wrapper_types:
                anchor = anchor.parent
            prev = anchor.prev_sibling
            if prev is not None and prev.type == "comment":
                text = node_text(prev, src)
                if text.startswith("/**"):
                    return text
            return None
        return None

    def _visibility(self, def_node: Node, parsed: ParsedFile, enclosing_class: str | None, qualified_name: str) -> str:
        lang = parsed.language_id
        simple_name = qualified_name.rsplit(".", 1)[-1]
        if lang == LanguageID.PYTHON:
            # Dunder methods (`__init__`, `__str__`, ...) are public special
            # methods by Python convention, not private, despite the
            # leading underscores a naive prefix check would flag.
            is_dunder = simple_name.startswith("__") and simple_name.endswith("__")
            return "private" if simple_name.startswith("_") and not is_dunder else "public"
        if lang in (LanguageID.JAVASCRIPT, LanguageID.TYPESCRIPT, LanguageID.TSX):
            wrapper_types = EXPORT_WRAPPER_TYPES.get(lang, set())
            if def_node.parent is not None and def_node.parent.type in wrapper_types:
                return "exported"
            if simple_name.startswith("_") or simple_name.startswith("#"):
                return "private"
            return "public"
        if lang == LanguageID.GO:
            return "exported" if simple_name[:1].isupper() else "private"
        if lang in (LanguageID.JAVA, LanguageID.CSHARP):
            for modifiers in def_node.children:
                if modifiers.type != "modifiers":
                    continue
                mod_texts = {node_text(c, parsed.source) for c in modifiers.children}
                if "public" in mod_texts:
                    return "public"
                if "private" in mod_texts:
                    return "private"
                if "protected" in mod_texts:
                    return "private"
            return "private" if lang == LanguageID.JAVA else "public"
        return "public"

    def _is_deprecated(self, def_node: Node, parsed: ParsedFile, doc_summary: str | None) -> bool:
        if doc_summary and _DEPRECATED_RE.search(doc_summary):
            return True
        raw = self._raw_doc_text(def_node, parsed, parsed.language_id)
        if raw and _DEPRECATED_RE.search(raw):
            return True
        lang = parsed.language_id
        if lang == LanguageID.PYTHON:
            wrapper_types = DECORATED_WRAPPER_TYPES.get(lang, set())
            if def_node.parent is not None and def_node.parent.type in wrapper_types:
                for deco in def_node.parent.children:
                    if deco.type == "decorator" and "deprecated" in node_text(deco, parsed.source).lower():
                        return True
        if lang in (LanguageID.JAVA, LanguageID.CSHARP):
            for container in def_node.children:
                if container.type in ("modifiers", "attribute_list"):
                    if "deprecated" in node_text(container, parsed.source).lower():
                        return True
        return False

def compute_contracts(builder: ConcreteGraphBuilder) -> dict[str, BehavioralContract]:
    """Convenience entry point mirroring `TaggingEngine().tag_graph(builder)`
    - the one function most callers actually need."""
    return ContractExtractor().extract_all(builder)
