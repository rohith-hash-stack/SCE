"""Public entry points and the entry -> sink paths above a function
(presentation only: nothing here changes how anything ranks).

An entry point is a function a framework invokes from outside the code:
an HTTP route, CLI command, task, signal receiver. Two sources, merged
with their provenance:

1. **Decorators** (Python; decorators with arguments in TS/JS). What a
   decorator does with the function is read from its own source
   (`prism.graph.decorator_kinds`): `REGISTRATION` (hands the function to a
   registry) makes an entry; `WRAPPER` (returns a new function calling the
   original - `@wraps` closures such as Django's `async_unsafe`) and
   `UNKNOWN` (mixed returns) never do; `ANNOTATION` (only sets attributes)
   and decorators whose source is not readable (not in the repo nor in the
   given dependency paths, e.g. `@router.get` on an instance, NestJS
   `@Get`) make an entry only with route evidence: an HTTP verb (the
   decorator's last name segment, or a `methods=[...]` keyword) or a path
   literal starting with `/`.
2. **Registration calls** (`prism.graph.registrations`): Express
   `app.get('/x', handler)`, tRPC `.query(resolver)` under a `router({...})`
   key, Django `path('x/', view)`, Flask `add_url_rule`.

Having no in-repo caller is *not* evidence (a dynamic call the graph cannot
resolve looks the same - Django's `savepoint()`); it is kept as metadata
(`EntryPoint.called_in_repo`).

Mount prefixes (blueprints, included routers) are framework configuration
and are not resolved. Breadcrumbs - the entry paths shown next to a slice
of context - are built by `prism.graph.render_breadcrumbs`.
"""
from __future__ import annotations

import ast
import os
import re
from collections import deque
from dataclasses import dataclass

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.graph.decorator_kinds import DecoratorKind, classify, find_def, find_qualified
from prism.graph.symbol_table import SymbolRole
from prism.parser.lang_config import LanguageID, node_text

#: Decorators that are language/stdlib machinery, not registration.
BUILTIN_DECORATORS = (
    "property", "staticmethod", "classmethod", "cached_property", "abstractmethod", "wraps", "dataclass",
    "contextmanager", "asynccontextmanager", "overload", "lru_cache", "cache", "override", "final",
)
HTTP_VERBS = ("get", "post", "put", "patch", "delete", "head", "options")
_EDGES = ("CALLS", "INSTANTIATES")
_JS_LANGS = (LanguageID.JAVASCRIPT, LanguageID.TYPESCRIPT, LanguageID.TSX)


@dataclass(frozen=True)
class EntryPoint:
    qname: str
    decorator: str  # dotted head ("router.delete"), or the registration call ("app.get")
    method: str | None = None  # "POST", "GET POST", "QUERY", ...
    path: str | None = None  # route path / procedure name
    source: str = "decorator"  # "decorator" | "call:express" | "call:trpc" | "call:django" | "call:flask"
    kind: str | None = None  # the decorator's DecoratorKind value, when its source was read
    called_in_repo: bool = False  # metadata only - never a reason to be an entry

    def label(self) -> str:
        """`POST /users`, `POST @router.post` (no literal path), `QUERY user.byId`."""
        if self.source != "decorator":
            return " ".join(x for x in (self.method, self.path or self.decorator) if x)
        where = self.path if self.path and self.path.startswith("/") else f"@{self.decorator}"
        return f"{self.method} {where}" if self.method else where


def _is_builtin(head: str) -> bool:
    last = head.rsplit(".", 1)[-1]
    return last in BUILTIN_DECORATORS or head.startswith("functools.")


def _parse_decorator(text: str) -> tuple[str, str | None, str | None]:
    """`(dotted head, HTTP method, path)` of one decorator's text (Python
    syntax; TS decorators `@Get('/x')` parse the same way)."""
    text = text.strip().lstrip("@").strip()
    try:
        expr = ast.parse(text, mode="eval").body
    except SyntaxError:
        head = text.split("(", 1)[0].strip()
        literal = re.search(r"""\(\s*['"`]([^'"`]*)['"`]""", text)
        verb = head.rsplit(".", 1)[-1].lower()
        return head, verb.upper() if verb in HTTP_VERBS else None, literal.group(1) if literal else None
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
        if method is None and last in HTTP_VERBS:
            method = last.upper()
    return head, method, path


def _is_called(text: str) -> bool:
    return text.rstrip().endswith(")")


def decorator_nodes(node, parsed) -> list:
    """The decorator nodes attached to a definition: Python's
    `decorated_definition` wrapper; for TS/JS, the `decorator` nodes right
    before a method in a class body, or on a class (and on the
    `export_statement` around it)."""
    parent = node.parent
    if parent is None:
        return []
    if parsed.language_id == LanguageID.PYTHON:
        return [c for c in parent.children if c.type == "decorator"] if parent.type == "decorated_definition" else []
    if parsed.language_id in _JS_LANGS:
        found = [c for c in node.children if c.type == "decorator"]
        if parent.type == "export_statement":
            found = [c for c in parent.children if c.type == "decorator"] + found
        sibling = node.prev_named_sibling
        while sibling is not None and sibling.type == "decorator":
            found.insert(0, sibling)
            sibling = sibling.prev_named_sibling
        return found
    return []


def _decorator_texts(node, parsed) -> list[str]:
    """Full decorator source, arguments included (`@router.post("/x")`)."""
    from prism.parser.lang_config import collect_decorator_texts

    nodes = decorator_nodes(node, parsed)
    if nodes:
        return [node_text(n, parsed.source) for n in nodes]
    return collect_decorator_texts(node, parsed)  # Java annotations / C# attributes (names only)


def _definite(data: dict) -> bool:
    """A call the code makes, not a speculative "may call" edge: the
    virtual-dispatch fan-out from a call on `Base.m` to every override of
    `m` (`dispatch_of`), or a function passed as a callback argument
    (`via="callback_argument"`). Other `TENTATIVE_CALL` edges (a method
    chained on a fresh instance, `Repo().insert(...)`) are kept."""
    return (data.get("relation", "CALLS") in _EDGES and "dispatch_of" not in data
            and data.get("via") != "callback_argument")


def _callers(graph, qname: str, funcs: set[str], definite: bool = False) -> list[str]:
    """Callers of `qname` among `funcs`; `definite=True` keeps only resolved
    calls (a breadcrumb asserts a path, so it never rests on a guess)."""
    if qname not in graph:
        return []
    return [u for u, _, d in graph.in_edges(qname, data=True)
            if d.get("relation", "CALLS") in _EDGES and u in funcs and (not definite or _definite(d))]


def _application_functions(builder: ConcreteGraphBuilder) -> set[str]:
    """Functions and methods outside test code (`SymbolRole.VERIFICATION`):
    a test fixture is registered by decorator too, but is not an entrance."""
    return {
        s.qualified_name for s in builder.symbol_table
        if s.kind in ("function", "method") and s.role != SymbolRole.VERIFICATION
    }


class _DecoratorReader:
    """Resolves a decorator's head to its Python definition - in the repo,
    or under `dependency_paths` - and classifies it (cached)."""

    def __init__(self, builder: ConcreteGraphBuilder, dependency_paths: list[str] | None = None):
        self.builder, self.paths = builder, [p for p in (dependency_paths or []) if p and os.path.isdir(p)]
        self._trees: dict[str, ast.Module | None] = {}
        self._kinds: dict[tuple, tuple[DecoratorKind | None, bool]] = {}

    def _tree(self, path: str) -> ast.Module | None:
        if path not in self._trees:
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    self._trees[path] = ast.parse(fh.read())
            except (OSError, SyntaxError, ValueError):
                self._trees[path] = None
        return self._trees[path]

    def _in_repo(self, segments: list[str], file: str, parsed):
        try:
            module = self.builder._module_for_file(parsed)
            import_map = self.builder.import_map(file)
            qname = self.builder._resolve_reference_chain(segments, module, import_map) if import_map else None
        except Exception:  # resolution is best effort; an unresolved head falls back to route evidence
            return None
        symbol = self.builder.symbol_table.get(qname) if qname else None
        if symbol is None or symbol.kind not in ("function", "method") or not symbol.file.endswith(".py"):
            return None
        path = symbol.file if os.path.isabs(symbol.file) else os.path.join(self.builder.repo_root, symbol.file)
        tree = self._tree(path)
        if tree is None:
            return None
        fn, cls = find_def(tree, symbol.line_range[0])
        return (fn, cls, tree) if fn is not None else None

    def _in_dependency(self, segments: list[str], file: str):
        if not self.paths:
            return None
        import_map = self.builder.import_map(file)
        target = import_map.resolve(segments[0]) if import_map else None
        if not target:
            return None
        dotted = target.split(".") + segments[1:]
        for split in range(len(dotted) - 1, 0, -1):
            mod, rest = dotted[:split], dotted[split:]
            for root in self.paths:
                for candidate in (os.path.join(root, *mod) + ".py", os.path.join(root, *mod, "__init__.py")):
                    if os.path.isfile(candidate) and (tree := self._tree(candidate)) is not None:
                        fn, cls = find_qualified(tree, rest)
                        if fn is not None:
                            return fn, cls, tree
        return None

    def kind(self, head: str, text: str, symbol, parsed) -> tuple[DecoratorKind | None, bool]:
        """`(kind, resolved)`: `kind` is `None` when the source was not read
        or the receiving function could not be followed."""
        if parsed.language_id != LanguageID.PYTHON:
            return None, False
        called = _is_called(text)
        key = (symbol.file, head, called)
        if key not in self._kinds:
            segments = head.split(".")
            found = self._in_repo(segments, symbol.file, parsed) or self._in_dependency(segments, symbol.file)
            self._kinds[key] = (classify(found[0], called, found[1], found[2]), True) if found else (None, False)
        return self._kinds[key]


def find_entry_points(builder: ConcreteGraphBuilder, funcs: "set[str] | None" = None,
                      dependency_paths: list[str] | None = None, calls: bool = True) -> dict[str, EntryPoint]:
    """`{qualified name: EntryPoint}` for every decorator-registered or
    call-registered function or method of the index (test code excluded).
    `funcs`: the index's application functions, when already computed."""
    funcs = _application_functions(builder) if funcs is None else funcs
    reader = _DecoratorReader(builder, dependency_paths)
    entries: dict[str, EntryPoint] = {}
    for qname in sorted(funcs):
        node = builder.def_node(qname)
        symbol = builder.symbol_table.get(qname)
        parsed = builder.parsed_file(symbol.file) if symbol is not None else None
        if node is None or parsed is None:
            continue
        chosen = None
        for text in _decorator_texts(node, parsed):
            head, method, path = _parse_decorator(text)
            if not head or _is_builtin(head):
                continue
            kind, _resolved = reader.kind(head, text, symbol, parsed)
            evidence = bool(method) or bool(path and path.startswith("/"))
            if kind == DecoratorKind.REGISTRATION or (kind in (None, DecoratorKind.ANNOTATION) and evidence):
                candidate = EntryPoint(qname, head, method, path, "decorator", kind.value if kind else None)
                if chosen is None or (not (chosen.method or chosen.path) and (method or path)):
                    chosen = candidate
        if chosen is not None:
            entries[qname] = chosen
    if calls:
        from prism.graph.registrations import find_call_registrations

        for qname, entry in find_call_registrations(builder, funcs).items():
            entries.setdefault(qname, entry)
    return {
        q: EntryPoint(e.qname, e.decorator, e.method, e.path, e.source, e.kind, bool(_callers(builder.graph, q, funcs)))
        for q, e in entries.items()
    }


def entry_paths(
    builder: ConcreteGraphBuilder, qname: str, entries: dict[str, EntryPoint], max_hops: int = 3, limit: int = 3,
    funcs: "set[str] | None" = None, blocked: "frozenset[str] | set[str]" = frozenset(),
) -> list[list[str]]:
    """Shortest caller chains `[entry, ..., qname]` from up to `limit`
    entry points within `max_hops` caller hops (`[[qname]]` when `qname` is
    itself an entry; `[]` when no entry reaches it through call edges).
    Breadth-first, so nearer entries come first; callers at the same depth
    in name order; each function is expanded once; speculative edges
    (override fan-out, callback guesses - see `_definite`) are not
    followed. `blocked` functions are
    never stepped through (a slice's other members: a breadcrumb never
    re-describes the slice). `funcs`: the index's application functions -
    pass them (or use `EntryIndex`) to avoid recomputing them per call."""
    if qname in entries:
        return [[qname]]
    funcs = _application_functions(builder) if funcs is None else funcs
    parent: dict[str, str | None] = {qname: None}
    depth = {qname: 0}
    queue, found = deque([qname]), []
    while queue and len(found) < limit:
        node = queue.popleft()
        if depth[node] >= max_hops:
            continue
        for caller in sorted(set(_callers(builder.graph, node, funcs, definite=True))):
            if caller in parent or caller in blocked:
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


def short_name(qname: str) -> str:
    parts = qname.split(".")
    return ".".join(parts[-2:]) if len(parts) > 1 and parts[-2][:1].isupper() else parts[-1]


_short = short_name  # backward-compatible alias


def format_entry_context(chain: list[str], entries: dict[str, EntryPoint]) -> str:
    """`[Entry: POST /follow/<username>] -> follow() => enters at User.follow()`
    (`[Entry: ...] => enters at f()` when `f` is itself the entry)."""
    entry = entries[chain[0]]
    outside = chain[:-1]
    head = f"[Entry: {entry.label()}]"
    if outside:
        head += " -> " + " -> ".join(f"{short_name(q)}()" for q in outside)
    return f"{head} => enters at {short_name(chain[-1])}()"


#: Directories holding example, tutorial or documentation apps. Their
#: routes describe how a *user* of the code calls it, not how the code
#: itself is reached, so they only count for code inside the same app.
EXAMPLE_DIRS = ("examples", "example", "docs", "docs_src", "tutorials")


def example_zone(relpath: str) -> str | None:
    """The example app `relpath` belongs to: the path through the first
    `EXAMPLE_DIRS` segment plus the app directory after it
    (`examples/auth/index.js` -> `examples/auth`; `docs_src/app.py` ->
    `docs_src`), or `None` outside example directories."""
    parts = relpath.replace("\\", "/").split("/")
    for i, part in enumerate(parts[:-1]):
        if part in EXAMPLE_DIRS:
            return "/".join(parts[: i + 2] if i + 2 < len(parts) else parts[: i + 1])
    return None


class EntryIndex:
    """Entry points and application functions of one index, computed once;
    `paths`/`context`/`breadcrumbs` per symbol or slice reuse them."""

    def __init__(self, builder: ConcreteGraphBuilder, max_hops: int = 3, limit: int = 3,
                 dependency_paths: list[str] | None = None):
        self.builder, self.max_hops, self.limit = builder, max_hops, limit
        self.functions = _application_functions(builder)
        self.entries = find_entry_points(builder, self.functions, dependency_paths)
        self._zones = {q: self.zone(q) for q in self.entries}
        self._by_zone: dict[str | None, dict[str, EntryPoint]] = {}

    def zone(self, qname: str) -> str | None:
        symbol = self.builder.symbol_table.get(qname)
        if symbol is None:
            return None
        rel = os.path.relpath(symbol.file, self.builder.repo_root) if os.path.isabs(symbol.file) else symbol.file
        return example_zone(rel)

    def entries_for(self, qname: str) -> dict[str, EntryPoint]:
        """The entries a path to `qname` may start from: all entries outside
        example directories, plus those in `qname`'s own example app."""
        zone = self.zone(qname)
        if zone not in self._by_zone:
            self._by_zone[zone] = {q: e for q, e in self.entries.items() if self._zones[q] in (None, zone)}
        return self._by_zone[zone]

    def paths(self, qname: str, blocked: "frozenset[str] | set[str]" = frozenset()) -> list[list[str]]:
        return entry_paths(self.builder, qname, self.entries_for(qname), self.max_hops, self.limit,
                           funcs=self.functions, blocked=blocked)

    def breadcrumbs(self, symbols: list[str], **kwargs):
        """Validated breadcrumbs for a slice - see
        `prism.graph.render_breadcrumbs.slice_breadcrumbs`."""
        from prism.graph.render_breadcrumbs import slice_breadcrumbs

        return slice_breadcrumbs(self, symbols, **kwargs)

    def context(self, qname: str) -> list[str]:
        """The one breadcrumb of a single-symbol slice, as text (`[]` when
        none passes the invariants)."""
        symbol = self.builder.symbol_table.get(qname)
        if symbol is None or symbol.kind == "class":
            return []
        return [c.text for c in self.breadcrumbs([qname])]
