"""Contract axis (Option 3): each function's local state-delta, exception and
return contract, plus its route role - a replacement candidate for the
Substance axis in the Turn-1 manifest.

Every field is read from the function's own syntax tree (no type inference):

- `state`: the roots a body mutates - `MUTATES_SELF` (an assignment to
  `self.x` / `this.x` / `self.x[k]`, or a mutating method called on a
  `self` / `this` chain), `MUTATES_ARGS` (the same rooted at a parameter),
  `MUTATES_GLOBAL` (rooted at an imported or module-level name, or a
  `global` / `nonlocal` statement); `READ_ONLY` when none.
- `throws`: the exception named by each `raise` / `throw`, with the HTTP
  status literals found in it (`HTTPException(404)`, `status_code=404`,
  `status.HTTP_404_NOT_FOUND`); `abort(404)` counts as `abort[404]`.
- `ret`: from the return annotation when there is one, otherwise from the
  returned expressions - `BOOL`, `COLLECTION`, `OBJECT`, or `NONE`.
- `entry`: `ROUTE` when `prism.graph.entry_points.find_entry_points` lists
  the function (decorator or call registration).

One-hop delegation (`compute_node_contracts(..., propagate=True)`): a call
that forwards one of the caller's own parameters (or `self`) into a
parameter its resolved callee mutates - by position or keyword - or calls a
self-mutating method on a forwarded parameter, adds the matching flag to the
caller (`route -> crud.create_user(session=session)` makes the route
`MUTATES_ARGS`). Only the callee's local contract is read: one hop, never
transitive.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.parser.lang_config import (
    ASSIGNMENT_NODE_TYPE,
    AUGMENTED_ASSIGNMENT_NODE_TYPE,
    CALL_NODE_TYPE,
    RAISE_NODE_TYPE,
    call_callee_segments,
    flatten_reference_chain,
    iter_scoped_nodes,
)
from prism.parser.tree_sitter_loader import LanguageID

READ_ONLY, MUTATES_SELF, MUTATES_ARGS, MUTATES_GLOBAL = "READ_ONLY", "MUTATES_SELF", "MUTATES_ARGS", "MUTATES_GLOBAL"
RET_NONE, RET_BOOL, RET_COLLECTION, RET_OBJECT = "NONE", "BOOL", "COLLECTION", "OBJECT"

#: Methods that change their receiver: the container verbs
#: `prism.graph.contracts` already treats as mutations, plus persistence verbs.
MUTATING_METHODS = frozenset({
    "append", "extend", "insert", "remove", "pop", "clear", "update", "add", "discard", "sort", "reverse",
    "setdefault", "popitem", "push", "shift", "unshift", "splice", "delete", "set", "fill",
    "save", "commit", "flush", "add_all", "create", "bulk_create", "bulk_update", "refresh", "merge",
})
_SELF_ROOTS = frozenset({"self", "this"})
_SUBSCRIPT_TYPES = ("subscript", "subscript_expression", "index_expression")
_STATUS_RE = re.compile(r"HTTP_(\d{3})|status_code\s*=\s*(\d{3})|status\s*=\s*(\d{3})")
_LEADING_STATUS_RE = re.compile(r"^\s*\(\s*(\d{3})\b")
_COLLECTION_EXPRS = frozenset({
    "list", "dictionary", "set", "tuple", "list_comprehension", "dictionary_comprehension", "set_comprehension",
    "generator_expression", "array", "object",
})
_BOOL_EXPRS = frozenset({"true", "false", "comparison_operator", "not_operator", "boolean_operator"})
_COLLECTION_ANN = re.compile(
    r"(list|List|dict|Dict|set|Set|frozenset|tuple|Tuple|Sequence|Iterable|Iterator|Generator|AsyncGenerator|"
    r"Mapping|QuerySet|Array|ReadonlyArray|Record|Map)\b|\[\]$"
)


@dataclass(frozen=True)
class NodeContract:
    state: tuple[str, ...]  # sorted; (READ_ONLY,) when nothing is mutated
    throws: tuple[tuple[str, tuple[str, ...]], ...]  # (exception, status codes), sorted
    ret: str
    entry: str | None = None  # "ROUTE" or None
    #: flags added by one-hop delegation (subset of `state`)
    delegated: tuple[str, ...] = ()
    #: the function's own parameters it mutates locally (for delegation)
    mutated_params: frozenset[str] = field(default_factory=frozenset)


@dataclass
class _Local:
    params: list[str]
    state: set[str]
    mutated_params: set[str]
    throws: dict[str, set[str]]
    ret: str


def _text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf8", "replace")


def _params(def_node, source: bytes) -> list[str]:
    """Parameter names in declaration order (`self` / `cls` excluded)."""
    params = def_node.child_by_field_name("parameters")
    names: list[str] = []
    if params is None:
        return names
    for p in params.named_children:
        name = None
        if p.type == "identifier":
            name = p
        elif p.type == "typed_parameter":
            name = next((c for c in p.named_children if c.type == "identifier"), None)
        elif p.type in ("default_parameter", "typed_default_parameter"):
            name = p.child_by_field_name("name")
        elif p.type in ("required_parameter", "optional_parameter"):
            name = p.child_by_field_name("pattern")
        elif p.type == "assignment_pattern":
            name = p.child_by_field_name("left")
        if name is not None and name.type == "identifier":
            text = _text(name, source)
            if text not in ("self", "cls"):
                names.append(text)
    return names


def _return_kind_annotation(annotation: str) -> str:
    a = annotation.strip().lstrip(":").strip()
    if re.fullmatch(r"None|void|NoReturn|never|Promise<void>", a):
        return RET_NONE
    if re.fullmatch(r"(bool|boolean)(\s*\|\s*None)?|Optional\[bool\]", a):
        return RET_BOOL
    if _COLLECTION_ANN.match(a) or a.endswith("[]"):
        return RET_COLLECTION
    return RET_OBJECT


def _return_kind_expr(expr, source: bytes) -> str:
    if expr is None:
        return RET_NONE
    text = _text(expr, source)
    if text in ("None", "null", "undefined"):
        return RET_NONE
    if expr.type in _BOOL_EXPRS or text in ("True", "False") or (
        expr.type == "unary_expression" and text.startswith("!")
    ):
        return RET_BOOL
    if expr.type in _COLLECTION_EXPRS:
        return RET_COLLECTION
    if expr.type in ("call", "call_expression") and re.search(r"\.(all|values_list|values)\(\)\s*$|^(list|sorted|dict|set|tuple)\(", text):
        return RET_COLLECTION
    return RET_OBJECT


def _return_kind(def_node, source: bytes, lang: str) -> str:
    annotation = def_node.child_by_field_name("return_type")
    if annotation is not None:
        return _return_kind_annotation(_text(annotation, source))
    if iter_scoped_nodes(def_node, {"yield", "yield_expression"}, lang):
        return RET_COLLECTION
    kinds = {
        _return_kind_expr(r.named_children[0] if r.named_children else None, source)
        for r in iter_scoped_nodes(def_node, {"return_statement"}, lang)
    }
    kinds.discard(RET_NONE) if len(kinds) > 1 else None
    if not kinds:
        return RET_NONE
    return kinds.pop() if len(kinds) == 1 else RET_OBJECT


def _exception(raise_node, source: bytes) -> tuple[str, set[str]]:
    text = _text(raise_node, source)
    match = re.match(r"(?:raise|throw)\s+(?:new\s+)?([\w.$]+)", text)
    name = match.group(1).rsplit(".", 1)[-1] if match else "reraise"
    codes = {c for groups in _STATUS_RE.findall(text) for c in groups if c}
    rest = text[match.end():] if match else ""
    lead = _LEADING_STATUS_RE.match(rest)
    if lead:
        codes.add(lead.group(1))
    return name, codes


def _local_contract(builder: ConcreteGraphBuilder, qname: str, import_maps: dict) -> _Local | None:
    symbol = builder.symbol_table.get(qname)
    def_node = builder.def_node(qname)
    parsed = builder.parsed_file(symbol.file) if symbol is not None else None
    if def_node is None or parsed is None:
        return None
    source, lang = parsed.source, parsed.language_id
    if symbol.module not in import_maps:
        import_maps[symbol.module] = builder._build_import_map(parsed, symbol.module)
    import_map = import_maps[symbol.module]
    params = _params(def_node, source)
    param_set = set(params)
    state: set[str] = set()
    mutated_params: set[str] = set()

    def mark(root: str) -> None:
        if root in _SELF_ROOTS:
            state.add(MUTATES_SELF)
        elif root in param_set:
            state.add(MUTATES_ARGS)
            mutated_params.add(root)
        elif import_map.resolve(root):
            state.add(MUTATES_GLOBAL)

    assign_types = {t for t in (ASSIGNMENT_NODE_TYPE.get(lang), AUGMENTED_ASSIGNMENT_NODE_TYPE.get(lang)) if t}
    for assign in iter_scoped_nodes(def_node, assign_types, lang):
        target = assign.child_by_field_name("left")
        if target is None:
            continue
        subscript = target.type in _SUBSCRIPT_TYPES
        if subscript:
            target = target.child_by_field_name("object") or target.child_by_field_name("value") or (
                target.children[0] if target.children else None)
            if target is None:
                continue
        segments = flatten_reference_chain(target, source, lang)
        if segments and (subscript or len(segments) >= 2):
            mark(segments[0])
    call_type = CALL_NODE_TYPE.get(lang)
    if call_type:
        for call in iter_scoped_nodes(def_node, {call_type}, lang):
            segments = call_callee_segments(call, source, lang)
            if segments and len(segments) >= 2 and segments[-1] in MUTATING_METHODS:
                mark(segments[0])
    if lang == LanguageID.PYTHON and iter_scoped_nodes(def_node, {"global_statement", "nonlocal_statement"}, lang):
        state.add(MUTATES_GLOBAL)

    throws: dict[str, set[str]] = {}
    raise_type = RAISE_NODE_TYPE.get(lang)
    if raise_type:
        for raise_node in iter_scoped_nodes(def_node, {raise_type}, lang):
            name, codes = _exception(raise_node, source)
            throws.setdefault(name, set()).update(codes)
    if call_type:
        for call in iter_scoped_nodes(def_node, {call_type}, lang):
            segments = call_callee_segments(call, source, lang)
            if segments and segments[-1] == "abort":
                lead = _LEADING_STATUS_RE.match(_text(call, source)[len(".".join(segments)):])
                throws.setdefault("abort", set()).update({lead.group(1)} if lead else set())
    return _Local(params, state, mutated_params, throws, _return_kind(def_node, source, lang))


def _call_arguments(call, source: bytes) -> tuple[list[str | None], dict[str, str | None]]:
    """`(positional, keyword)` argument root names (None when not a plain name
    or attribute chain)."""
    args = call.child_by_field_name("arguments")
    positional: list[str | None] = []
    keyword: dict[str, str | None] = {}
    if args is None:
        return positional, keyword
    for a in args.named_children:
        if a.type == "keyword_argument":
            name, value = a.child_by_field_name("name"), a.child_by_field_name("value")
            if name is not None:
                keyword[_text(name, source)] = _text(value, source).split(".")[0] if value is not None and value.type in ("identifier", "attribute") else None
        elif a.type in ("list_splat", "dictionary_splat", "spread_element", "comment"):
            continue
        else:
            positional.append(_text(a, source).split(".")[0] if a.type in ("identifier", "attribute", "member_expression") else None)
    return positional, keyword


def _delegated_flags(builder: ConcreteGraphBuilder, qname: str, local: _Local, locals_: dict[str, _Local]) -> set[str]:
    """The mutation flags `qname` inherits from callees it forwards a
    parameter (or `self`) into - one hop, by argument position or keyword."""
    symbol = builder.symbol_table.get(qname)
    def_node = builder.def_node(qname)
    parsed = builder.parsed_file(symbol.file)
    source, lang = parsed.source, parsed.language_id
    call_type = CALL_NODE_TYPE.get(lang)
    if not call_type or qname not in builder.graph:
        return set()
    by_name: dict[str, list[str]] = {}
    for _u, target, data in builder.graph.out_edges(qname, data=True):
        if data.get("relation", "CALLS") == "CALLS" and target in locals_:
            by_name.setdefault(target.rsplit(".", 1)[-1], []).append(target)
    params = set(local.params)
    flags: set[str] = set()

    def flag_for(root: str | None) -> str | None:
        if root in _SELF_ROOTS:
            return MUTATES_SELF
        if root in params:
            return MUTATES_ARGS
        return None

    for call in iter_scoped_nodes(def_node, {call_type}, lang):
        segments = call_callee_segments(call, source, lang)
        if not segments:
            continue
        targets = by_name.get(segments[-1], [])
        if len(targets) != 1:
            continue  # unresolved or ambiguous: never guess a callee
        callee = locals_[targets[0]]
        # a self-mutating method called on a forwarded parameter / self
        if len(segments) >= 2 and MUTATES_SELF in callee.state:
            flag = flag_for(segments[0])
            if flag:
                flags.add(flag)
        if not callee.mutated_params:
            continue
        positional, keyword = _call_arguments(call, source)
        for index, root in enumerate(positional):
            if index < len(callee.params) and callee.params[index] in callee.mutated_params:
                flag = flag_for(root)
                if flag:
                    flags.add(flag)
        for name, root in keyword.items():
            if name in callee.mutated_params:
                flag = flag_for(root)
                if flag:
                    flags.add(flag)
    return flags


def compute_node_contracts(
    builder: ConcreteGraphBuilder, entries: "set[str] | None" = None, propagate: bool = True,
) -> dict[str, NodeContract]:
    """`{qualified_name: NodeContract}` for every indexed function/method.
    `entries`: route qualified names (default: `find_entry_points(builder)`)."""
    if entries is None:
        from prism.graph.entry_points import find_entry_points
        entries = set(find_entry_points(builder))
    import_maps: dict = {}
    locals_: dict[str, _Local] = {}
    for symbol in builder.symbol_table:
        if symbol.kind in ("function", "method"):
            local = _local_contract(builder, symbol.qualified_name, import_maps)
            if local is not None:
                locals_[symbol.qualified_name] = local
    contracts: dict[str, NodeContract] = {}
    for qname, local in locals_.items():
        delegated = _delegated_flags(builder, qname, local, locals_) - local.state if propagate else set()
        state = (local.state | delegated) or {READ_ONLY}
        contracts[qname] = NodeContract(
            state=tuple(sorted(state)),
            throws=tuple(sorted((name, tuple(sorted(codes))) for name, codes in local.throws.items())),
            ret=local.ret,
            entry="ROUTE" if qname in entries else None,
            delegated=tuple(sorted(delegated)),
            mutated_params=frozenset(local.mutated_params),
        )
    return contracts


def contract_field(contract: NodeContract | None) -> str:
    """`contract=[state=... throws=... ret=... entry=...]` for a manifest row."""
    if contract is None:
        return "contract=[state=UNKNOWN throws=NOTHROW ret=NONE entry=NONE]"
    throws = ",".join(f"{name}[{','.join(codes)}]" if codes else name for name, codes in contract.throws) or "NOTHROW"
    return (f"contract=[state={'+'.join(contract.state)} throws={throws} ret={contract.ret} "
            f"entry={contract.entry or 'NONE'}]")
