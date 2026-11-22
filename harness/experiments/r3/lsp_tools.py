"""`find_references`: a language server's reference search, for the LSP-enabled baseline (Phase 3, the user's decision
2026-11-22: "Baseline should be like Copilot/Claude, using LSP, not just grep").

    lsp = LspReferences(work)
    text = lsp.find_references("werkzeug.datastructures.csp.ContentSecurityPolicy.to_header")

It answers as an editor's "Find All References" does: jedi (the engine behind jedi-language-server, and the default
Python backend of multilspy) resolves the symbol, then `Script.get_references` returns every place in the project
whose name *infers* to that definition: through imports, aliases, annotated parameters and attributes assigned from
a constructor. Uses of the same name that belong to other objects are not references. It reads the working tree as
it is now (edits included), as a language server reads the editor's buffers.

**The symbol** is a dotted name (`pkg.module.Class.method`, or a shorter tail of it such as `Class.method`), or
`path:line` / `path:line:column` of a use or of the definition (the LSP request itself, a position). A name that
matches several definitions returns them (up to 20), to be asked again more precisely, as an editor's symbol search
lists them.

**The answer:** the resolved definition, then one line per reference, `path:line:column: <the source line>`, sorted
by path and line, the definition marked; at most `MAX_REFS` lines (then how many more). Nothing is summarised:
it is what the language server returns, rendered as text.
"""
from __future__ import annotations

import os
import re

MAX_REFS = 200
MAX_CANDIDATES = 20
_KEYWORDS = {"def", "class", "async", "await", "return", "import", "from", "self", "cls", "if", "for", "in",
             "with", "as", "not", "and", "or", "lambda", "yield", "None", "True", "False"}
_POS = re.compile(r"^(?P<path>[^:]+\.pyi?):(?P<line>\d+)(?::(?P<col>\d+))?$")


class LspError(Exception):
    pass


class LspReferences:
    def __init__(self, work: str):
        import jedi
        self.work = os.path.abspath(work)
        extra = [os.path.join(self.work, d) for d in ("src", "lib") if os.path.isdir(os.path.join(self.work, d))]
        self._jedi = jedi
        self.project = jedi.Project(self.work, added_sys_path=extra, smart_sys_path=True, load_unsafe_extensions=False)

    def _rel(self, path) -> str:
        p = str(path)
        return os.path.relpath(p, self.work) if p.startswith(self.work) else p

    def _script(self, path: str):
        full = path if os.path.isabs(path) else os.path.join(self.work, path)
        if not os.path.isfile(full):
            raise LspError(f"no such file: {path}")
        with open(full, encoding="utf8", errors="replace") as fh:
            code = fh.read()
        return self._jedi.Script(code, path=full, project=self.project)

    # ------------------------------------------------------------------ resolving the symbol
    def _at_position(self, path: str, line: int, col: int | None):
        script = self._script(path)
        full = path if os.path.isabs(path) else os.path.join(self.work, path)
        text = open(full, encoding="utf8", errors="replace").read().splitlines()
        if not 1 <= line <= len(text):
            raise LspError(f"{path} has {len(text)} lines")
        if col is None:     # no column: a defined name first, then the line's attribute names (`x.name`), then any
            while text[line - 1].lstrip().startswith("@") and line < len(text):
                line += 1                                  # a decorator: the definition it decorates
            src = text[line - 1]
            names = [m for m in re.finditer(r"[A-Za-z_]\w*", src) if m.group(0) not in _KEYWORDS]
            order = [m for m in names if re.search(rf"\b(def|class)\s+{m.group(0)}\b", src)]
            order += [m for m in names if m.start() and src[m.start() - 1] == "."]
            order += names
            for m in order:
                hits = script.goto(line, m.start())
                if hits and hits[0].type in ("function", "class", "property", "statement"):
                    col = m.start()
                    break
            if col is None:
                raise LspError(f"nothing to resolve on {path}:{line}; give `path:line:column`")
        return script, line, col

    def _definitions(self, symbol: str) -> list:
        leaf = symbol.rsplit(".", 1)[-1]
        out = []
        for n in self.project.search(leaf, all_scopes=True):
            if n.type not in ("function", "class", "statement", "property", "instance", "param") or n.name != leaf:
                continue
            full = n.full_name or ""
            mod = self._rel(n.module_path) if n.module_path else ""
            dotted = mod[:-3].replace("/", ".") + full[len(n.module_name or ""):] if mod.endswith(".py") else full
            if any(x == symbol or x.endswith("." + symbol) for x in (full, dotted)) or symbol == leaf:
                if n.type == "param":
                    continue
                out.append(n)
        seen, uniq = set(), []
        for n in out:
            k = (str(n.module_path), n.line, n.column)
            if k not in seen:
                seen.add(k)
                uniq.append(n)
        return uniq

    # ------------------------------------------------------------------ the request
    def find_references(self, symbol: str) -> str:
        symbol = symbol.strip().strip("`")
        m = _POS.match(symbol)
        if m:
            script, line, col = self._at_position(m["path"], int(m["line"]), int(m["col"]) - 1 if m["col"] else None)
            targets = script.goto(line, col, follow_imports=True)
            if not targets:
                raise LspError(f"nothing resolves at {symbol}")
            d = targets[0]
        else:
            defs = self._definitions(symbol)
            if not defs:
                raise LspError(f"no definition named `{symbol}` in the project")
            if len(defs) > 1:
                rows = [f"- {self._rel(d.module_path)}:{d.line}: {d.type} `{d.full_name}`"
                        for d in sorted(defs, key=lambda d: (str(d.module_path), d.line))[:MAX_CANDIDATES]]
                more = f"\n... ({len(defs) - MAX_CANDIDATES} more)" if len(defs) > MAX_CANDIDATES else ""
                return (f"`{symbol}` matches {len(defs)} definitions; ask again with a longer dotted name or "
                        f"`path:line` of the one you mean:\n" + "\n".join(rows) + more)
            d = defs[0]
        if d.module_path is None:
            raise LspError(f"`{symbol}` resolves outside the project ({d.full_name})")
        script = self._script(str(d.module_path))
        refs = script.get_references(d.line, d.column, include_builtins=False, scope="project")
        rows, seen = [], set()
        for r in refs:
            if r.module_path is None or not str(r.module_path).startswith(self.work):
                continue
            k = (self._rel(r.module_path), r.line, r.column)
            if k in seen:
                continue
            seen.add(k)
            tag = " (definition)" if (r.line, r.column) == (d.line, d.column) and r.module_path == d.module_path else ""
            rows.append((k, f"{k[0]}:{k[1]}:{k[2] + 1}{tag}: {(r.get_line_code() or '').strip()}"))
        rows.sort()
        head = (f"`{d.full_name}` ({d.type}, {self._rel(d.module_path)}:{d.line}): {len(rows)} reference(s), the "
                f"definition included")
        body = [t for _k, t in rows[:MAX_REFS]]
        more = [f"... ({len(rows) - MAX_REFS} more)"] if len(rows) > MAX_REFS else []
        return "\n".join([head, *body, *more])
