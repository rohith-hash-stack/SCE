"""Labels for `prism_search` and `prism_read` (Phase 2, Phase D; `harness/experiments/r4/PHASE2_PLAN.md`): what a
matching line is, read from the file's current text, so a label is right after the agent's own edits.

- **Python** (`ast`): the innermost syntax node at the match decides the kind:
  `import`, `definition` (a `def`/`class` line's name), `decorator`, `type annotation`, `base class`, `isinstance`,
  `except`, `call`, `string`, `comment`, `code`. The enclosing definition is the innermost `def`/`class` around the
  line, qualified as PRISM qualifies it (the file's dotted path, `__init__` dropped).
- **JS/TS and other code:** the same kinds where a line's shape shows them (`import`/`require`, a `function`/`class`
  line, a `//` comment, a name followed by `(`); the enclosing definition comes from PRISM's index.
- **The file:** `test` (PRISM's own test-file rule), `docs`, `config`, `template`, or nothing for code.
"""
from __future__ import annotations

import ast
import os
import re

FILE_KINDS = (("docs", (".md", ".rst", ".txt", ".adoc")), ("config", (".toml", ".cfg", ".ini", ".yaml", ".yml",
                                                                     ".json", ".env")),
              ("template", (".html", ".htm", ".jinja", ".jinja2", ".j2", ".mako", ".tmpl", ".hbs", ".ejs")))
_JS_IMPORT = re.compile(r"^\s*(import\b|export\s+\{[^}]*\}\s+from\b|export\s+\*\s+from\b)|\brequire\s*\(")
_JS_DEF = re.compile(r"^\s*(export\s+)?(default\s+)?(async\s+)?(function\b|class\b|interface\b|type\s+\w+\s*=|enum\b)")


def file_kind(rel: str, is_test) -> str | None:
    if is_test(rel):
        return "test"
    ext = os.path.splitext(rel)[1].lower()
    return next((k for k, exts in FILE_KINDS if ext in exts), None)


def module_of(rel: str) -> str:
    mod = os.path.splitext(rel)[0].replace(os.sep, "/").replace("/", ".")
    return mod[:-9] if mod.endswith(".__init__") else mod


class PythonFile:
    """One Python file's current text, parsed once: the kind and enclosing definition of a position."""

    def __init__(self, rel: str, text: str):
        self.rel, self.lines = rel, text.splitlines()
        self.tree = None
        try:
            self.tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return
        self.parents = {}
        self.defs = []                                     # (first line, last line, dotted name)
        self.nodes = []

        def walk(node, chain):
            for c in ast.iter_child_nodes(node):
                self.parents[c] = node
                sub = chain
                if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    first = min([c.lineno] + [d.lineno for d in c.decorator_list])
                    sub = chain + [c.name]
                    self.defs.append((first, c.end_lineno or c.lineno, ".".join(sub)))
                if hasattr(c, "lineno") and hasattr(c, "end_lineno"):
                    self.nodes.append(c)
                walk(c, sub)
        walk(self.tree, [])

    @property
    def ok(self) -> bool:
        return self.tree is not None

    def enclosing(self, line: int) -> str | None:
        best = None
        for lo, hi, name in self.defs:
            if lo <= line <= hi and (best is None or hi - lo <= best[1] - best[0]):
                best = (lo, hi, name)
        return f"{module_of(self.rel)}.{best[2]}" if best else None

    def definitions_in(self, a: int, b: int) -> list[tuple[str, int, int]]:
        return [(f"{module_of(self.rel)}.{n}", lo, hi) for lo, hi, n in sorted(self.defs) if lo <= b and hi >= a]

    def _at(self, line: int, col: int):
        best, size = None, None
        for n in self.nodes:
            start, end = (n.lineno, n.col_offset), (n.end_lineno, n.end_col_offset)
            if start <= (line, col) < end or (start <= (line, col) and (line, col) == end):
                s = (n.end_lineno - n.lineno, n.end_col_offset - n.col_offset if n.end_lineno == n.lineno else 10 ** 6)
                if size is None or s < size:
                    best, size = n, s
        return best

    def kind(self, line: int, col: int) -> str:
        """The kind of use at 1-based `line`, 0-based `col`."""
        text = self.lines[line - 1] if 0 < line <= len(self.lines) else ""
        hash_at = _comment_start(text)
        if hash_at is not None and col >= hash_at:
            return "comment"
        for n in self.nodes:                                        # `@deco`: the `@` is outside every node
            for d in getattr(n, "decorator_list", ()):
                first_col = d.col_offset - 1 if d.lineno == line else 0
                if d.lineno <= line <= (d.end_lineno or d.lineno) and col >= first_col:
                    if line < (d.end_lineno or d.lineno) or col < (d.end_col_offset or 10 ** 6):
                        return "decorator"
        node = self._at(line, col)
        if node is None:
            return "code"
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and line == node.lineno:
            return "definition"
        if isinstance(node, (ast.Import, ast.ImportFrom)):           # the match is on the statement's keywords
            return "import"
        if isinstance(node, ast.ExceptHandler):
            return "except"
        c, p = node, self.parents.get(node)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return "string"
        while p is not None:
            if isinstance(p, (ast.Import, ast.ImportFrom)):
                return "import"
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if c in p.decorator_list:
                    return "decorator"
                if isinstance(p, ast.ClassDef) and (c in p.bases or c in p.keywords):
                    return "base class"
                if c is getattr(p, "returns", None):
                    return "type annotation"
                break
            if isinstance(p, ast.arg) and c is p.annotation:
                return "type annotation"
            if isinstance(p, ast.AnnAssign) and c is p.annotation:
                return "type annotation"
            if isinstance(p, ast.ExceptHandler) and c is p.type:
                return "except"
            if isinstance(p, ast.Call):
                f = p.func
                if isinstance(f, ast.Name) and f.id in ("isinstance", "issubclass") and c in p.args[1:]:
                    return "isinstance"
                if c is f:
                    return "call"
            if isinstance(p, (ast.stmt,)):
                break
            c, p = p, self.parents.get(p)
        return "code"


def _comment_start(text: str) -> int | None:
    """Where a `#` comment starts on a Python line (outside string quotes), or None."""
    quote = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return i
        i += 1
    return None


def other_kind(text: str, col: int) -> str:
    """The kind of a match in a non-Python code line, from the line's shape."""
    c = text.find("//")
    if c != -1 and c <= col:
        return "comment"
    if _JS_IMPORT.search(text):
        return "import"
    if _JS_DEF.search(text):
        return "definition"
    m = re.match(r"[\w$.]*\s*\(", text[col:])
    if m:
        return "call"
    return "code"
