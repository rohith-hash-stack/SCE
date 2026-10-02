"""Tree-sitter Python splitter: a source file -> compilable chunks of at most
`max_tokens` tokens (800 for Arm 1). Shared by Arm 1 (RAG) and Arm 2
(Priompt, M2).

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

    @property
    def source_id(self) -> str:
        suffix = f"#part{self.part}of{self.n_parts}" if self.n_parts > 1 else ""
        return f"{self.file}:{self.start_line}-{self.end_line}{suffix}"


def module_name(rel_path: str) -> str:
    mod = rel_path[:-3] if rel_path.endswith(".py") else rel_path
    parts = mod.replace(os.sep, "/").split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(p for p in parts if p)


def _compiles(text: str) -> bool:
    try:
        compile(text, "<chunk>", "exec", dont_inherit=True)
        return True
    except (SyntaxError, ValueError):
        return False


class PythonSplitter:
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

    def _chunk(self, qual, kind, rows_prefix, rows_body, part=1, n_parts=1, oversized=False, notes=None) -> Chunk:
        content = self._rows_text(rows_prefix + rows_body)
        return Chunk(
            qualified_name=qual, kind=kind, file=self._rel,
            start_line=rows_body[0] + 1, end_line=rows_body[-1] + 1, content=content,
            token_count=self.tok.count(content), prefix_lines=len(rows_prefix), part=part, n_parts=n_parts,
            oversized=oversized, notes=list(notes or []), source_rows=sorted(set(rows_prefix + rows_body)),
        )

    def _units(self, nodes: list[Node]) -> list[list[int]]:
        """Group statement nodes into atomic row ranges: each compound
        statement alone; consecutive simple statements (and comments)
        together; nodes sharing a row never separated."""
        units: list[list[int]] = []
        current: list[int] = []
        for n in nodes:
            rows = list(range(n.start_point[0], n.end_point[0] + 1))
            is_compound = (n.type in COMPOUND or n.type in DEF_TYPES)
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

    def _pack(self, units: list[list[int]], prefix_rows: list[int]) -> list[tuple[list[int], bool]]:
        """Greedy in-order packing of units into fragments under the cap.
        Returns (rows, oversized) per fragment."""
        frags: list[tuple[list[int], bool]] = []
        cur: list[int] = []
        for u in units:
            cand = cur + (list(range(cur[-1] + 1, u[0])) if cur else []) + u
            if cur and self.tok.count(self._rows_text(prefix_rows + cand)) > self.max_tokens:
                frags.append((cur, False))
                cur = list(u)
            else:
                cur = cand
        if cur:
            frags.append((cur, False))
        return [(rows, self.tok.count(self._rows_text(prefix_rows + rows)) > self.max_tokens) for rows, _ in frags]

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


def iter_python_files(repo_root: str) -> list[str]:
    out: list[str] = []
    for d, dirs, files in os.walk(repo_root):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x not in ("node_modules", "__pycache__"))
        out.extend(os.path.join(d, f) for f in sorted(files) if f.endswith(".py"))
    return out
