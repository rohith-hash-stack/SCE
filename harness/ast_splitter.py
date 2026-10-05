"""Tree-sitter splitters: a source file -> compilable chunks of at most
`max_tokens` tokens (800 for Arm 1). Shared by Arm 1 (RAG) and Arm 2
(Priompt, M2). `CodeSplitter` routes by extension: `.py` to the Python
splitter below, `.ts/.tsx/.js/.jsx` to `TypeScriptSplitter` (M4; its own
docstring covers the TypeScript rules).

Chunks
- `function` / `method`: one definition with its decorators and docstring.
  A method chunk is prefixed with its class header line(s) (and every
  enclosing class header for nested classes), so it compiles on its own and
  keeps its class context.
- `function_part`: a piece of an oversized function. The function is split
  only at its FIRST-LEVEL statements: each first-level compound statement
  (if/for/while/try/with/match, sync or async) is an atomic unit, runs of
  simple statements form units, and units are packed in order up to the
  cap. Nothing deeper is split. Each part is prefixed with the enclosing
  class headers and the `def` header, so it compiles on its own. The first
  part keeps the decorators and docstring.
- `class_preamble`: a class's header plus its docstring and class-level
  statements (assignments etc.), everything in the class that is not a
  method or nested class. Emitted only when there is something besides the
  header.
- `module_block`: runs of module-level statements between definitions
  (imports, constants, top-level code).

Known-bug guards
1. Preamble and unit boundaries come from AST nodes (rows of the nodes
   themselves), never from indices into a list of strings.
2. `parent_prefix` (the enclosing class headers) is threaded through the
   class recursion, so nested classes are named `Outer.Inner` and their
   methods carry both headers.
3. Splitting is gated on the calibrated token count (the harness tokenizer
   counts the chunk exactly as delivered, prefix included).

A unit larger than the cap is emitted whole and flagged `oversized` (the
spec forbids splitting deeper). A split whose parts would not compile on
their own (e.g. a `nonlocal` whose binding landed in another part) falls
back to the whole function, flagged `oversized` with reason `unsplittable`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import tree_sitter_python as tspython
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node, Parser

PY_LANGUAGE = Language(tspython.language())
COMPOUND = {
    "if_statement", "for_statement", "while_statement", "try_statement", "with_statement",
    "match_statement",
}
DEF_TYPES = {"function_definition", "class_definition", "decorated_definition"}


@dataclass
class Chunk:
    qualified_name: str          # FQN of the owning definition (module for module blocks)
    kind: str                    # function | method | function_part | class_preamble | module_block
    file: str                    # path relative to the repository root
    start_line: int              # 1-based, first original body line (prefix excluded)
    end_line: int                # 1-based, inclusive
    content: str                 # exactly what is delivered (prefix included)
    token_count: int
    prefix_lines: int = 0        # synthetic context lines at the top of `content`
    part: int = 1
    n_parts: int = 1
    oversized: bool = False
    notes: list[str] = field(default_factory=list)
    source_rows: list[int] = field(default_factory=list)   # 0-based original rows included
    #: "python" or "typescript" (TypeScript and JavaScript files)
    language: str = "python"
    #: TypeScript only: named functions nested in the chunk's definition
    #: (inner functions, object-literal methods), as `<qualified_name>.<name>`
    inner_symbols: list[str] = field(default_factory=list)
    #: TypeScript only: the chunk's signature stub (header, `// ...`, closing
    #: rows), "" when it has none; Arm 2 computes Python stubs itself
    signature: str = ""

    @property
    def source_id(self) -> str:
        suffix = f"#part{self.part}of{self.n_parts}" if self.n_parts > 1 else ""
        return f"{self.file}:{self.start_line}-{self.end_line}{suffix}"


PY_EXTENSIONS = (".py",)
TS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")
LANGUAGE_EXTENSIONS = {"python": PY_EXTENSIONS, "typescript": TS_EXTENSIONS}


def language_of(path: str) -> str | None:
    """The splitter language for a file, by extension; None when neither."""
    if path.endswith(PY_EXTENSIONS):
        return "python"
    if path.endswith(TS_EXTENSIONS):
        return "typescript"
    return None


def module_name(rel_path: str) -> str:
    """Dotted module path: `fastapi/dependencies/utils.py` ->
    `fastapi.dependencies.utils`; a package's `__init__.py` (Python) or
    `index.ts`/`index.js` (TypeScript/JavaScript) is the package itself."""
    if rel_path.endswith(".py"):
        mod, index = rel_path[:-3], "__init__"
    else:
        ext = next((e for e in TS_EXTENSIONS if rel_path.endswith(e)), None)
        mod, index = (rel_path[:-len(ext)], "index") if ext else (rel_path, None)
    parts = mod.replace(os.sep, "/").split("/")
    if parts and index and parts[-1] == index:
        parts = parts[:-1]
    return ".".join(p for p in parts if p)


def _compiles(text: str) -> bool:
    try:
        compile(text, "<chunk>", "exec", dont_inherit=True)
        return True
    except (SyntaxError, ValueError):
        return False


class PythonSplitter:
    #: statements that form an atomic unit of their own when a function is split
    compound_types = COMPOUND
    def_types = DEF_TYPES

    def __init__(self, tokenizer, max_tokens: int = 800) -> None:
        self.tok = tokenizer
        self.max_tokens = max_tokens
        self.parser = Parser(PY_LANGUAGE)

    # ------------------------------------------------------------------ api
    def split_file(self, path: str, repo_root: str) -> list[Chunk]:
        with open(path, "rb") as fh:
            source = fh.read()
        rel = os.path.relpath(path, repo_root).replace(os.sep, "/")
        return self.split_source(source, rel)

    def split_source(self, source: bytes, rel_path: str) -> list[Chunk]:
        self._lines = source.decode("utf-8", errors="replace").split("\n")
        self._rel = rel_path
        self._module = module_name(rel_path)
        tree = self.parser.parse(source)
        chunks: list[Chunk] = []
        pending: list[Node] = []
        for child in tree.root_node.named_children:
            if self._definition(child) is not None:
                chunks += self._module_blocks(pending)
                pending = []
                chunks += self._definition_chunks(child, prefix_rows=[], qual_prefix=self._module)
            else:
                pending.append(child)
        chunks += self._module_blocks(pending)
        return chunks

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _definition(node: Node) -> Node | None:
        """The function/class node behind `node` (unwrapping decorators)."""
        if node.type == "decorated_definition":
            return node.child_by_field_name("definition")
        if node.type in ("function_definition", "class_definition"):
            return node
        return None

    def _rows_text(self, rows: list[int]) -> str:
        return "\n".join(self._lines[r] for r in rows)

    def _header_rows(self, defn: Node) -> list[int]:
        """Rows of a def/class header: from the definition keyword (not its
        decorators) up to the row before its body starts."""
        body = defn.child_by_field_name("body")
        start = defn.start_point[0]
        end = body.start_point[0] if body is not None else defn.end_point[0] + 1
        return list(range(start, max(end, start + 1)))

    def _chunk(self, qual, kind, rows_prefix, rows_body, part=1, n_parts=1, oversized=False, notes=None,
               rows_suffix=()) -> Chunk:
        rows_suffix = list(rows_suffix)
        content = self._rows_text(rows_prefix + rows_body + rows_suffix)
        return Chunk(
            qualified_name=qual, kind=kind, file=self._rel,
            start_line=rows_body[0] + 1, end_line=rows_body[-1] + 1, content=content,
            token_count=self.tok.count(content), prefix_lines=len(rows_prefix), part=part, n_parts=n_parts,
            oversized=oversized, notes=list(notes or []),
            source_rows=sorted(set(rows_prefix + rows_body + rows_suffix)),
        )

    def _is_compound(self, n: Node) -> bool:
        return n.type in self.compound_types or n.type in self.def_types

    def _units(self, nodes: list[Node]) -> list[list[int]]:
        """Group statement nodes into atomic row ranges: each compound
        statement alone; consecutive simple statements (and comments)
        together; nodes sharing a row never separated."""
        units: list[list[int]] = []
        current: list[int] = []
        for n in nodes:
            rows = list(range(n.start_point[0], n.end_point[0] + 1))
            is_compound = self._is_compound(n)
            shares_row = bool(current) and rows[0] <= current[-1]
            if shares_row:
                current += [r for r in rows if r > current[-1]]
                continue
            if is_compound:
                if current:
                    units.append(current)
                units.append(rows)
                current = []
            else:
                if current and rows[0] > current[-1] + 1:
                    # keep blank lines between simple statements inside the unit
                    current += list(range(current[-1] + 1, rows[0]))
                current += rows
        if current:
            units.append(current)
        # glue a unit to the previous one if they share a row (comment on
        # the line of a compound statement's end, etc.)
        merged: list[list[int]] = []
        for u in units:
            if merged and u[0] <= merged[-1][-1]:
                merged[-1] += [r for r in u if r > merged[-1][-1]]
            else:
                merged.append(u)
        return merged

    def _pack(self, units: list[list[int]], prefix_rows: list[int],
              suffix_rows: list[int] = ()) -> list[tuple[list[int], bool]]:
        """Greedy in-order packing of units into fragments under the cap.
        Returns (rows, oversized) per fragment. `suffix_rows` (closing
        braces, TypeScript only) count against the cap like the prefix."""
        suffix_rows = list(suffix_rows)
        frags: list[tuple[list[int], bool]] = []
        cur: list[int] = []
        for u in units:
            cand = cur + (list(range(cur[-1] + 1, u[0])) if cur else []) + u
            if cur and self.tok.count(self._rows_text(prefix_rows + cand + suffix_rows)) > self.max_tokens:
                frags.append((cur, False))
                cur = list(u)
            else:
                cur = cand
        if cur:
            frags.append((cur, False))
        return [(rows, self.tok.count(self._rows_text(prefix_rows + rows + suffix_rows)) > self.max_tokens)
                for rows, _ in frags]

    # -------------------------------------------------------- module level
    def _module_blocks(self, nodes: list[Node]) -> list[Chunk]:
        if not nodes:
            return []
        frags = self._pack(self._units(nodes), [])
        n = len(frags)
        return [self._chunk(self._module, "module_block", [], rows, i + 1, n, over)
                for i, (rows, over) in enumerate(frags)]

    # --------------------------------------------------------- definitions
    def _definition_chunks(self, node: Node, prefix_rows: list[int], qual_prefix: str) -> list[Chunk]:
        defn = self._definition(node)
        assert defn is not None, "caller passes definition nodes only"
        name_node = defn.child_by_field_name("name")
        name = name_node.text.decode("utf-8", errors="replace") if name_node is not None and name_node.text else "?"
        qual = f"{qual_prefix}.{name}"
        if defn.type == "class_definition":
            return self._class_chunks(node, defn, prefix_rows, qual)
        return self._function_chunks(node, defn, prefix_rows, qual, method=bool(prefix_rows))

    def _class_chunks(self, node: Node, defn: Node, prefix_rows: list[int], qual: str) -> list[Chunk]:
        header = self._header_rows(defn)
        # parent_prefix threaded: this class's header joins the enclosing ones
        child_prefix = prefix_rows + header
        body = defn.child_by_field_name("body")
        members = body.named_children if body is not None else []
        preamble = [m for m in members if self._definition(m) is None]
        chunks: list[Chunk] = []
        has_content = any(m.type != "comment" for m in preamble)
        if has_content:
            # decorators of the class itself belong with its preamble
            dec_rows = list(range(node.start_point[0], defn.start_point[0])) if node is not defn else []
            frags = self._pack(self._units(preamble), prefix_rows + dec_rows + header)
            n = len(frags)
            for i, (rows, over) in enumerate(frags):
                chunks.append(self._chunk(qual, "class_preamble", prefix_rows + dec_rows + header, rows, i + 1, n, over))
        for m in members:
            if self._definition(m) is not None:
                chunks += self._definition_chunks(m, child_prefix, qual)
        return chunks

    def _function_chunks(self, node: Node, defn: Node, prefix_rows: list[int], qual: str, method: bool) -> list[Chunk]:
        kind = "method" if method else "function"
        full_rows = list(range(node.start_point[0], node.end_point[0] + 1))   # decorators included
        whole = self._chunk(qual, kind, prefix_rows, full_rows)
        if whole.token_count <= self.max_tokens:
            return [whole]
        body = defn.child_by_field_name("body")
        stmts = body.named_children if body is not None else []
        header = self._header_rows(defn)
        if not stmts or body.start_point[0] == defn.start_point[0]:
            whole.oversized = True
            return [whole]
        dec_rows = list(range(node.start_point[0], defn.start_point[0]))
        units = self._units(stmts)
        frags = self._pack(units, prefix_rows + header)
        if len(frags) <= 1:
            whole.oversized = True
            return [whole]
        n = len(frags)
        parts = []
        for i, (rows, over) in enumerate(frags):
            pre = prefix_rows + (dec_rows if i == 0 else []) + header
            parts.append(self._chunk(qual, "function_part", pre, rows, i + 1, n, over))
        if not all(_compiles(p.content) for p in parts) and _compiles(whole.content):
            whole.oversized = True
            whole.notes.append("unsplittable: parts do not compile on their own")
            return [whole]
        return parts


# --------------------------------------------------------------------------
# TypeScript / JavaScript
# --------------------------------------------------------------------------
TS_LANGUAGE = Language(tstypescript.language_typescript())
#: .tsx, .js and .jsx: the TSX grammar (a superset of JavaScript with JSX)
TSX_LANGUAGE = Language(tstypescript.language_tsx())
#: values that make a declaration or an assignment a function definition
TS_FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}
TS_FUNCTION_DECLS = {"function_declaration", "generator_function_declaration"}
TS_CLASS_DECLS = {"class_declaration", "abstract_class_declaration"}
TS_COMPOUND = {
    "if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement", "try_statement",
    "switch_statement", "labeled_statement", "statement_block",
}
TS_DEF_TYPES = TS_FUNCTION_DECLS | TS_CLASS_DECLS


def _ts_text(node: Node | None) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None and node.text else ""


def _ts_function_value(node: Node | None) -> Node | None:
    """The function behind an initializer or an assignment's right-hand
    side, through parentheses and chained assignments
    (`var proto = module.exports = function (options) {...}`)."""
    while node is not None:
        if node.type in TS_FUNCTION_VALUES:
            return node
        if node.type == "parenthesized_expression":
            node = node.named_children[0] if node.named_children else None
        elif node.type == "assignment_expression":
            node = node.child_by_field_name("right")
        else:
            return None
    return None


class TypeScriptSplitter(PythonSplitter):
    """Tree-sitter TypeScript/JavaScript splitter, the counterpart of
    `PythonSplitter` with the same chunk kinds, cap and packing.

    Definitions (top level, also inside `export`):
    - `function_declaration` (and generator functions): `function`;
    - `class_declaration`: a `class_preamble` (header, fields and other
      non-method members, closing brace) and one `method` chunk per
      `method_definition` or arrow-function field;
    - `const f = () => {...}` / `var f = function () {...}` (arrow functions
      and function expressions bound to a name): `function` named by the
      variable;
    - `x.y = function name() {...}` / `exports.y = (...) => {...}`
      (CommonJS and prototype assignments, the Express style): `function`
      named by the assigned property, so `app.init = function init` in
      `lib/application.js` is `lib.application.init`.
    Everything else at the top level (imports, types, interfaces,
    `module.exports = ...`, calls) is packed into `module_block`s.

    A JSDoc or line comment directly above a definition (no blank line)
    belongs to it. A method chunk is wrapped in its class header and the
    class's closing row, so it parses on its own. An oversized function is
    split at its body's first-level statements; each part carries the header
    rows (through the body's `{`) and the closing rows. Parts that do not
    parse on their own fall back to the whole function (`oversized`,
    `unsplittable`), as in the Python splitter.
    """

    compound_types = TS_COMPOUND
    def_types = TS_DEF_TYPES

    def __init__(self, tokenizer, max_tokens: int = 800) -> None:
        self.tok = tokenizer
        self.max_tokens = max_tokens
        self._parsers = {"ts": Parser(TS_LANGUAGE), "tsx": Parser(TSX_LANGUAGE)}

    def _is_compound(self, n: Node) -> bool:
        """Multi-row statements are atomic units too: JavaScript test files
        are runs of multi-row calls (`it("...", function () {...})`), and
        TypeScript modules runs of multi-row types, that would otherwise
        merge into one oversized unit."""
        return super()._is_compound(n) or n.end_point[0] > n.start_point[0]

    def _parser_for(self, rel_path: str) -> Parser:
        return self._parsers["ts" if rel_path.endswith(".ts") else "tsx"]

    def parses(self, text: str, rel_path: str | None = None) -> bool:
        """True when `text` parses without a syntax error under the grammar
        of `rel_path` (of the file being split when None)."""
        parser = self._parser_for(rel_path or self._rel)
        return not parser.parse(text.encode("utf-8")).root_node.has_error

    # ------------------------------------------------------------------ api
    def split_source(self, source: bytes, rel_path: str) -> list[Chunk]:
        self._lines = source.decode("utf-8", errors="replace").split("\n")
        self._rel = rel_path
        self._module = module_name(rel_path)
        tree = self._parser_for(rel_path).parse(source)
        chunks: list[Chunk] = []
        pending: list[Node] = []
        for child in tree.root_node.named_children:
            if self._top_definition(child) is not None:
                lead = self._leading_comments(pending, child)
                pending = pending[:len(pending) - len(lead)]
                chunks += self._module_blocks(pending)
                pending = []
                chunks += self._ts_definition_chunks(child, lead)
            else:
                pending.append(child)
        chunks += self._module_blocks(pending)
        for c in chunks:
            c.language = "typescript"
        return chunks

    # ------------------------------------------------------------- helpers
    def _top_definition(self, node: Node) -> tuple[str, str, Node] | None:
        """(name, "function" | "class", definition node) for a top-level
        statement that defines a function or class; None otherwise."""
        if node.type == "export_statement":
            inner = node.child_by_field_name("declaration") or node.child_by_field_name("value")
            if inner is None:
                return None
            found = self._top_definition(inner)
            if found is None and inner.type in TS_FUNCTION_VALUES | {"class"}:     # export default function () {}
                return ("default", "class" if inner.type == "class" else "function", inner)
            return found
        if node.type in TS_FUNCTION_DECLS:
            return (_ts_text(node.child_by_field_name("name")) or "default", "function", node)
        if node.type in TS_CLASS_DECLS:
            return (_ts_text(node.child_by_field_name("name")) or "default", "class", node)
        if node.type in ("lexical_declaration", "variable_declaration"):
            decls = [c for c in node.named_children if c.type == "variable_declarator"]
            if len(decls) == 1:
                fn = _ts_function_value(decls[0].child_by_field_name("value"))
                name = _ts_text(decls[0].child_by_field_name("name"))
                if fn is not None and name and decls[0].child_by_field_name("name").type == "identifier":
                    return (name, "function", fn)
            return None
        if node.type == "expression_statement" and node.named_children:
            expr = node.named_children[0]
            if expr.type == "assignment_expression":
                fn = _ts_function_value(expr.child_by_field_name("right"))
                if fn is None:
                    return None
                left = expr.child_by_field_name("left")
                name = _ts_text(left.child_by_field_name("property")) if left.type == "member_expression" \
                    else _ts_text(left) if left.type == "identifier" else ""
                if name == "exports":            # module.exports = function name() {...}
                    name = _ts_text(fn.child_by_field_name("name")) or ""
                if name:
                    return (name, "function", fn)
        return None

    def _leading_comments(self, pending: list[Node], node: Node) -> list[Node]:
        """The comment nodes at the end of `pending` that sit directly above
        `node` (each ending on the row before the next)."""
        lead: list[Node] = []
        nxt = node.start_point[0]
        for p in reversed(pending):
            if p.type != "comment" or p.end_point[0] != nxt - 1:
                break
            lead.insert(0, p)
            nxt = p.start_point[0]
        return lead

    def _inner_symbols(self, node: Node, prefix: str) -> list[str]:
        """Named functions nested in `node`, as `<prefix>.<chain>`."""
        out: list[str] = []

        def walk(n: Node, pre: str) -> None:
            for c in n.named_children:
                name = ""
                if c.type in TS_FUNCTION_DECLS or c.type == "method_definition":
                    name = _ts_text(c.child_by_field_name("name"))
                elif c.type in ("variable_declarator", "pair", "public_field_definition"):
                    key = c.child_by_field_name("name") or c.child_by_field_name("key")
                    val = c.child_by_field_name("value")
                    if _ts_function_value(val) is not None and key is not None \
                            and key.type in ("identifier", "property_identifier"):
                        name = _ts_text(key)
                if name:
                    out.append(f"{pre}.{name}")
                    walk(c, f"{pre}.{name}")
                else:
                    walk(c, pre)

        walk(node, prefix)
        return list(dict.fromkeys(out))

    def _stub(self, prefix_rows: list[int], lead_rows: list[int], header_rows: list[int],
              closing_rows: list[int], indent: str) -> str:
        """Signature stub: wrapper prefix, the first comment line, the
        header through the body's `{`, `// ...`, the closing rows."""
        out = [self._lines[r] for r in prefix_rows]
        if lead_rows:
            out.append(self._lines[lead_rows[0]])
            if len(lead_rows) > 1 and self._lines[lead_rows[0]].lstrip().startswith("/*"):
                out[-1] = out[-1].rstrip() + " ... */"
        out += [self._lines[r] for r in header_rows]
        out.append(f"{indent}// ...")
        out += [self._lines[r] for r in closing_rows]
        return "\n".join(out)

    @staticmethod
    def _indent(line: str) -> str:
        return line[:len(line) - len(line.lstrip())]

    def _body_layout(self, outer: Node, fn: Node) -> tuple[list[int], list[Node], list[int]] | None:
        """(header rows, body statements, closing rows) of a function
        whose block body opens and closes on rows of its own; None when the
        body is not a block or shares rows with its statements."""
        body = fn.child_by_field_name("body")
        if body is None or body.type != "statement_block":
            return None
        stmts = body.named_children
        if not stmts or stmts[0].start_point[0] <= body.start_point[0] \
                or stmts[-1].end_point[0] >= body.end_point[0]:
            return None
        header = list(range(outer.start_point[0], body.start_point[0] + 1))
        closing = list(range(body.end_point[0], outer.end_point[0] + 1))
        return header, stmts, closing

    # ---------------------------------------------------------- definitions
    def _ts_definition_chunks(self, node: Node, lead: list[Node]) -> list[Chunk]:
        name, kind, defn = self._top_definition(node)
        qual = f"{self._module}.{name}"
        lead_rows = list(range(lead[0].start_point[0], node.start_point[0])) if lead else []
        if kind == "class":
            return self._ts_class_chunks(node, defn, qual, lead_rows)
        return self._ts_function_chunks(node, defn, qual, lead_rows, [], [], "function")

    def _ts_function_chunks(self, outer: Node, fn: Node, qual: str, lead_rows: list[int],
                            prefix_rows: list[int], suffix_rows: list[int], kind: str) -> list[Chunk]:
        """`outer` spans the whole definition (the statement, the method);
        `fn` is the function node whose body may be split."""
        own_rows = list(range(outer.start_point[0], outer.end_point[0] + 1))
        whole = self._chunk(qual, kind, prefix_rows, lead_rows + own_rows, rows_suffix=suffix_rows)
        whole.inner_symbols = self._inner_symbols(fn, qual)
        layout = self._body_layout(outer, fn)
        if layout is not None:
            header, _, closing = layout
            whole.signature = self._stub(prefix_rows, lead_rows, header, closing + suffix_rows,
                                         self._indent(self._lines[header[-1]]) + "  ")
        if whole.token_count <= self.max_tokens:
            return [whole]
        if layout is None:
            whole.oversized = True
            return [whole]
        header, stmts, closing = layout
        frags = self._pack(self._units(stmts), prefix_rows + lead_rows + header, closing + suffix_rows)
        if len(frags) <= 1:
            whole.oversized = True
            return [whole]
        n = len(frags)
        parts = []
        for i, (rows, over) in enumerate(frags):
            pre = prefix_rows + (lead_rows if i == 0 else []) + header
            part = self._chunk(qual, "function_part", pre, rows, i + 1, n, over, rows_suffix=closing + suffix_rows)
            parts.append(part)
        if not all(self.parses(p.content) for p in parts) and self.parses(whole.content):
            whole.oversized = True
            whole.notes.append("unsplittable: parts do not parse on their own")
            return [whole]
        for p in parts:
            p.inner_symbols = whole.inner_symbols if p.part == 1 else []
        return parts

    def _ts_class_chunks(self, outer: Node, defn: Node, qual: str, lead_rows: list[int]) -> list[Chunk]:
        body = defn.child_by_field_name("body")
        members = body.named_children if body is not None else []
        whole_rows = lead_rows + list(range(outer.start_point[0], outer.end_point[0] + 1))
        # a class whose members share its header's or closing row cannot be wrapped: one chunk
        if body is None or not members or members[0].start_point[0] <= body.start_point[0] \
                or members[-1].end_point[0] >= body.end_point[0]:
            whole = self._chunk(qual, "class_preamble", [], whole_rows)
            whole.oversized = whole.token_count > self.max_tokens
            return [whole]
        header = list(range(outer.start_point[0], body.start_point[0] + 1))
        closing = list(range(body.end_point[0], outer.end_point[0] + 1))

        def method_of(m: Node) -> Node | None:
            if m.type == "method_definition":
                return m
            if m.type == "public_field_definition":
                return _ts_function_value(m.child_by_field_name("value"))
            return None

        # a comment directly above a method belongs to the method, not the preamble
        method_leads: dict[int, list[Node]] = {}
        pending: list[Node] = []
        for m in members:
            if method_of(m) is None:
                pending.append(m)
            else:
                method_leads[m.id] = self._leading_comments(pending, m)
                pending = []
        lead_ids = {c.id for lead in method_leads.values() for c in lead}
        preamble = [m for m in members if method_of(m) is None and m.id not in lead_ids]
        chunks: list[Chunk] = []
        if any(m.type != "comment" for m in preamble):
            frags = self._pack(self._units(preamble), lead_rows + header, closing)
            n = len(frags)
            for i, (rows, over) in enumerate(frags):
                c = self._chunk(qual, "class_preamble", lead_rows + header, rows, i + 1, n, over, rows_suffix=closing)
                c.signature = self._stub(lead_rows + header[:-1], [], header[-1:], closing,
                                         self._indent(self._lines[header[-1]]) + "  ") if i == 0 else ""
                chunks.append(c)
        for m in members:
            if method_of(m) is None:
                continue
            m_lead = method_leads[m.id]
            m_lead_rows = list(range(m_lead[0].start_point[0], m.start_point[0])) if m_lead else []
            name = _ts_text(m.child_by_field_name("name")) or "?"
            chunks += self._ts_function_chunks(m, method_of(m), f"{qual}.{name}", m_lead_rows, header, closing,
                                               "method")
        return chunks


    # -------------------------------------------------------- module level
    def _module_blocks(self, nodes: list[Node], prefix_rows: list[int] = (), suffix_rows: list[int] = ()) \
            -> list[Chunk]:
        """Module-level runs, as in the Python splitter, except that a
        statement over the cap that carries a callback with a block body
        (`describe("x", function () {...})`, `router.get(path, async () =>
        {...})`) is split inside that callback: its header rows (through the
        callback's `{`) and closing rows wrap each part, recursively for
        nested callbacks. Chunks stay `module_block`s of the module."""
        prefix_rows, suffix_rows = list(prefix_rows), list(suffix_rows)
        chunks: list[Chunk] = []
        run: list[Node] = []

        def flush() -> None:
            if run:
                frags = self._pack(self._units(run), prefix_rows, suffix_rows)
                n = len(frags)
                chunks.extend(self._chunk(self._module, "module_block", prefix_rows, rows, i + 1, n, over,
                                          rows_suffix=suffix_rows) for i, (rows, over) in enumerate(frags))
                run.clear()

        for node in nodes:
            rows = list(range(node.start_point[0], node.end_point[0] + 1))
            too_big = self.tok.count(self._rows_text(prefix_rows + rows + suffix_rows)) > self.max_tokens
            layout = self._callback_layout(node) if too_big else None
            if layout is None:
                run.append(node)
                continue
            flush()
            header, stmts, closing = layout
            chunks += self._module_blocks(stmts, prefix_rows + header, closing + suffix_rows)
        flush()
        return chunks

    def _callback_layout(self, stmt: Node) -> tuple[list[int], list[Node], list[int]] | None:
        """(header rows, body statements, closing rows) of the outermost
        callback in `stmt` whose block body sits on rows of its own."""
        queue = list(stmt.named_children)
        while queue:
            n = queue.pop(0)
            if n.type in TS_FUNCTION_VALUES:
                layout = self._body_layout(stmt, n)
                if layout is not None:
                    return layout
            queue.extend(n.named_children)
        return None


class CodeSplitter:
    """Routes each file to its language's splitter by extension: `.py` to
    `PythonSplitter`, `.ts/.tsx/.js/.jsx` to `TypeScriptSplitter`. Other
    files yield no chunks."""

    def __init__(self, tokenizer, max_tokens: int = 800) -> None:
        self.python = PythonSplitter(tokenizer, max_tokens)
        self.typescript = TypeScriptSplitter(tokenizer, max_tokens)

    def for_path(self, path: str) -> PythonSplitter | None:
        lang = language_of(path)
        return self.python if lang == "python" else self.typescript if lang == "typescript" else None

    def split_file(self, path: str, repo_root: str) -> list[Chunk]:
        splitter = self.for_path(path)
        return splitter.split_file(path, repo_root) if splitter is not None else []

    def split_source(self, source: bytes, rel_path: str) -> list[Chunk]:
        splitter = self.for_path(rel_path)
        return splitter.split_source(source, rel_path) if splitter is not None else []


_SKIP_DIRS = ("node_modules", "__pycache__")


def iter_source_files(repo_root: str, language: str = "python") -> list[str]:
    """Every file of `language` under `repo_root` (sorted; hidden
    directories, `node_modules` and `__pycache__` skipped). TypeScript
    corpora index `.ts/.tsx/.js/.jsx` (`.d.ts` included); Python corpora
    index `.py` only, so a Python corpus's stray JavaScript (docs assets)
    never enters its index."""
    exts = LANGUAGE_EXTENSIONS[language]
    out: list[str] = []
    for d, dirs, files in os.walk(repo_root):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x not in _SKIP_DIRS)
        out.extend(os.path.join(d, f) for f in sorted(files) if f.endswith(exts))
    return out


def iter_python_files(repo_root: str) -> list[str]:
    return iter_source_files(repo_root, "python")


def corpus_language(repo_path: str, config: dict | None = None) -> str:
    """The corpus language an arm indexes: `config["language"]`, else the
    configured language of `config["repo_id"]` (config.CORPUS_LANGUAGE),
    else the language with more files under `repo_path`."""
    from harness import config as C

    config = config or {}
    if config.get("language"):
        return config["language"]
    if config.get("repo_id") in C.CORPUS_LANGUAGE:
        return C.CORPUS_LANGUAGE[config["repo_id"]]
    n_py = len(iter_source_files(repo_path, "python"))
    n_ts = len(iter_source_files(repo_path, "typescript"))
    return "typescript" if n_ts > n_py else "python"
