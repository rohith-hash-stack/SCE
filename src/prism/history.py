"""PRISM's historical references (AMENDMENTS "POC 2 amendment 1"): an immutable, task-scoped snapshot of the start
state's index, so that a definition the agent deleted can still be asked about.

    snap = HistoricalGraph(builder, root)      # the start index and its tree, right after the first build
    snap.resolve("litestar/params.py:556")     # -> "litestar.params.Dependency" (the start state's definitions)
    snap.status(rows, leaf, module_file, work) # each historical place's state in the current files

**Why:** the harness rebuilds the index from the current tree before every PRISM call (`WorkingIndex._build`), so a
deleted definition leaves the current graph with everything that pointed to it. The start index is a separate
object that later builds replace and never change, so keeping it gives the start state's graph without building it
twice.

**Immutable:**
- The snapshot keeps the builder object and the exact bytes it parsed (`builder._parsed_files`), read-only.
- `fingerprint` is a sha256 over the graph's nodes and edges (the main graph and the reference graph) and the files'
  hashes, taken at capture. `verify()` recomputes it before every read; a mismatch raises `SnapshotChanged`, so a
  changed snapshot never answers.

**Three kinds of fact, kept apart** (`status`):
- **historical:** what the snapshot says, i.e. the places that used the definition at the start;
- **current:** where each place's line is now (a line diff of the start text against the current file, followed
  through the edits) and whether it still holds the name: `gone` or `still_references`;
- **confirmed broken:** a place that still holds the name, where the current code shows it cannot work:
  - `file_does_not_parse` (`ast`);
  - `imports_a_removed_name`: an import of the name from the module that defined it, while that module no longer
    defines it at its top level;
  - `undefined_name` (pyflakes, on that line).

A historical place is never assumed broken: `still_references` means only that the name is still there.

Nothing here reads a task's answer key, hidden tests, places or scoring data: the snapshot is the start-state index
the PRISM arms already build.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import os
import re
import time
from types import MappingProxyType

STATUSES = ("gone", "still_references", "confirmed_broken")


class SnapshotChanged(Exception):
    pass


class HistoryError(Exception):
    pass


def _digest_graph(h, g) -> None:
    for n in sorted(map(str, g.nodes)):
        h.update(b"n\0" + n.encode() + b"\0")
    for u, v, d in sorted((str(u), str(v), str(d.get("relation", d.get("kind", "")))) for u, v, d in g.edges(data=True)):
        h.update(b"e\0" + u.encode() + b"\0" + v.encode() + b"\0" + d.encode() + b"\0")


def line_map(a: list[str], b: list[str]) -> dict[int, int | None]:
    """Each 1-based line of `a` to its line in `b` (None: deleted); a replaced block maps line by line."""
    out: dict[int, int | None] = {}
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        for k in range(i2 - i1):
            if op == "equal":
                out[i1 + k + 1] = j1 + k + 1
            elif op == "replace":
                out[i1 + k + 1] = j1 + min(k, j2 - j1 - 1) + 1
            else:
                out[i1 + k + 1] = None
    return out


def _top_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                for x in ast.walk(t):
                    if isinstance(x, ast.Name):
                        names.add(x.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, (ast.If, ast.Try)):           # conditional definitions count as definitions
            names |= _top_level_names(ast.Module(body=list(ast.iter_child_nodes(node)), type_ignores=[]))
    return names


def _module_candidates(module: str) -> set[str]:
    base = module.replace(".", "/")
    return {f"{p}{base}{s}" for p in ("", "src/", "lib/") for s in (".py", "/__init__.py")}


class HistoricalGraph:
    def __init__(self, builder, root: str):
        """`builder`: the start state's `ConcreteGraphBuilder`, which the caller never rebuilds in place. `root`: the
        tree it was built from (its symbols' paths are under it)."""
        t0 = time.time()
        self.builder, self.root = builder, os.path.abspath(root)
        texts = {}
        for path, parsed in getattr(builder, "_parsed_files", {}).items():
            src = getattr(parsed, "source", None)
            if isinstance(src, (bytes, bytearray)):
                texts[os.path.relpath(path, self.root) if os.path.isabs(path) else path] = bytes(src).decode(
                    "utf8", "replace")
        self._texts = MappingProxyType(texts)
        t1 = time.time()
        self.fingerprint = self._fingerprint()
        self.stats = {"capture_s": round(t1 - t0, 3), "fingerprint_s": round(time.time() - t1, 3),
                      "files": len(texts), "bytes": sum(len(t) for t in texts.values()),
                      "nodes": builder.graph.number_of_nodes(), "edges": builder.graph.number_of_edges(),
                      "reads": 0, "verify_s": 0.0}
        self.queried: dict[str, dict] = {}           # the targets historical answers were given about

    # ------------------------------------------------------------------ immutability
    def _fingerprint(self) -> str:
        h = hashlib.sha256()
        _digest_graph(h, self.builder.graph)
        rg = getattr(self.builder, "reference_graph", None)
        if rg is not None:
            h.update(b"R\0")
            _digest_graph(h, rg)
        for rel in sorted(self._texts):
            h.update(b"f\0" + rel.encode() + b"\0" + hashlib.sha256(self._texts[rel].encode()).digest())
        return h.hexdigest()

    def verify(self) -> None:
        t0 = time.time()
        now = self._fingerprint()
        self.stats["verify_s"] = round(self.stats["verify_s"] + time.time() - t0, 3)
        if now != self.fingerprint:
            raise SnapshotChanged("PRISM's historical snapshot changed after it was captured")

    # ------------------------------------------------------------------ reading
    def text(self, rel: str) -> str | None:
        return self._texts.get(rel)

    def lines_at(self, path: str) -> list[str]:
        """A file's lines at the start (an absolute path under `root`, or a relative one)."""
        rel = os.path.relpath(path, self.root) if os.path.isabs(path) else path
        return (self._texts.get(rel) or "").splitlines()

    def resolve(self, symbol: str) -> str:
        """The start state's definition a symbol names: `path:line` (the definition whose `def`/`class` is on that
        line), a qualified name, or a unique dotted tail of one."""
        b = self.builder
        target = symbol.strip().strip("`")
        path, _, line = target.rpartition(":")
        defs = [q for q in getattr(b, "_def_nodes", ()) if b.symbol_table.get(q) is not None]
        if path and line.isdigit():
            rel, n = path.replace("\\", "/").removeprefix("./"), int(line)
            here = [q for q in defs if self._rel(b.symbol_table.get(q).file) == rel]
            starts = [q for q in here if self.def_line(q) == n]
            if starts:
                return min(starts, key=len)
            raise HistoryError(f"no definition started at {rel}:{n} at the start of the task either")
        if target in b.symbol_table:
            return target
        tail = [q for q in defs if q.endswith("." + target)]
        if len(tail) == 1:
            return tail[0]
        if tail:
            raise HistoryError(f"`{target}` named several definitions at the start: " + ", ".join(sorted(tail)[:10]))
        raise HistoryError(f"`{target}` was not a definition at the start of the task either")

    def _rel(self, path: str) -> str:
        return os.path.relpath(path, self.root) if path and os.path.isabs(path) else path

    def def_line(self, q: str) -> int:
        s = self.builder.symbol_table.get(q)
        lines = self.lines_at(s.file)
        leaf = q.rsplit(".", 1)[-1]
        for i in range(s.line_range[0], min(s.line_range[1], len(lines)) + 1):
            if re.match(rf"\s*(async\s+)?(def|class)\s+{re.escape(leaf)}\b", lines[i - 1]):
                return i
        return s.line_range[0]

    def lookalikes(self, leaf: str, listed: set) -> tuple[int, int] | None:
        """Lines of the start state's Python files that use the word `leaf` without being a listed place."""
        pat = re.compile(rf"\b{re.escape(leaf)}\b")
        n, files = 0, set()
        for rel, text in self._texts.items():
            if not rel.endswith(".py"):
                continue
            for i, ln in enumerate(text.splitlines(), 1):
                if pat.search(ln) and (rel, i) not in listed:
                    n += 1
                    files.add(rel)
        return (n, len(files)) if n else None

    # ------------------------------------------------------------------ the current state of historical places
    def status(self, places, leaf: str, module_file: str, work: str) -> dict[tuple[str, int], dict]:
        """Each historical place `(rel, line)` -> {"status", "now" (its current line or None), "why"} against the
        files in `work` now (see the module docstring)."""
        pat = re.compile(rf"\b{re.escape(leaf)}\b")
        module_gone = None
        out: dict[tuple[str, int], dict] = {}
        cache: dict[str, tuple] = {}
        for rel, line in sorted(set(places)):
            if rel not in cache:
                p = os.path.join(work, rel)
                cur = open(p, encoding="utf8", errors="replace").read() if os.path.isfile(p) else None
                m = line_map(self.lines_at(rel), cur.splitlines()) if cur is not None else {}
                tree = parse_err = None
                if cur is not None and rel.endswith(".py"):
                    try:
                        tree = ast.parse(cur)
                    except SyntaxError as exc:
                        parse_err = f"line {exc.lineno}: {exc.msg}"
                cache[rel] = (cur, m, tree, parse_err, None)
            cur, m, tree, parse_err, undefined = cache[rel]
            now = m.get(line) if cur is not None else None
            cur_lines = cur.splitlines() if cur is not None else []
            if now is None or not (0 < now <= len(cur_lines)) or not pat.search(cur_lines[now - 1]):
                out[(rel, line)] = {"status": "gone", "now": now if cur is not None else None,
                                    "why": "file deleted" if cur is None else "the name is no longer on this line"}
                continue
            why = None
            if parse_err is not None:
                why = f"file_does_not_parse ({parse_err})"
            elif tree is not None:
                if module_gone is None:
                    module_gone = self._module_lost(module_file, leaf, work)
                if module_gone and self._imports_from(tree, now, leaf, module_file):
                    why = f"imports_a_removed_name (`{module_file}` no longer defines `{leaf}`)"
                else:
                    if undefined is None:
                        undefined = _undefined_lines(tree, rel, leaf)
                        cache[rel] = (cur, m, tree, parse_err, undefined)
                    if now in undefined:
                        why = f"undefined_name (`{leaf}` is not defined in this file)"
            out[(rel, line)] = ({"status": "confirmed_broken", "now": now, "why": why} if why else
                                {"status": "still_references", "now": now, "why": "the name is still on this line"})
        return out

    @staticmethod
    def _module_lost(module_file: str, leaf: str, work: str) -> bool:
        p = os.path.join(work, module_file)
        if not os.path.isfile(p):
            return True
        try:
            return leaf not in _top_level_names(ast.parse(open(p, encoding="utf8", errors="replace").read()))
        except SyntaxError:
            return False                          # cannot tell: not confirmed

    @staticmethod
    def _imports_from(tree: ast.Module, line: int, leaf: str, module_file: str) -> bool:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.lineno <= line <= (node.end_lineno or
                                                                                              node.lineno):
                if any(a.name == leaf for a in node.names) and module_file in _module_candidates(node.module):
                    return True
        return False


def _undefined_lines(tree: ast.Module, rel: str, leaf: str) -> set[int]:
    try:
        from pyflakes import checker
    except ImportError:                           # pyflakes absent: no such evidence
        return set()
    return {m.lineno for m in checker.Checker(tree, rel).messages
            if type(m).__name__ in ("UndefinedName", "UndefinedLocal") and m.message_args
            and m.message_args[0] == leaf}
