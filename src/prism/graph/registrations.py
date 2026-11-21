"""Entry points registered by a call rather than a decorator.

- **Express** (JS/TS): `app.get('/x', handler)`, `router.post('/x', mw, handler)`,
  `app.use('/x', handler)`, `app.all(...)`, `app.route('/x').get(handler)`:
  the callee's last segment is an HTTP verb, `all` or `use`, a path literal
  starting with `/` is given (or comes from the `.route('/x')` it chains
  on), and the last function argument is the handler (earlier ones are
  middleware).
- **tRPC** (JS/TS): a resolver passed to `.query(...)`, `.mutation(...)` or
  `.subscription(...)`, named by the `router({...})` keys around it
  (`user.byId`).
- **Django** (Python): `path('x/', view)`, `re_path(...)`, `url(...)`;
  `View.as_view()` registers that class's HTTP-verb methods (and `dispatch`).
- **Flask** (Python): `add_url_rule('/x', endpoint, view_func)` /
  `view_func=` keyword.

A handler must resolve to an indexed function or method. An inline
handler (arrow function, lambda) has no symbol of its own, so the
registration is attributed to the named function containing the
registration call; one at module level is skipped. Test files are
skipped. Detection is syntactic: a call that merely looks like a
registration (`cache.get('/key', fn)`) would be taken as one.
"""
from __future__ import annotations

from prism.graph.concrete_builder import ConcreteGraphBuilder
from prism.parser.lang_config import CALL_NODE_TYPE, LanguageID, flatten_reference_chain, node_text

HTTP_VERBS = ("get", "post", "put", "patch", "delete", "head", "options")
_EXPRESS = set(HTTP_VERBS) | {"all", "use"}
_TRPC = ("query", "mutation", "subscription")
_DJANGO = ("path", "re_path", "url")
_JS_LANGS = (LanguageID.JAVASCRIPT, LanguageID.TYPESCRIPT, LanguageID.TSX)
_JS_FUNCTIONS = ("arrow_function", "function_expression", "function", "generator_function")
_PY_FUNCTIONS = ("lambda",)


def _literal(node, source: bytes) -> str | None:
    """The value of a plain string literal node, else `None`."""
    if node is None or node.type not in ("string", "template_string"):
        return None
    if node.type == "template_string" and any(c.type == "template_substitution" for c in node.children):
        return None
    text = node_text(node, source)
    i = 0
    while i < len(text) and text[i].isalpha():  # r"", b'', f"" prefixes
        i += 1
    body = text[i:]
    for q in ('"""', "'''", '"', "'", "`"):
        if body.startswith(q) and body.endswith(q) and len(body) >= 2 * len(q):
            return body[len(q):-len(q)]
    return None


def _args(call, lang) -> list:
    args = call.child_by_field_name("arguments")
    return [c for c in args.named_children if c.type != "comment"] if args is not None else []


def _walk(node, kind: str):
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == kind:
            yield n
        stack.extend(n.children)


class _Scanner:
    def __init__(self, builder: ConcreteGraphBuilder, funcs: set[str]):
        self.builder, self.funcs = builder, funcs
        self.by_file: dict[str, list] = {}
        for s in builder.symbol_table:
            if s.qualified_name in funcs:
                self.by_file.setdefault(s.file, []).append(s)
        self.found: dict[str, object] = {}

    # -- resolution ------------------------------------------------------ #
    def _resolve(self, node, parsed) -> str | None:
        segments = flatten_reference_chain(node, parsed.source, parsed.language_id)
        if not segments:
            return None
        try:
            module = self.builder._module_for_file(parsed)
            import_map = self.builder.import_map(parsed.path)
            q = self.builder._resolve_reference_chain(segments, module, import_map) if import_map else None
        except Exception:  # best effort: an unresolved handler is not an entry
            return None
        return q if q in self.funcs else None

    def _enclosing(self, node, parsed) -> str | None:
        line = node.start_point[0] + 1
        inside = [s for s in self.by_file.get(parsed.path, ()) if s.line_range[0] <= line <= s.line_range[1]]
        return min(inside, key=lambda s: s.line_range[1] - s.line_range[0]).qualified_name if inside else None

    def _handler(self, node, parsed, functions: tuple) -> str | None:
        if node.type in functions:
            return self._enclosing(node, parsed)
        return self._resolve(node, parsed)

    def _add(self, qname: str | None, decorator: str, method: str | None, path: str | None, source: str) -> None:
        from prism.graph.entry_points import EntryPoint

        if qname and qname not in self.found:
            self.found[qname] = EntryPoint(qname, decorator, method, path, source)

    # -- per language ------------------------------------------------------ #
    def scan_js(self, parsed) -> None:
        src = parsed.source
        for call in _walk(parsed.root_node, CALL_NODE_TYPE[parsed.language_id]):
            func = call.child_by_field_name("function")
            if func is None or func.type != "member_expression":
                continue
            prop = func.child_by_field_name("property")
            last = node_text(prop, src) if prop is not None else ""
            args = _args(call, parsed.language_id)
            if last in _EXPRESS:
                path = _literal(args[0], src) if args else None
                obj = func.child_by_field_name("object")
                if path is None and obj is not None and obj.type == "call_expression":  # .route('/x').get(h)
                    inner = obj.child_by_field_name("function")
                    inner_prop = inner.child_by_field_name("property") if inner is not None and inner.type == "member_expression" else None
                    if inner_prop is not None and node_text(inner_prop, src) == "route":
                        inner_args = _args(obj, parsed.language_id)
                        path = _literal(inner_args[0], src) if inner_args else None
                if not path or not path.startswith("/"):
                    continue
                handlers = [a for a in args if _literal(a, src) is None]
                if not handlers:
                    continue
                head = ".".join(flatten_reference_chain(func, src, parsed.language_id) or [last])
                method = None if last == "use" else ("ALL" if last == "all" else last.upper())
                self._add(self._handler(handlers[-1], parsed, _JS_FUNCTIONS), head, method or "USE", path, "call:express")
            elif last in _TRPC and args:
                handler = args[0]
                if handler.type not in _JS_FUNCTIONS and handler.type not in ("identifier", "member_expression"):
                    continue
                qname = self._handler(handler, parsed, _JS_FUNCTIONS)
                if qname is None:
                    continue
                keys, cur = [], call.parent
                while cur is not None:
                    if cur.type == "pair":
                        key = cur.child_by_field_name("key")
                        if key is not None:
                            keys.append(_literal(key, src) or node_text(key, src))
                    cur = cur.parent
                if keys:
                    self._add(qname, f".{last}", last.upper(), ".".join(reversed(keys)), "call:trpc")

    def scan_python(self, parsed) -> None:
        src = parsed.source
        for call in _walk(parsed.root_node, "call"):
            func = call.child_by_field_name("function")
            if func is None:
                continue
            segments = flatten_reference_chain(func, src, parsed.language_id) or []
            last = segments[-1] if segments else ""
            args = _args(call, parsed.language_id)
            positional = [a for a in args if a.type != "keyword_argument"]
            keywords = {node_text(a.child_by_field_name("name"), src): a.child_by_field_name("value")
                        for a in args if a.type == "keyword_argument"}
            if last in _DJANGO and len(positional) >= 2 and (route := _literal(positional[0], src)) is not None:
                view = positional[1]
                head = ".".join(segments)
                if view.type == "call":  # SomeView.as_view(...)
                    vfunc = view.child_by_field_name("function")
                    vseg = flatten_reference_chain(vfunc, src, parsed.language_id) if vfunc is not None else None
                    if vseg and vseg[-1] == "as_view" and len(vseg) >= 2:
                        cls = self._resolve_class(vfunc.child_by_field_name("object"), parsed)
                        for verb in (*HTTP_VERBS, "dispatch"):
                            if cls and f"{cls}.{verb}" in self.funcs:
                                self._add(f"{cls}.{verb}", head, "URL", route, "call:django")
                    continue
                self._add(self._handler(view, parsed, _PY_FUNCTIONS), head, "URL", route, "call:django")
            elif last == "add_url_rule" and positional and (rule := _literal(positional[0], src)) is not None:
                view = keywords.get("view_func") or (positional[2] if len(positional) >= 3 else None)
                if view is not None:
                    self._add(self._handler(view, parsed, _PY_FUNCTIONS), ".".join(segments), "URL", rule, "call:flask")

    def _resolve_class(self, node, parsed) -> str | None:
        if node is None:
            return None
        segments = flatten_reference_chain(node, parsed.source, parsed.language_id)
        if not segments:
            return None
        try:
            module = self.builder._module_for_file(parsed)
            import_map = self.builder.import_map(parsed.path)
            q = self.builder._resolve_reference_chain(segments, module, import_map) if import_map else None
        except Exception:
            return None
        symbol = self.builder.symbol_table.get(q) if q else None
        return q if symbol is not None and symbol.kind == "class" else None


def find_call_registrations(builder: ConcreteGraphBuilder, funcs: set[str]) -> dict[str, object]:
    """`{handler qualified name: EntryPoint}` for the call-registered entry
    points of the index (test files skipped)."""
    scanner = _Scanner(builder, funcs)
    for path in sorted(builder._parsed_files):
        if builder._in_test_file(path):
            continue
        parsed = builder.parsed_file(path)
        if parsed is None or parsed.root_node is None:
            continue
        if parsed.language_id in _JS_LANGS:
            scanner.scan_js(parsed)
        elif parsed.language_id == LanguageID.PYTHON:
            scanner.scan_python(parsed)
    return scanner.found
