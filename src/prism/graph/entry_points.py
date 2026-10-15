"""Public entry points and the entry -> sink paths above a function
(presentation only: nothing here changes how anything ranks).

An entry point is found without any framework catalog: a function with at
least one decorator that is not a language builtin (`property`,
`staticmethod`, `functools.*`, ...) and no in-repo `CALLS`/`INSTANTIATES`
caller. Being registered by decorator rather than called is exactly how a
framework invokes a route, CLI command, task or callback. For display, a
decorator's HTTP method and path are read generically: the decorator's
last name segment when it is an HTTP verb (`router.delete(...)`), else a
`methods=[...]` keyword (`bp.route(..., methods=['POST'])`), and its first
string argument as the path. Mount prefixes (blueprints, included routers)
are framework configuration and are not resolved.
"""
from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.symbol_table import SymbolRole
from prism.parser.lang_config import collect_decorator_texts
from prism.parser.lang_config import LanguageID, node_text

#: Decorators that are language/stdlib machinery, not registration.
BUILTIN_DECORATORS = (
    "property", "staticmethod", "classmethod", "cached_property", "abstractmethod", "wraps", "dataclass",
    "contextmanager", "asynccontextmanager", "overload", "lru_cache", "cache", "override", "final",
)
_HTTP_VERBS = ("get", "post", "put", "patch", "delete", "head", "options")
_EDGES = ("CALLS", "INSTANTIATES")


@dataclass(frozen=True)
class EntryPoint:
    qname: str
    decorator: str  # dotted head, e.g. "router.delete"
    method: str | None = None  # "POST", "GET POST", ...
    path: str | None = None  # the decorator's first string argument

    def label(self) -> str:
        """`POST /users`, `POST @router.post` (no literal path), `@translate.command`."""
        where = self.path if self.path and self.path.startswith("/") else f"@{self.decorator}"
        return f"{self.method} {where}" if self.method else where


def _is_builtin(head: str) -> bool:
    last = head.rsplit(".", 1)[-1]
    return last in BUILTIN_DECORATORS or head.startswith("functools.")


def _parse_decorator(text: str) -> tuple[str, str | None, str | None]:
    """`(dotted head, HTTP method, path)` of one decorator's text."""
    text = text.strip().lstrip("@").strip()
    try:
        expr = ast.parse(text, mode="eval").body
    except SyntaxError:
        return text.split("(", 1)[0].strip(), None, None
    call = expr if isinstance(expr, ast.Call) else None
    func = call.func if call is not None else expr
    parts = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    head = ".".join(reversed(parts)) or text.split("(", 1)[0]
    method = path = None
    if call is not None:
        if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
            path = call.args[0].value
        for kw in call.keywords:
            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                verbs = [e.value.upper() for e in kw.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                method = " ".join(verbs) or None
        last = head.rsplit(".", 1)[-1].lower()
        if method is None and last in _HTTP_VERBS:
            method = last.upper()
    return head, method, path


def _decorator_texts(node, parsed) -> list[str]:
    """Full decorator source (arguments included) for Python; the dotted
    names `collect_decorator_texts` gives for every other language."""
    if parsed.language_id == LanguageID.PYTHON:
        parent = node.parent
        if parent is None or parent.type != "decorated_definition":
            return []
        return [node_text(c, parsed.source) for c in parent.children if c.type == "decorator"]
    return collect_decorator_texts(node, parsed)


def _callers(graph, qname: str, funcs: set[str]) -> list[str]:
    if qname not in graph:
        return []
    return [u for u, _, d in graph.in_edges(qname, data=True) if d.get("relation", "CALLS") in _EDGES and u in funcs]


def _application_functions(builder: ConcreteGraphBuilder) -> set[str]:
    """Functions and methods outside test code (`SymbolRole.VERIFICATION`):
    a test fixture is registered by decorator too, but is not an entrance."""
    return {
        s.qualified_name for s in builder.symbol_table
        if s.kind in ("function", "method") and s.role != SymbolRole.VERIFICATION
    }


def find_entry_points(builder: ConcreteGraphBuilder) -> dict[str, EntryPoint]:
    """`{qualified name: EntryPoint}` for every decorator-registered,
    never-called function or method of the index (test code excluded)."""
    funcs = _application_functions(builder)
    entries: dict[str, EntryPoint] = {}
    for qname in funcs:
        node = builder.def_node(qname)
        symbol = builder.symbol_table.get(qname)
        parsed = builder.parsed_file(symbol.file) if symbol is not None else None
        if node is None or parsed is None:
            continue
        decorators = [_parse_decorator(t) for t in _decorator_texts(node, parsed)]
        registering = [d for d in decorators if not _is_builtin(d[0])]
        if not registering or _callers(builder.graph, qname, funcs):
            continue
        head, method, path = next((d for d in registering if d[1] or d[2]), registering[0])
        entries[qname] = EntryPoint(qname, head, method, path)
    return entries


def entry_paths(
    builder: ConcreteGraphBuilder, qname: str, entries: dict[str, EntryPoint], max_hops: int = 3, limit: int = 3
) -> list[list[str]]:
    """Shortest caller chains `[entry, ..., qname]` from up to `limit`
    entry points within `max_hops` caller hops (`[[qname]]` when `qname` is
    itself an entry; `[]` when no entry reaches it through call edges)."""
    if qname in entries:
        return [[qname]]
    funcs = _application_functions(builder)
    parent: dict[str, str | None] = {qname: None}
    depth = {qname: 0}
    queue, found = deque([qname]), []
    while queue and len(found) < limit:
        node = queue.popleft()
        if depth[node] >= max_hops:
            continue
        for caller in sorted(_callers(builder.graph, node, funcs)):
            if caller in parent:
                continue
            parent[caller], depth[caller] = node, depth[node] + 1
            if caller in entries:
                chain, cur = [], caller
                while cur is not None:
                    chain.append(cur)
                    cur = parent[cur]
                found.append(chain)
                if len(found) >= limit:
                    break
            else:
                queue.append(caller)
    return found


def _short(qname: str) -> str:
    parts = qname.split(".")
    return ".".join(parts[-2:]) if len(parts) > 1 and parts[-2][:1].isupper() else parts[-1]


def format_entry_context(chain: list[str], entries: dict[str, EntryPoint]) -> str:
    """`[Entry: POST /follow/<username>] -> follow() -> User.follow()`."""
    entry = entries[chain[0]]
    return " -> ".join([f"[Entry: {entry.label()}]"] + [f"{_short(q)}()" for q in chain])
