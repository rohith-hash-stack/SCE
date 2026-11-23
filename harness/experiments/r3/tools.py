"""R3's index-backed tools over a WorkingIndex (amendment R3-5, Revisions 1-2).

    layer = ToolLayer(working_index, tokenizer_path)
    text = layer.call("prism_change_context", symbol="pkg.mod.fn")    # tool name first, then its arguments

Every call:
1. refreshes the WorkingIndex (the drift probe; a cold-build-equivalent rebuild only if the shown bytes moved);
2. runs the tool against the shadow tree's index, through PRISM's own code paths:
   - `prism_change_context`, `prism_blast_radius` and `prism_ask`: the MCP server's tool functions
     (`prism.mcp.server`), given a RepoContext built from the WorkingIndex's builder exactly as
     `GraphCache._index` builds one;
   - `prism_new_function_context`: `prism.impact.newcode` (no MCP tool exists);
   - `prism_digest`: `prism.summaries.digest.DigestBuilder`, digest v2 (no MCP tool exists);
   - `related_code` (the baseline's version): BM25 over the index's production functions, as code, R1's control;
3. turns the shadow's absolute paths into repo-relative ones;
4. prepends the R3-5 notice for every gated file the answer touches (its path, or a symbol defined in it, appears);
5. cuts the answer at the per-call cap (4,000 Qwen2.5 tokens), marking the cut; the notice is placed in front of
   the capped answer, outside the cap, so it never changes what is served.

Settings shared by both versions and fixed here (harness choices, not rules): budgets equal to the 4,000-token cap,
and tests excluded from the change context and blast radius, as in R1 and R2.
"""
from __future__ import annotations

import json
import os
import re

CAP_TOKENS = 4000
DIGEST_MAX = 10
RELATED_MAX = 40
#: Share of the cap the blast radius's list of dependents may take before it is grouped by file
#: (`prism.impact.dependents`); its code envelope follows.
BLAST_LIST_SHARE = 0.55
PRISM_TOOLS = ("prism_change_context", "prism_blast_radius", "prism_ask", "prism_read_code", "prism_new_function_context",
               "prism_digest", "prism_search", "prism_read", "prism_find_references")
#: `prism_search`'s hit lines (as `grep`'s); `prism_read`'s footer of definitions
SEARCH_MAX_LINES = 200
READ_MAX_DEFS = 12
BASELINE_TOOLS = ("related_code",)
LSP_TOOLS = ("find_references", "semantic_search", "ast_search", "run_shell")


class ToolError(Exception):
    """A tool call the agent made wrongly (unknown symbol, bad argument): its text goes back to the agent."""


class ToolLayer:
    def __init__(self, wi, tokenizer_path: str | None = None, shared=None):
        """`shared`: the run's SharedTools, which `prism_read` and `prism_search` read the working files through (the
        current bytes, the same path rules and caps as `read_file`/`grep`)."""
        self.wi = wi
        self.shared = shared
        self._ctx_builder = None
        self._key = None
        self._tok = None
        if tokenizer_path:
            from tokenizers import Tokenizer
            self._tok = Tokenizer.from_file(tokenizer_path)
        self._install()

    # ------------------------------------------------------------------ the index the tools read
    def _install(self) -> None:
        """A RepoContext for the shadow tree, built from the WorkingIndex's builder the way GraphCache._index does,
        placed in the MCP server's cache so its tool functions answer from it."""
        from prism.graph.hierarchy import compute_hierarchical_profile
        from prism.graph.metamodel import SemanticMetamodel
        from prism.mcp import server
        from prism.mcp.cache import GraphCache, RepoContext
        from prism.runtime.contract_cache import compute_or_load_contracts
        from prism.slicer.distance import DistanceConfig, DistanceEngine
        from prism.traversal._cache_keys import evict_derived_caches, snapshot_signature
        b, tags, root = self.wi.builder, self.wi.tag_matrix, self.wi.shadow
        evict_derived_caches()
        metamodel = SemanticMetamodel()
        contracts = compute_or_load_contracts(b, root)
        ctx = RepoContext(repo_root=root, builder=b, tag_matrix=tags, metamodel=metamodel,
                          distance_engine=DistanceEngine(metamodel, tags, DistanceConfig()), runtime_state={},
                          contracts=contracts, hierarchy=compute_hierarchical_profile(b, contracts, root),
                          snapshot_signature=snapshot_signature(b))
        self._key = GraphCache.canonical_path(root)
        server._cache._entries[self._key] = ctx
        server._nlq_indexes.clear()
        self._ctx_builder = b
        self._newfn = None
        self._digests = None
        self._nlq = None

    def close(self) -> None:
        """Remove this run's index from PRISM's server cache. Every run has its own shadow path, so without this the
        indexes of all finished runs in one process stay in memory (the dry run was killed at 4.2 GB after about
        30 runs; a Kaggle cell runs many tasks in one process)."""
        from prism.mcp import server
        from prism.traversal._cache_keys import evict_derived_caches
        from prism.parser import cache as parse_cache
        if self._key is not None:
            server._cache._entries.pop(self._key, None)
        server._nlq_indexes.clear()
        evict_derived_caches()
        prefix = self.wi.shadow.replace("\\", "/").rstrip("/") + "/"      # the parse cache is keyed "path:sha256"
        with parse_cache._lock:
            for k in [k for k in parse_cache._cache if k.startswith(prefix)]:
                del parse_cache._cache[k]
        self._ctx_builder = self._newfn = self._digests = self._nlq = None

    def _sync(self):
        from prism.mcp import server
        r = self.wi.refresh()
        if r.rebuilt or self.wi.builder is not self._ctx_builder:
            self._install()
        elif server._cache._entries.get(self._key) is None:   # another layer cleared the server's cache
            self._install()
        return r

    # ------------------------------------------------------------------ the tools
    def call(self, tool: str, **args) -> str:
        refresh = self._sync()
        self.last_refresh = refresh
        try:
            body = getattr(self, "_" + tool)(**args)
        except ToolError as exc:
            body = f"error: {exc}"
        body = body.replace(self.wi.shadow + os.sep, "").replace(self.wi.shadow, ".")
        # The cap applies to the tool's own answer; the R3-5 notice goes in front of the capped answer (a few
        # dozen tokens per gated file, the same in both versions), so a notice never changes what is served.
        return self._notice_for(body) + self._cap(body)

    def _mcp(self, fn, **kw):
        from mcp.server.mcpserver.exceptions import ToolError as McpToolError
        from mcp.shared.exceptions import MCPError
        try:
            return fn(repo_path=self.wi.shadow, **kw)
        except (MCPError, McpToolError) as exc:
            raise ToolError(getattr(exc, "message", None) or str(exc)) from exc

    def _prism_change_context(self, symbol: str) -> str:
        from prism.mcp import server
        out = self._mcp(server.prism_change_context, symbol=symbol, budget_tokens=CAP_TOKENS, include_tests=False)
        return out["context"]

    def _prism_blast_radius(self, symbol: str) -> str:
        from prism.impact.context import MAX_REFERENCES, render_reference
        from prism.impact.dependents import Row, grouped
        from prism.mcp import server
        from prism.slicer.tokenizer import count_tokens
        out = self._mcp(server.prism_blast_radius, seed_symbol=symbol, budget_tokens=CAP_TOKENS, include_tests=False)
        callers = [f"- hop {c['hop']}: `{c['symbol']}` ({c['file']}:{c['line']})" for c in out["callers"]]
        refs = [render_reference(r) for r in out.get("references", [])]
        total = out["callers_total"] + out.get("references_total", 0)
        listed = len(out["callers"]) + len(out.get("references", []))
        full = callers + (["", f"Other uses of `{out['seed']}` (not calls; {out.get('references_total', 0)}) - each place "
                           "breaks if it is renamed, moved or removed:"] + refs[:MAX_REFERENCES] if refs else [])
        if (count_tokens("\n".join(full)) <= int(CAP_TOKENS * BLAST_LIST_SHARE) and len(refs) <= MAX_REFERENCES
                and listed == total):
            head = [f"Blast radius of `{out['seed']}`: {out['callers_total']} transitive callers"
                    + (" (list cut)" if out["callers_truncated"] else "") + ", nearest first:"] + full
            return "\n".join(head) + "\n\n" + out["envelope"]
        seed = self.wi.builder.symbol_table.get(out["seed"])
        seed_file = os.path.relpath(seed.file, self.wi.shadow) if seed is not None else None
        rows = [Row(c["symbol"], c["file"], ["calls" if c["hop"] == 1 else f"hop {c['hop']}"], [line],
                    is_test=c["is_test"], rank=(c["hop"],)) for c, line in zip(out["callers"], callers)]
        rows += [Row(r["source"], r["file"], list(r["kinds"]), [line], is_test=r["is_test"], is_module=r["is_module"],
                     rank=(1,)) for r, line in zip(out.get("references", []), refs)]
        intro = (f"Blast radius of `{out['seed']}`: {out['callers_total']} transitive callers and "
                 f"{out.get('references_total', 0)} other uses (imports, type use, values, ...)"
                 + (f", {total - listed} past PRISM's list" if total > listed else "")
                 + " - {n} listed here, in {files} files: the nearest in full, the rest by file. Each place breaks if"
                 " it is renamed, moved or removed.")
        head = grouped(rows, seed_file, int(CAP_TOKENS * BLAST_LIST_SHARE), count_tokens, intro)
        return "\n".join(head) + "\n\n" + out["envelope"]

    def _prism_ask(self, question: str) -> str:
        from prism.mcp import server
        out = self._mcp(server.prism_ask, question=question, top_k=10)
        return json.dumps(out, indent=1, sort_keys=True, default=str)

    def _prism_read_code(self, symbol: str) -> str:
        """The exact code of one indexed definition, as its file holds it: every line (blank lines and indentation
        kept), each numbered as `read_file` numbers it (the number, a tab, the text), so the text can be copied into
        `edit_file`'s `old` as is.

        `symbol` is what `prism_ask` / `prism_change_context` return: a qualified name (`hits[].qname`; a partial
        name that exactly one symbol ends with is resolved as `prism_change_context` resolves it), a location
        (`hits[].location`, `path:line`: the innermost definition containing that line), or a file path (the whole
        file). The bytes are the shadow's (what PRISM may show, R3-5): for a file the agent has not left unparsable,
        the same bytes as the working file."""
        from prism.mcp import server
        b, root = self.wi.builder, self.wi.shadow
        target = symbol.strip().strip("`")
        path, _, line = target.rpartition(":")
        rel = target
        if path and line.isdigit():
            rel = path
        rel = rel.replace("\\", "/").removeprefix("./")
        abs_path = os.path.join(root, rel)
        if os.path.isfile(abs_path) and os.path.realpath(abs_path).startswith(os.path.realpath(root) + os.sep):
            lines = self._lines(abs_path)
            if rel != target:                                      # path:line -> the innermost definition there
                n = int(line)
                if not 1 <= n <= len(lines):
                    raise ToolError(f"{rel} has {len(lines)} lines; line {n} is outside it")
                inside = [(s.line_range[1] - s.line_range[0], q) for q in b._def_nodes
                          if (s := b.symbol_table.get(q)) is not None and os.path.abspath(s.file) == abs_path
                          and s.line_range[0] <= n <= s.line_range[1]]
                if inside:
                    return self._code_block(min(inside)[1])
                return self._numbered(rel, lines, n, n, f"line {n} (no indexed definition contains it)")
            return self._numbered(rel, lines, 1, len(lines), "whole file")
        ctx = server._cache._entries.get(self._key)
        try:
            qname = server._resolve_seed_or_raise(ctx, target)
        except Exception as exc:                                   # MCPError: unknown or ambiguous name
            data = getattr(exc, "data", None) or {}
            cands = data.get("candidates") if isinstance(data, dict) else None
            msg = getattr(exc, "message", None) or str(exc)
            raise ToolError(msg.replace(root + os.sep, "").replace(root, ".")
                            + (f"; candidates: {', '.join(cands)}" if cands else "")) from exc
        return self._code_block(qname)

    # ------------------------------------------------------------------ Phase 3: references from PRISM's graph
    _REF_LABELS = {"call": "call", "instantiate": "instantiation", "possible": "possible call",
                   "import": "import", "export": "__all__", "base": "subclass", "isinstance": "isinstance",
                   "except": "except", "annotation": "type annotation", "generic": "type parameter",
                   "decorator": "decorator", "value": "value", "attribute": "attribute", "override": "override",
                   "callref": "used in a call",
                   "definition": "definition"}

    def _line_kinds(self, kinds: list[str], rel: str, line: int) -> list[str]:
        """An edge's kinds are per source, not per line: the kinds this line's own text shows (an import statement:
        `import`; an `__all__` line: `export`), else the edge's other kinds."""
        lines = self._lines(os.path.join(self.wi.shadow, rel))
        text = lines[line - 1].strip() if 0 < line <= len(lines) else ""
        stmt = text
        for i in range(line - 1, max(0, line - 40), -1):         # the statement a continuation line belongs to
            prev = lines[i - 1].strip()
            if prev.startswith(("from ", "import ", "__all__")):
                stmt = prev
                break
            if not prev.endswith((",", "(", "[")) and not text.startswith((")", "]")):
                break
        if stmt.startswith(("from ", "import ")) and "import" in kinds:
            return ["import"]
        if stmt.startswith("__all__") and "export" in kinds:
            return ["export"]
        other = [k for k in kinds if k not in ("import", "export")]
        return other or kinds

    def _ref_label(self, kind: str) -> str:
        """A kind's label; several kinds of one use (`import/export`) each by its own label."""
        return "/".join(self._REF_LABELS.get(k, k) for k in kind.split("/"))

    def _find_target(self, symbol: str) -> str:
        """A qualified name (as `prism_read_code` resolves it), or `path:line`: the definition starting on that line,
        else the one thing PRISM resolves a use on that line to (a recorded call site or a reference line)."""
        from prism.mcp import server
        b, root = self.wi.builder, self.wi.shadow
        target = symbol.strip().strip("`")
        path, _, line = target.rpartition(":")
        if path and line.isdigit():
            rel = path.replace("\\", "/").removeprefix("./")
            abs_path = os.path.abspath(os.path.join(root, rel))
            if not os.path.isfile(abs_path):
                raise ToolError(f"no such file: {rel}")
            n = int(line)
            defs = [q for q in b._def_nodes if (s := b.symbol_table.get(q)) is not None
                    and os.path.abspath(s.file) == abs_path and s.line_range[0] <= n <= s.line_range[1]]
            starts = [q for q in defs if self._def_line(q) == n]
            if starts:
                return min(starts, key=lambda q: len(q))
            found = set()
            for src in defs or []:
                for _s, dst, e in b.graph.out_edges(src, data=True):
                    if any(site[0] == n for site in e.get("sites", ())):
                        found.add(dst)
            rg = getattr(b, "reference_graph", None)
            if rg is not None:
                for src in defs:
                    if src in rg:
                        for _s, dst, e in rg.out_edges(src, data=True):
                            if n in e.get("lines", ()):
                                found.add(dst)
            found = {q for q in found if q in b.symbol_table}
            if len(found) == 1:
                return found.pop()
            if found:
                raise ToolError(f"{rel}:{n} uses several definitions: " + ", ".join(sorted(found)[:10])
                                + "; ask with one of these names")
            raise ToolError(f"PRISM resolves nothing on {rel}:{n}; ask with the definition's name")
        ctx = server._cache._entries.get(self._key)
        try:
            return server._resolve_seed_or_raise(ctx, target)
        except Exception as exc:
            data = getattr(exc, "data", None) or {}
            cands = data.get("candidates") if isinstance(data, dict) else None
            msg = getattr(exc, "message", None) or str(exc)
            raise ToolError(msg.replace(root + os.sep, "").replace(root, ".")
                            + (f"; candidates: {', '.join(cands)}" if cands else "")) from exc

    def _def_line(self, q: str) -> int:
        """The line of a definition's `def`/`class` keyword (after its decorators)."""
        s = self.wi.builder.symbol_table.get(q)
        lines = self._lines(s.file)
        leaf = q.rsplit(".", 1)[-1]
        for i in range(s.line_range[0], min(s.line_range[1], len(lines)) + 1):
            if re.match(rf"\s*(async\s+)?(def|class)\s+{re.escape(leaf)}\b", lines[i - 1]):
                return i
        return s.line_range[0]

    def _prism_find_references(self, symbol: str) -> str:
        """Every place PRISM's graph resolves to one definition, as a language server's Find All References lists
        them, each labelled with how it uses the definition and the definition it sits in:
        - calls and instantiations: the main graph's call edges, at each call's own position (`sites`);
        - other uses (imports, `__all__`, type annotations, subclasses, isinstance, decorators, values...): the
          reference graph's labelled edges, at their lines;
        - overrides in either direction.
        Grouped by file, production first; then how many uses of the same name belong to other objects."""
        d = self.reference_places(symbol)
        q, sym, leaf, rows, possible, truncated = (d["target"], d["sym"], d["leaf"], d["rows"], d["possible"],
                                                   d["truncated"])
        root, own = self.wi.shadow, d["file"]
        by_file: dict[str, list] = {}
        for (f, line, col), (label, where) in rows.items():
            by_file.setdefault(f, []).append((line, col, label, where))
        files = sorted(by_file, key=lambda f: (self._is_test(f), f))
        n = sum(len(v) for v in by_file.values()) - 1
        counts: dict[str, int] = {}
        for (label, _w) in rows.values():
            if label != "definition":
                k = self._ref_label(label.split(" ")[0])
                counts[k] = counts.get(k, 0) + 1
        tests = sum(len(by_file[f]) for f in files if self._is_test(f))
        out = [f"`{q}` ({sym.kind}, {own}:{self._def_line(q)}): {n} reference(s) in "
               f"{len([f for f in files if any(x[2] != 'definition' for x in by_file[f])])} file(s) ("
               + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
               + f"); {tests} in test files" + (f"; {len(possible)} possible (below)" if possible else "")]
        shown = 0
        for f in files:
            out.append(f + (" [test]" if self._is_test(f) else ""))
            lines = self._lines(os.path.join(root, f))
            for line, col, label, where in sorted(by_file[f]):
                if shown >= SEARCH_MAX_LINES:
                    break
                text = lines[line - 1].strip() if 0 < line <= len(lines) else ""
                lab = self._ref_label(label.split(" ")[0]) + label[len(label.split(" ")[0]):]
                out.append(f"  {line}:{col + 1} [{lab}] in `{where}`: {text}")
                shown += 1
        if shown < n + 1:
            out.append(f"... ({n + 1 - shown} more)")
        if truncated:
            out.append(f"(and {truncated} more use(s) inside the definitions above; their lines were not kept)")
        if possible:
            out.append(f"Possible references ({len(possible)}): calls of `{leaf}` on a receiver whose type PRISM could "
                       f"not determine, matched by name; check each:")
            for (f, line, col), (_label, where) in sorted(possible.items()):
                lines = self._lines(os.path.join(root, f))
                text = lines[line - 1].strip() if 0 < line <= len(lines) else ""
                out.append(f"  {f}:{line}:{col + 1} in `{where}`: {text}")
        listed = {(f, line) for (f, line, _c) in [*rows, *possible]}
        other = self._other_uses(leaf, listed)
        if other:
            out.append(f"Not references: {other[0]} other use(s) of the name `{leaf}` in {other[1]} file(s) belong to "
                       f"other objects (not listed).")
        return "\n".join(out)

    def reference_places(self, symbol: str) -> dict:
        """`prism_find_references`'s places as data (also what the PRISM gateway, `prism.gateway`, tracks):
        `target` (the qualified name), `leaf`, `file` / `def_line` (the definition), `rows` ({(rel, line, byte col):
        (label, enclosing)}, the definition included) and `possible` (the same, for calls matched only by name)."""
        from prism.graph.references import references_to
        b, root = self.wi.builder, self.wi.shadow
        q = self._find_target(symbol)
        sym = b.symbol_table.get(q)
        leaf = q.rsplit(".", 1)[-1]
        rows = {}                                                  # (rel, line, col) -> (label, enclosing)

        def rel_of(path):
            return os.path.relpath(path, root) if path and os.path.isabs(path) else path

        def add(rel, line, col, label, where):
            if rel is None or line is None:
                return
            if col is None:
                lines = self._lines(os.path.join(root, rel))
                text = lines[line - 1] if 0 < line <= len(lines) else ""
                m = re.search(rf"\b{re.escape(leaf)}\b", text)
                col = len(text[:m.start()].encode()) if m else 0
            key = (rel, line, col)
            if key not in rows:
                rows[key] = (label, where)

        own = rel_of(sym.file)
        add(own, self._def_line(q), None, "definition", q.rsplit(".", 1)[0])
        truncated = 0
        for src, _d, e in b.graph.in_edges(q, data=True):
            rel_ = e.get("relation")
            if rel_ not in ("CALLS", "INSTANTIATES", "OVERRIDES"):
                continue
            ss = b.symbol_table.get(src)
            if ss is None:
                continue
            f = rel_of(ss.file)
            if rel_ == "OVERRIDES":
                add(f, self._def_line(src), None, "override", src)
                continue
            guessed = e.get("kind") == "TENTATIVE_CALL" or e.get("via") == "name_match"
            label = "instantiate" if rel_ == "INSTANTIATES" else "possible" if guessed else "call"
            sites = e.get("sites")
            if not sites:                                          # an index without call sites: the caller only
                add(f, ss.line_range[0], None, label + " (somewhere in this function)", src)
            for line, col in sites or ():
                add(f, line, col, label, src)
        for _s, dst, e in b.graph.out_edges(q, data=True):
            if e.get("relation") == "OVERRIDES" and (ds := b.symbol_table.get(dst)) is not None:
                add(rel_of(ds.file), self._def_line(dst), None, "override", dst + " (overridden by this one)")
        for r in references_to(b, q):
            f = rel_of(r["file"])
            for line in r["lines"]:
                kinds = ["callref" if k == "call" else k for k in r["kinds"]]   # the reference pass's "call": a
                if len(kinds) > 1:                                    # name inside a call expression
                    kinds = self._line_kinds(kinds, f, line)
                add(f, line, None, "/".join(kinds), r["source"] if not r["is_module"] else "<module>")
            truncated += max(0, r["count"] - len(r["lines"]))
        possible = {k: v for k, v in rows.items() if v[0].startswith("possible")}
        rows = {k: v for k, v in rows.items() if k not in possible}
        return {"target": q, "sym": sym, "leaf": leaf, "file": own, "def_line": self._def_line(q), "rows": rows,
                "possible": possible, "truncated": truncated}

    def _other_uses(self, leaf: str, listed: set) -> tuple[int, int] | None:
        """How many lines of Python files use the word `leaf` without being a listed reference, and in how many files."""
        import subprocess
        base = self.shared.work if self.shared is not None else self.wi.shadow
        r = subprocess.run(["git", "-C", base, "grep", "-n", "-w", "-I", "--untracked", "-e", leaf, "--",
                            "*.py"], capture_output=True, text=True, errors="replace")
        n, files = 0, set()
        for ln in r.stdout.splitlines():
            f, _, rest = ln.partition(":")
            num = rest.split(":", 1)[0]
            if num.isdigit() and (f, int(num)) not in listed:
                n += 1
                files.add(f)
        return (n, len(files)) if n else None

    # ------------------------------------------------------------------ Phase D: search and read (current text)
    def _is_test(self, rel: str) -> bool:
        from prism.scanner.entrypoint_gate import is_test_file
        return is_test_file(rel.replace(os.sep, "/"), self.wi.workdir)

    def _index_enclosing(self, rel: str, line: int) -> str | None:
        """The innermost indexed definition at `rel:line` (PRISM's index; for files `ast` cannot read)."""
        b, path = self.wi.builder, os.path.join(self.wi.shadow, rel)
        inside = [(s.line_range[1] - s.line_range[0], q) for q in b._def_nodes
                  if (s := b.symbol_table.get(q)) is not None and os.path.abspath(s.file) == path
                  and s.line_range[0] <= line <= s.line_range[1]]
        return min(inside)[1] if inside else None

    def _prism_search(self, pattern: str, path: str = ".", kind: str | None = None) -> str:
        """`grep` over the working files, each hit labelled: what the matching code is (import, definition, call,
        type annotation, decorator, base class, isinstance, except, string, comment, code), the definition it is in,
        and the file's kind (test, docs, config, template). `kind` keeps only hits of that kind (or file kind)."""
        import subprocess
        from harness.experiments.r3.search_labels import PythonFile, file_kind, other_kind
        if self.shared is None:
            raise ToolError("prism_search needs the working files")
        work = self.shared.work
        p = self.shared._path(path)
        rel = os.path.relpath(p, work)
        r = subprocess.run(["git", "-C", work, "grep", "-n", "--column", "-I", "-E", "--untracked", "-e", pattern, "--",
                            rel if rel != "." else ":/", ":!node_modules"], capture_output=True, text=True,
                           errors="replace")
        if r.returncode not in (0, 1):
            raise ToolError(f"bad pattern: {r.stderr.strip()[:200]}")
        parsed: dict[str, PythonFile | None] = {}
        hits = []
        for ln in r.stdout.splitlines():
            m = re.match(r"(.+?):(\d+):(\d+):(.*)$", ln)
            if not m or "/.r3-" in ln or ln.startswith(".r3-"):
                continue
            f, line, col, text = m.group(1), int(m.group(2)), int(m.group(3)) - 1, m.group(4)
            fk = file_kind(f, self._is_test)
            if f.endswith(".py"):
                if f not in parsed:
                    try:
                        parsed[f] = PythonFile(f, open(os.path.join(work, f), "rb").read().decode("utf8", "replace"))
                    except OSError:
                        parsed[f] = None
                pf = parsed[f]
                if pf is not None and pf.ok:
                    what, where = pf.kind(line, col), pf.enclosing(line)
                else:
                    what, where = "code", self._index_enclosing(f, line)
            elif fk in ("docs", "config", "template"):
                what, where = None, None
            else:
                what, where = other_kind(text, col), self._index_enclosing(f, line)
            hits.append((f, line, fk, what, where, text))
        if kind:
            hits = [h for h in hits if kind in (h[2], h[3])]
        if not hits:
            return f"no matches for {pattern!r}" + (f" of kind {kind!r}" if kind else "")
        counts: dict[str, int] = {}
        for h in hits:
            for k in (h[3], h[2]):
                if k:
                    counts[k] = counts.get(k, 0) + 1
        files = len({h[0] for h in hits})
        head = (f"prism_search {pattern!r}: {len(hits)} hits in {files} files ("
                + ", ".join(f"{k} {n}" for k, n in sorted(counts.items(), key=lambda x: -x[1])) + ")")
        out = [head]
        for f, line, fk, what, where, text in hits[:SEARCH_MAX_LINES]:
            label = "; ".join(x for x in (fk, what, f"in `{where}`" if where else None) if x)
            out.append(f"{f}:{line}: [{label}] {text.strip()[:240]}")
        if len(hits) > SEARCH_MAX_LINES:
            out.append(f"... ({len(hits) - SEARCH_MAX_LINES} more hits; narrow `path` or `kind`)")
        return "\n".join(out)

    def _prism_read(self, path: str, start: int = 1, end: int | None = None) -> str:
        """`read_file` exactly (any file: code, tests, templates, configs; the working file's current bytes, numbered
        lines, the same cut rule), then one line from PRISM: the file's kind and the definitions in the lines shown."""
        from harness.experiments.r3.search_labels import PythonFile, file_kind
        if self.shared is None:
            raise ToolError("prism_read needs the working files")
        from harness.experiments.r3.shared_tools import ToolCallError
        try:
            body = self.shared._read_file(path, start, end)
        except ToolCallError as exc:
            raise ToolError(str(exc)) from exc
        m = re.match(r"(\S+) \((?:lines (\d+)-(\d+) of \d+|lines (\d+)-(\d+) of \d+ shown)", body)
        if not m:
            return body
        rel = os.path.relpath(self.shared._path(path), self.shared.work).replace(os.sep, "/")
        a, b = (int(m.group(2)), int(m.group(3))) if m.group(2) else (int(m.group(4)), int(m.group(5)))
        defs: list[tuple[str, int, int]] = []
        if rel.endswith(".py"):
            pf = PythonFile(rel, open(self.shared._path(path), "rb").read().decode("utf8", "replace"))
            defs = pf.definitions_in(a, b) if pf.ok else []
        if not defs:
            bb, full = self.wi.builder, os.path.join(self.wi.shadow, rel)
            defs = sorted(((q, s.line_range[0], s.line_range[1]) for q in bb._def_nodes
                           if (s := bb.symbol_table.get(q)) is not None and os.path.abspath(s.file) == full
                           and s.kind in ("function", "method", "class")
                           and s.line_range[0] <= b and s.line_range[1] >= a), key=lambda d: (d[1], d[0]))
        fk = file_kind(rel, self._is_test)
        parts = [f"{fk} file" if fk else None]
        if defs:
            listed = ", ".join(f"`{q}` ({lo}-{hi})" for q, lo, hi in defs[:READ_MAX_DEFS])
            parts.append("definitions in these lines: " + listed
                         + (f" +{len(defs) - READ_MAX_DEFS} more" if len(defs) > READ_MAX_DEFS else ""))
        note = "; ".join(x for x in parts if x)
        return body + (f"\n--- PRISM: {note}" if note else "")

    def _code_block(self, qname: str) -> str:
        s = self.wi.builder.symbol_table.get(qname)
        lines = self._lines(s.file)
        lo, hi = s.line_range
        rel = os.path.relpath(s.file, self.wi.shadow).replace(os.sep, "/")
        what = f"`{qname}`, {s.kind}"
        if rel.endswith(".py"):
            # Phase 3 (the delete-05 fix): a definition nested in an `if`/`else`, `try`, ... block is shown with that
            # block, and where it sits in it, so a change sees the branch it would leave behind
            from prism.impact.enclosing import MAX_BLOCK_LINES, describe, enclosing_block
            block = enclosing_block("\n".join(lines) + "\n", lo)
            if block is not None:
                note = describe(block, qname.rsplit(".", 1)[-1])
                if block["hi"] - block["lo"] + 1 <= MAX_BLOCK_LINES:
                    return self._numbered(rel, lines, block["lo"], min(block["hi"], len(lines)),
                                          f"{what} at lines {lo}-{hi}, with its enclosing block. {note}")
                what += f". {note}"
        return self._numbered(rel, lines, lo, min(hi, len(lines)), what)

    @staticmethod
    def _lines(path: str) -> list[str]:
        with open(path, "rb") as fh:
            return fh.read().decode("utf8", "replace").splitlines()

    @staticmethod
    def _numbered(rel: str, lines: list[str], lo: int, hi: int, what: str) -> str:
        body = "\n".join(f"{i}\t{lines[i - 1]}" for i in range(lo, hi + 1))
        return f"{rel} (lines {lo}-{hi} of {len(lines)}: {what})\n{body}"

    def _prism_new_function_context(self, file: str, name: str, signature: str = "", purpose: str = "",
                                    cls: str | None = None) -> str:
        from prism.impact.newcode import NewFunctionContextBuilder, render
        if self._newfn is None:
            self._newfn = NewFunctionContextBuilder(self.wi.builder, self.wi.shadow)
        try:
            return render(self._newfn.build(file, name, signature=signature, purpose=purpose, cls=cls,
                                            budget_tokens=CAP_TOKENS))
        except (KeyError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    def _prism_digest(self, symbols: list[str]) -> str:
        from prism.summaries.digest import DigestBuilder
        if self._digests is None:
            self._digests = DigestBuilder(self.wi.builder)
        if len(symbols) > DIGEST_MAX:
            raise ToolError(f"at most {DIGEST_MAX} symbols per call")
        out = []
        for q in symbols:
            if q not in self.wi.builder.symbol_table:
                out.append(f"`{q}`: not found in the index")
                continue
            out.append(self._digests.digest(q).render())
        return "\n\n".join(out)

    def _baseline(self):
        from harness.experiments.r3.baseline_tools import BaselineTools
        if self.shared is None:
            raise ToolError("this tool needs the working tree")
        if getattr(self, "_bt", None) is None:
            self._bt = BaselineTools(self.shared.work)
        return self._bt

    def _bt_call(self, name: str, *args) -> str:
        from harness.experiments.r3.baseline_tools import BaselineToolError
        try:
            return getattr(self._baseline(), name)(*args)
        except BaselineToolError as exc:
            raise ToolError(str(exc)) from exc

    def _semantic_search(self, query: str) -> str:
        return self._bt_call("semantic_search", query)

    def _ast_search(self, pattern: str, path: str = ".") -> str:
        return self._bt_call("ast_search", pattern, path)

    def _run_shell(self, command: str) -> str:
        return self._bt_call("run_shell", command)

    def _find_references(self, symbol: str) -> str:
        """BASELINE_LSP's language-server reference search (`lsp_tools.py`), on the working files as they are now."""
        from harness.experiments.r3.lsp_tools import LspError, LspReferences
        if self.shared is None:
            raise ToolError("find_references needs the working tree")
        if getattr(self, "_lsp", None) is None:
            self._lsp = LspReferences(self.shared.work)
        try:
            return self._lsp.find_references(symbol)
        except LspError as exc:
            raise ToolError(str(exc)) from exc

    def _related_code(self, query: str) -> str:
        """R1's control in the loop: the functions a BM25 search over the repository's production functions ranks
        highest for `query`, as code (each: its name and location, then up to 60 lines)."""
        from harness.experiments.suite.bugs import _hit_text
        from prism.query.nlq import NLQIndex, tokenize
        b = self.wi.builder
        if self._nlq is None:
            self._nlq = NLQIndex(b, self.wi.shadow, summaries=None)
        prod = {q for q in b._def_nodes if (s := b.symbol_table.get(q)) is not None
                and s.kind in ("function", "method") and not b._in_test_file(s.file)}
        ranked = [self._nlq.names[i] for i in self._nlq.bm25.rank(tokenize(query))]
        hits = [q for q in ranked if q in prod][:RELATED_MAX]
        if not hits:
            return "No matching functions."
        return "\n\n".join(_hit_text(b, self.wi.shadow, q) for q in hits)

    # ------------------------------------------------------------------ notice and cap
    def _notice_for(self, body: str) -> str:
        touched = []
        b = self.wi.builder
        for rel in self.wi.gated_files():
            if rel in body:
                touched.append(rel)
                continue
            path = os.path.join(self.wi.shadow, rel)
            if any(f"`{q}`" in body or f'"{q}"' in body for q in b._def_nodes
                   if (s := b.symbol_table.get(q)) is not None and s.file == path):
                touched.append(rel)
        return self.wi.notice(touched)

    def _cap(self, text: str) -> str:
        if self._tok is None:
            return text
        ids = self._tok.encode(text).ids
        if len(ids) <= CAP_TOKENS:
            return text
        marker = f"\n... [cut at {CAP_TOKENS:,} tokens]"
        keep = CAP_TOKENS - len(self._tok.encode(marker).ids)
        return self._tok.decode(ids[:keep]) + marker
