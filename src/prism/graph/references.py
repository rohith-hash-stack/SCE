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

The main graph is not changed: it keeps one edge per pair, and code across PRISM walks it as a call graph. Surfacing
references in PRISM's tools is a later step (B10). Python here; JS/TS next.
"""
from __future__ import annotations

import networkx as nx

DEF_KINDS = ("function", "method", "class")
_FUNC = "function_definition"
_SCOPE_BREAK = {"function_definition", "class_definition", "lambda"}
_COMPREHENSIONS = {"list_comprehension", "set_comprehension", "dictionary_comprehension", "generator_expression"}


def new_reference_graph() -> nx.DiGraph:
    return nx.DiGraph()


def add_reference(g: nx.DiGraph, src: str, dst: str, kind: str, module_node: str | None = None) -> None:
    if src == dst:
        return
    if module_node is not None and src == module_node and src not in g:
        g.add_node(src, kind="module", synthetic=True)
    if g.has_edge(src, dst):
        e = g.edges[src, dst]
        if kind not in e["ref_kinds"]:
            e["ref_kinds"] = sorted({*e["ref_kinds"], kind})
        e["count"] += 1
    else:
        g.add_edge(src, dst, relation="REFERENCES", ref_kinds=[kind], count=1)


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

    def add(self, node, tgt: str, kind: str) -> None:
        src = self.source_of(node)
        if kind == "call" and self.b.graph.has_edge(src, tgt):
            return                                            # the main graph already holds this call
        if kind == "base" and self.b.graph.has_edge(src, tgt):
            return
        add_reference(self.g, src, tgt, kind, self.module_node)

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
        if self.shadowed(n, segs[0]):
            return
        hit = self.target(segs)
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
