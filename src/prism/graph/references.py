"""Sub-pass 2d: references beyond calls and inheritance (Phase 2, Phase B; `harness/experiments/r4/PHASE2_B_DESIGN.md`).

PRISM's main graph (`ConcreteGraphBuilder.graph`) holds calls, constructor calls and inheritance. This pass records the
other ways one definition depends on another, in `builder.reference_graph`:

- **Sources:** the innermost indexed definition containing the use (a definition's own header, its decorators and
  annotations, belongs to it, as its line range does); outside every definition, the file's **module node**
  (`kind="module"`, `synthetic=True`), which exists only in the reference graph.
- **Targets:** indexed functions, methods and classes, reached through the file's imports, re-exports and aliases by
  the builder's own resolver (`_resolve_reference_chain`), the one call resolution uses. A name that does not resolve
  to an indexed definition gets no edge; a name bound locally (a parameter, an assignment, a loop or `with` or
  `except` target, a comprehension variable) is the local, not the global.
- **Edges:** one per (source, target), with `ref_kinds` (every kind of use between the two, a sorted list) and
  `count`. Kinds: `import`, `export` (`__all__`), `call` (a call the main graph does not already hold, such as one at
  module level), `annotation`, `generic`, `isinstance`, `except`, `decorator`, `base` (a base class without an
  `EXTENDS` edge), `attribute` (an attribute read on a definition, `Config.LIMIT`), `value` (passed, assigned,
  stored, returned, compared).

Each edge also keeps `lines` (the first lines of its uses, up to `MAX_EDGE_LINES`); a module node keeps its `file`.

The main graph is not changed: it keeps one edge per pair, and code across PRISM walks it as a call graph. The tools
show references through `references_to` (B10): `prism_change_context` and `prism_blast_radius` list them, labelled
by kind, apart from the callers. Python here; JS/TS next.
"""
from __future__ import annotations

import networkx as nx

DEF_KINDS = ("function", "method", "class")
_FUNC = "function_definition"
_SCOPE_BREAK = {"function_definition", "class_definition", "lambda"}
_COMPREHENSIONS = {"list_comprehension", "set_comprehension", "dictionary_comprehension", "generator_expression"}


def new_reference_graph() -> nx.DiGraph:
    return nx.DiGraph()


#: Lines kept per edge: where the uses are, for the tools to cite (the count keeps the total). 64 since Phase 3:
#: `prism_find_references` lists every use, and a module can use a name more than 5 times.
MAX_EDGE_LINES = 64


def add_reference(g: nx.DiGraph, src: str, dst: str, kind: str, module_node: str | None = None,
                  line: int | None = None, file: str | None = None) -> None:
    if src == dst:
        return
    if module_node is not None and src == module_node and src not in g:
        g.add_node(src, kind="module", synthetic=True, **({"file": file} if file else {}))
    if g.has_edge(src, dst):
        e = g.edges[src, dst]
        if kind not in e["ref_kinds"]:
            e["ref_kinds"] = sorted({*e["ref_kinds"], kind})
        e["count"] += 1
        if line is not None and line not in e["lines"] and len(e["lines"]) < MAX_EDGE_LINES:
            e["lines"] = sorted({*e["lines"], line})
    else:
        g.add_edge(src, dst, relation="REFERENCES", ref_kinds=[kind], count=1,
                   lines=[line] if line is not None else [])


# ---------------------------------------------------------------------------------------------- reading (B10)
#: How PRISM's tools name each kind of use, in the order they are listed.
KIND_LABELS = {
    "import": "imports it",
    "export": "lists it in `__all__`",
    "base": "subclasses it",
    "isinstance": "type-checks against it (isinstance)",
    "except": "catches it (except)",
    "annotation": "uses it as a type annotation",
    "generic": "uses it as a type parameter",
    "decorator": "uses it as a decorator",
    "call": "calls it (outside a function)",
    "value": "uses it as a value (passed, stored, assigned, returned)",
    "attribute": "reads an attribute of it",
}
_KIND_ORDER = {k: i for i, k in enumerate(KIND_LABELS)}


#: Main-graph relations that are uses of their target but not calls: listed with the references.
_STRUCTURAL = {"EXTENDS": "base", "IMPLEMENTS": "base", "EMBEDS": "base"}


def references_to(builder, qname: str, include_tests: bool = True, skip: set | None = None) -> list[dict]:
    """The definitions and modules that use `qname` other than by calling it, nearest kinds first:
    `{source, is_module, file, lines, kinds, count, is_test}` per source. Sources: the reference graph's edges into
    `qname`, and the main graph's inheritance edges (`EXTENDS`/`IMPLEMENTS`/`EMBEDS`, kind `base`). Sources in
    `skip` (the callers a tool already lists) are left out."""
    skip = skip or set()
    rows: dict[str, dict] = {}
    g = getattr(builder, "reference_graph", None)
    if g is not None and qname in g:
        for src, _dst, e in g.in_edges(qname, data=True):
            node = g.nodes[src]
            is_module = node.get("kind") == "module"
            rows[src] = {"source": src, "is_module": is_module, "file": node.get("file") if is_module else None,
                         "lines": list(e.get("lines", ())), "kinds": list(e["ref_kinds"]), "count": e["count"]}
    if qname in builder.graph:
        for src, _dst, e in builder.graph.in_edges(qname, data=True):
            kind = _STRUCTURAL.get(e.get("relation"))
            if kind is None:
                continue
            r = rows.setdefault(src, {"source": src, "is_module": False, "file": None, "lines": [], "kinds": [],
                                      "count": 0})
            if kind not in r["kinds"]:
                r["kinds"].append(kind)
                r["count"] += 1
                sym = builder.symbol_table.get(src)
                if sym is not None and sym.line_range[0] not in r["lines"]:
                    r["lines"] = sorted({*r["lines"], sym.line_range[0]})
    out = []
    for src, r in rows.items():
        if src in skip:
            continue
        if not r["is_module"]:
            sym = builder.symbol_table.get(src)
            r["file"] = sym.file if sym is not None else None
        r["is_test"] = bool(r["file"]) and builder._in_test_file(r["file"])
        if r["is_test"] and not include_tests:
            continue
        r["kinds"] = sorted(r["kinds"], key=lambda k: _KIND_ORDER.get(k, 99))
        out.append(r)
    out.sort(key=lambda r: (r["is_test"], min(_KIND_ORDER.get(k, 99) for k in r["kinds"]), r["source"]))
    return out


def describe_kinds(kinds) -> str:
    return "; ".join(KIND_LABELS.get(k, k) for k in kinds)


# ---------------------------------------------------------------------------------------------- Python
def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf8", "replace")


def _chain(node, src: bytes) -> list[str] | None:
    """`a.b.c` as ['a', 'b', 'c'] when it is a chain of plain names; else None."""
    if node.type == "identifier":
        return [_text(node, src)]
    if node.type == "attribute":
        obj, attr = node.child_by_field_name("object"), node.child_by_field_name("attribute")
        head = _chain(obj, src) if obj is not None else None
        return head + [_text(attr, src)] if head is not None and attr is not None else None
    return None


def _bound_names(target, src: bytes, out: set) -> None:
    """Names an assignment-like target binds (`a`, `a, b`, `[a, *b]`); attributes and subscripts bind none."""
    if target is None:
        return
    if target.type == "identifier":
        out.add(_text(target, src))
    elif target.type in ("pattern_list", "tuple_pattern", "list_pattern", "tuple", "list", "list_splat_pattern",
                         "parenthesized_expression", "as_pattern_target"):
        for c in target.named_children:
            _bound_names(c, src, out)


def _locals_of(func, src: bytes) -> set:
    """Names bound inside a function or lambda: parameters, and bindings in its body (not in nested functions)."""
    names: set = set()
    params = func.child_by_field_name("parameters")
    if params is not None:
        for p in params.named_children:
            if p.type == "identifier":
                names.add(_text(p, src))
            elif p.type in ("typed_parameter", "list_splat_pattern", "dictionary_splat_pattern"):
                ident = next((c for c in p.named_children if c.type == "identifier"), None)
                if ident is not None:
                    names.add(_text(ident, src))
            elif p.type in ("default_parameter", "typed_default_parameter"):
                n = p.child_by_field_name("name")
                if n is not None:
                    names.add(_text(n, src))
    body = func.child_by_field_name("body")
    stack = [body] if body is not None else []
    while stack:
        n = stack.pop()
        t = n.type
        if t in ("function_definition", "class_definition"):
            nm = n.child_by_field_name("name")
            if nm is not None:
                names.add(_text(nm, src))
            continue                                         # its own scope
        if t == "lambda":
            continue
        if t in ("assignment", "augmented_assignment"):
            _bound_names(n.child_by_field_name("left"), src, names)
        elif t in ("for_statement", "for_in_clause"):
            _bound_names(n.child_by_field_name("left"), src, names)
        elif t == "as_pattern":                              # with ... as x / except E as x
            alias = n.child_by_field_name("alias")
            _bound_names(alias.named_children[0] if alias is not None and alias.named_children else alias, src, names)
        elif t == "named_expression":
            _bound_names(n.child_by_field_name("name"), src, names)
        elif t in ("import_statement", "import_from_statement"):
            for c in n.named_children:
                if c.type == "aliased_import":
                    a = c.child_by_field_name("alias")
                    if a is not None:
                        names.add(_text(a, src))
                elif c.type == "dotted_name" and c != n.child_by_field_name("module_name"):
                    names.add(_text(c, src).split(".")[0])
        elif t in ("global_statement", "nonlocal_statement"):
            for c in n.named_children:
                names.discard(_text(c, src))
        stack.extend(n.named_children)
    return names


class _PythonFile:
    def __init__(self, builder, parsed, module: str, import_map):
        self.b, self.parsed, self.module, self.import_map = builder, parsed, module, import_map
        self.src = parsed.source
        self.g = builder.reference_graph
        self.module_node = module[:-9] if module.endswith(".__init__") else module
        self.defs = sorted(
            ((s.line_range[0], s.line_range[1], q) for q in builder._def_nodes
             if (s := builder.symbol_table.get(q)) is not None and s.file == parsed.path and s.kind in DEF_KINDS),
            key=lambda x: (x[0], -x[1]))
        self._locals: dict[int, set] = {}
        self._typed_maps: dict = {}

    # -------------------------------------------------------------- resolution
    def source_of(self, node) -> str:
        line = node.start_point[0] + 1
        best = None
        for lo, hi, q in self.defs:
            if lo <= line <= hi and (best is None or hi - lo <= best[1] - best[0]):
                best = (lo, hi, q)
        return best[2] if best else self.module_node

    def shadowed(self, node, name: str) -> bool:
        """`name` at `node` is bound locally (in an enclosing function, lambda or comprehension)."""
        p = node.parent
        while p is not None:
            if p.type in (_FUNC, "lambda"):
                key = p.id
                if key not in self._locals:
                    self._locals[key] = _locals_of(p, self.src)
                if name in self._locals[key]:
                    return True
            elif p.type in _COMPREHENSIONS:
                bound: set = set()
                for c in p.named_children:
                    if c.type == "for_in_clause":
                        _bound_names(c.child_by_field_name("left"), self.src, bound)
                if name in bound:
                    return True
            p = p.parent
        return False

    def target(self, segments: list[str]) -> tuple[str, list[str]] | None:
        """The indexed definition a chain names (its longest prefix that is one), and the segments after it."""
        resolved = self.b._resolve_reference_chain(segments, self.module, self.import_map)
        if resolved is None:
            return None
        resolved = _follow_aliases(self.b, resolved)
        parts = resolved.split(".")
        n_rest = 0
        while parts:
            q = ".".join(parts)
            s = self.b.symbol_table.get(q)
            if s is not None and s.kind in DEF_KINDS:
                return q, segments[len(segments) - n_rest:] if n_rest else []
            parts.pop()
            n_rest += 1
            if n_rest >= len(segments):                       # never resolve to a module or package prefix
                return None
        return None

    # -------------------------------------------------------------- variable types
    def _maps(self, src: str):
        """The instance-type maps call resolution (2b) uses where `src`'s code runs: `(enclosing class, class map,
        function map)`, built once per definition; a module's own bindings at module level."""
        if src in self._typed_maps:
            return self._typed_maps[src]
        from prism.graph.symbol_table import InstanceTypeMap
        b = self.b
        sym = b.symbol_table.get(src)
        empty = InstanceTypeMap()
        if sym is None:                                   # module level
            maps = (None, empty, b._module_instance_map(self.parsed, self.module, self.import_map))
        elif sym.kind in ("function", "method"):
            cls = sym.enclosing_class
            class_map = b._build_class_instance_map(cls, self.parsed, self.module, self.import_map) if cls else empty
            func_map = b._build_function_instance_map(b._def_nodes[src], self.parsed, self.module, self.import_map,
                                                      cls, class_map)
            b._seed_module_bindings(func_map, self.parsed, self.module, self.import_map)
            maps = (cls, class_map, func_map)
        else:                                             # a class body: its own attributes, no `self`
            maps = (src, b._build_class_instance_map(src, self.parsed, self.module, self.import_map), empty)
        self._typed_maps[src] = maps
        return maps

    def typed_target(self, node, segments: list[str]) -> tuple[str, list[str]] | None:
        """The definition a chain through a typed variable names, resolved by call resolution's own resolver
        (`_resolve_segments`) with the same maps: the longest prefix that is a function, method or class, kept only
        when the resolution is clean (not tentative, ambiguous, a builtin receiver or an unbound local)."""
        b = self.b
        cls, class_map, func_map = self._maps(self.source_of(node))
        from prism.parser.lang_config import SELF_TOKEN_TEXT
        self_tokens = SELF_TOKEN_TEXT[self.parsed.language_id]           # as call resolution has them
        for k in range(len(segments), 1, -1):
            try:
                r = b._resolve_segments(segments[:k], self.module, cls, self.import_map, class_map, func_map,
                                        self_tokens, self.parsed.language_id)
            except (KeyError, AttributeError, RecursionError, ValueError):
                return None
            if r is None or b._last_resolution_was_tentative or b._last_resolution_was_ambiguous \
                    or b._last_resolution_was_builtin_receiver or b._last_resolution_was_unbound_local:
                continue
            r = _follow_aliases(b, r)
            s = b.symbol_table.get(r)
            if s is not None and s.kind in DEF_KINDS:
                return r, segments[k:]
        return None

    def add(self, node, tgt: str, kind: str) -> None:
        src = self.source_of(node)
        if kind == "call" and self.b.graph.has_edge(src, tgt):
            return                                            # the main graph already holds this call
        if kind == "base" and self.b.graph.has_edge(src, tgt):
            return
        add_reference(self.g, src, tgt, kind, self.module_node, line=node.start_point[0] + 1, file=self.parsed.path)

    # -------------------------------------------------------------- the walk
    def run(self) -> None:
        root = self.parsed.root_node
        stack = [root]
        while stack:
            n = stack.pop()
            t = n.type
            if t in ("import_from_statement", "import_statement"):
                self.imports(n)
                continue
            if t == "expression_statement" and self.all_entries(n):
                continue
            if t in ("attribute", "identifier"):
                self.use(n)
                continue                                      # an attribute chain is handled whole
            if t == "keyword_argument":                       # its name is not a reference; its value is
                v = n.child_by_field_name("value")
                if v is not None:
                    stack.append(v)
                continue
            if t in ("function_definition", "class_definition"):
                for c in n.named_children:
                    if c != n.child_by_field_name("name"):
                        stack.append(c)
                continue
            if t in ("parameters", "lambda_parameters"):
                for p in n.named_children:                     # only annotations and defaults are uses
                    for f in ("type", "value"):
                        c = p.child_by_field_name(f)
                        if c is not None:
                            stack.append(c)
                continue
            if t in ("assignment", "augmented_assignment"):
                left = n.child_by_field_name("left")
                for c in n.named_children:
                    if c != left or (left is not None and left.type not in (
                            "identifier", "pattern_list", "tuple_pattern", "list_pattern")):
                        stack.append(c)
                continue
            if t in ("for_statement", "for_in_clause"):
                left = n.child_by_field_name("left")
                stack.extend(c for c in n.named_children if c != left)
                continue
            if t in ("global_statement", "nonlocal_statement", "string", "comment"):
                continue
            stack.extend(n.named_children)

    def imports(self, n) -> None:
        module_name = n.child_by_field_name("module_name")
        for c in n.named_children:
            if c == module_name:
                continue
            name_node = c.child_by_field_name("name") if c.type == "aliased_import" else c
            if name_node is None or name_node.type != "dotted_name":
                continue
            local = _text(c.child_by_field_name("alias"), self.src) if c.type == "aliased_import" else None
            if n.type == "import_from_statement":
                key = local or _text(name_node, self.src)
                hit = self.target([key])
            else:
                hit = self.target(_text(name_node, self.src).split("."))
            if hit is not None and not hit[1]:
                self.add(n, hit[0], "import")

    def all_entries(self, stmt) -> bool:
        """`__all__ = [...]` / `__all__ += [...]` at module level: each name an `export`."""
        a = stmt.named_children[0] if stmt.named_children else None
        if a is None or a.type not in ("assignment", "augmented_assignment") or stmt.parent is None \
                or stmt.parent.type != "module":
            return False
        left = a.child_by_field_name("left")
        if left is None or _text(left, self.src) != "__all__":
            return False
        right = a.child_by_field_name("right")
        for s in (right.named_children if right is not None else []):
            if s.type == "string":
                content = "".join(_text(c, self.src) for c in s.named_children if c.type == "string_content")
                hit = self.target([content]) if content.isidentifier() else None
                if hit is not None and not hit[1]:
                    self.add(stmt, hit[0], "export")
        return True

    def use(self, n) -> None:
        segs = _chain(n, self.src)
        if segs is None:                                      # `f().x`, `a[0].b`: look inside for plain names
            for c in n.named_children:
                if c.type in ("attribute", "identifier"):
                    self.use(c)
                elif c.type not in ("identifier",):
                    for cc in c.named_children:
                        if cc.type in ("attribute", "identifier"):
                            self.use(cc)
                        else:
                            self._walk_expr(cc)
            return
        local = self.shadowed(n, segs[0])
        if local and len(segs) < 2:
            return
        hit = None if local else self.target(segs)
        if hit is None and len(segs) >= 2:
            hit = self.typed_target(n, segs)              # `self.client.send`, `client.send` on a typed variable
        if hit is None:
            return
        tgt, rest = hit
        self.add(n, tgt, self.kind(n, bool(rest)))

    def _walk_expr(self, n) -> None:
        stack = [n]
        while stack:
            x = stack.pop()
            if x.type in ("attribute", "identifier"):
                self.use(x)
            elif x.type not in ("string", "comment"):
                stack.extend(x.named_children)

    def kind(self, n, has_rest: bool) -> str:
        if has_rest:
            p = n.parent
            if p is not None and p.type == "call" and p.child_by_field_name("function") == n:
                return "call"                                 # Foo.method(...) through the class
            return "attribute"
        p, c = n.parent, n
        # walk up through wrappers to the construct that gives the use its meaning
        while p is not None:
            t = p.type
            if t == "call" and p.child_by_field_name("function") == c:
                return "decorator" if p.parent is not None and p.parent.type == "decorator" else "call"
            if t == "decorator":
                return "decorator"
            if t == "type":
                return "annotation"
            if t == "argument_list" and p.parent is not None and p.parent.type == "class_definition":
                return "base"
            if t == "argument_list" and p.parent is not None and p.parent.type == "call":
                fn = p.parent.child_by_field_name("function")
                if fn is not None and fn.type == "identifier" and _text(fn, self.src) in ("isinstance", "issubclass") \
                        and c != p.named_children[0]:
                    return "isinstance"
                return "value"
            if t == "except_clause":
                return "except" if c.type != "block" else "value"
            if t == "subscript" and p.child_by_field_name("value") == c:
                return "generic"
            if t in ("tuple", "parenthesized_expression"):
                c, p = p, p.parent
                continue
            return "value"
        return "value"


def collect_python_aliases(builder, parsed, module: str, import_map) -> None:
    """Module-level `Name = a.b.c` (a plain name or chain on the right): `module.Name` is an alias of what the chain
    resolves to. Kept in `builder.reference_aliases` and followed by `target` (an `isinstance(v, Alias)` reaches the
    class). Unconditional and conditional (`if/else`, `try/except`) bindings alike."""
    src = parsed.source
    mod = module[:-9] if module.endswith(".__init__") else module
    stack = list(parsed.root_node.named_children)
    while stack:
        n = stack.pop()
        if n.type in ("if_statement", "try_statement", "else_clause", "elif_clause", "except_clause", "block",
                      "finally_clause"):
            stack.extend(n.named_children)
            continue
        if n.type != "expression_statement" or not n.named_children or n.named_children[0].type != "assignment":
            continue
        a = n.named_children[0]
        left, right = a.child_by_field_name("left"), a.child_by_field_name("right")
        if left is None or right is None or left.type != "identifier":
            continue
        segs = _chain(right, src)
        if segs is None:
            continue
        name = _text(left, src)
        resolved = builder._resolve_reference_chain(segs, module, import_map)
        if resolved is not None and resolved != f"{mod}.{name}":
            builder.reference_aliases.setdefault(f"{mod}.{name}", resolved)


def _follow_aliases(builder, qname: str, depth: int = 0) -> str:
    """`qname` with its longest prefix that is a module-level alias replaced by the alias's target (at most 5 hops)."""
    aliases = getattr(builder, "reference_aliases", {})
    if depth >= 5 or not aliases:
        return qname
    parts = qname.split(".")
    for k in range(len(parts), 0, -1):
        prefix = ".".join(parts[:k])
        if builder.symbol_table.get(prefix) is not None and builder.symbol_table.get(prefix).kind in DEF_KINDS:
            return qname                                      # a real definition: never replaced by an alias
        if prefix in aliases:
            return _follow_aliases(builder, ".".join([aliases[prefix], *parts[k:]]), depth + 1)
    return qname


def link_python_references(builder, parsed, module: str, import_map) -> None:
    _PythonFile(builder, parsed, module, import_map).run()
