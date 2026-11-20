"""Graph-coverage benchmark (Phase 2, Phase A; `harness/experiments/r4/PHASE2_PLAN.md`): how many of the real uses of a
repository's definitions PRISM's graph connects, by the kind of use.

    python -B -m harness.experiments.graph_coverage.benchmark audit --repo CLONE --rev SHA --label NAME --out FILE.json
    python -B -m harness.experiments.graph_coverage.benchmark summarize FILE.json ... [--markdown OUT.md]
    python -B -m harness.experiments.graph_coverage.benchmark check --baseline DIR --new DIR   # exit 1 on a drop
    python -B -m harness.experiments.graph_coverage.benchmark shown --repo CLONE --rev SHA --label NAME --out F.json
    python -B -m harness.experiments.graph_coverage.benchmark shown-summary F.json ... [--markdown OUT.md]

**What is measured** (Python files; JS/TS is the next step of A1):
- **The definitions:** every function, method and class PRISM indexes whose simple name is unique among them
  (same-named definitions cannot be matched to a use without type information, so they are left out; dunder names
  too).
- **The uses:** every place a file refers to one of those names, found by parsing it (`ast`), each with:
  - **kind:** what the use does (a call, an import, a type annotation, `isinstance`, a decorator, a base class, passed
    or stored or returned as a value, an attribute read on it, `__all__`, a `getattr`-style string, ...);
  - **location:** inside a function, in a class body, at module level, or in a test file;
  - **form:** `bare` (`foo`) or `qualified` (`mod.foo`, `obj.foo`). A bare name is matched exactly; a qualified one
    by its last part, so it may name a different object with the same attribute name (lower precision; reported
    apart).
  - **A bare name bound locally is not a use** (since the type-tracking step): a parameter, an assigned name, a loop
    or comprehension variable in an enclosing function, or a module-level assignment in another file than the
    definition's. Before, `client` (a parameter) counted as a use of an unrelated `client` fixture. These are
    counted apart (`local_names_excluded`); reports before this change include them, so compare a stage with the
    one before it under the same method.
- **Recorded:** the graph has an edge from the use's source node to the definition, any relation (the main graph,
  or the reference graph Phase B adds). The source node is
  the innermost indexed definition containing the use; for a use outside every definition, the module's own node
  (dotted module name), which PRISM does not build today. PRISM connects definitions, not lines, so a use counts as
  recorded when its definition has any edge to the target (`run` calling `f` also covers `getattr(self, "f")` in
  `run`): the question is whether PRISM knows that this definition depends on that one.
- **Surfaced:** that edge is one `prism_change_context` and `prism_blast_radius` list: a main-graph relation PRISM's
  caller views traverse (`prism.graph.concrete_builder.TRAVERSABLE_RELATIONS`), or, since B10, a reference-graph edge
  (the tools list a symbol's other uses through `prism.graph.references.references_to`). Read from PRISM, so the
  benchmark follows what PRISM can show; it ignores the tools' list lengths and token caps.
- **Shown** (`shown`, sampled): what the agent actually gets. For a sample of uses outside test files, both tools are
  called on the used definition through the harness's own tool layer (`harness.experiments.r3.tools.ToolLayer`:
  tests left out, each answer cut at 4,000 tokens), and a use counts as shown when its source's name appears in an
  answer; split by where (the callers' part of the answer, or the other-uses list B10 adds).
- **Non-Python files:** for each unique name of 6+ characters, the non-Python tracked files that mention it as a word,
  by type (docs, C/C++, templates, config, JS/TS, other). PRISM indexes none of these today; counted, not matched.

The numbers are a reading of how code is connected, not a list of required edits: a use is real, but a task may need
none of them changed.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import collections
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

TEST_PATH = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$")
GETATTR_FUNCS = {"getattr", "hasattr", "setattr", "delattr"}
NON_PY_TYPES = (("docs", (".md", ".rst", ".txt", ".adoc")), ("c/c++", (".c", ".h", ".cc", ".cpp", ".pyx", ".pxd")),
                ("templates", (".html", ".jinja", ".jinja2", ".j2", ".mako", ".tmpl")),
                ("config", (".toml", ".cfg", ".ini", ".yaml", ".yml", ".json")),
                ("js/ts", (".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs")))
KINDS = ("call", "instantiation", "base class", "decorator", "type annotation", "generic/subscript",
         "isinstance/issubclass", "except clause", "import", "__all__ entry", "getattr-style string",
         "passed as an argument", "assigned as a value", "stored in a container", "returned as a value",
         "attribute read on it", "compared", "other use")


def _git(cwd: str, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[:300]}")
    return r.stdout


def is_test(rel: str) -> bool:
    return bool(TEST_PATH.search(rel))


# ---------------------------------------------------------------------------------------------- the index
class Index:
    """The definitions PRISM indexed in `root`, and its graph."""

    def __init__(self, builder, root: str):
        from prism.graph import references as _refs
        from prism.graph.concrete_builder import TRAVERSABLE_RELATIONS
        self.graph, self.root, self.surfaced = builder.graph, root, set(TRAVERSABLE_RELATIONS)
        if hasattr(_refs, "references_to"):          # B10: the tools list reference-graph edges
            self.surfaced.add("REFERENCES")
        # Phase B: references other than calls and inheritance, in a graph of their own (PHASE2_B_DESIGN.md)
        self.refs = getattr(builder, "reference_graph", None)
        self.defs: dict[str, tuple[str, int, int, str]] = {}
        self.local_names = 0          # bare names bound locally that share a definition's name: not counted
        names = collections.defaultdict(list)
        for q in builder._def_nodes:
            s = builder.symbol_table.get(q)
            if s is None or s.kind not in ("function", "method", "class") or not s.file.endswith(".py"):
                continue
            rel = os.path.relpath(s.file, root).replace(os.sep, "/")
            self.defs[q] = (rel, s.line_range[0], s.line_range[1], s.kind)
            names[q.rsplit(".", 1)[-1]].append(q)
        self.unique = {n: qs[0] for n, qs in names.items()
                       if len(qs) == 1 and not (n.startswith("__") and n.endswith("__"))}
        self._by_file = collections.defaultdict(list)
        for q, (rel, lo, hi, _k) in self.defs.items():
            self._by_file[rel].append((lo, hi, q))

    def source(self, rel: str, line: int) -> str:
        """The innermost indexed definition containing the line, else the file's module node name."""
        inside = [(hi - lo, q) for lo, hi, q in self._by_file.get(rel, []) if lo <= line <= hi]
        if inside:
            return min(inside)[1]
        mod = rel[:-3].replace("/", ".")
        return mod[:-9] if mod.endswith(".__init__") else mod

    def edge(self, src: str, dst: str) -> str | None:
        """The main graph's relation from `src` to `dst`, else 'REFERENCES' if the reference graph connects them."""
        if src == dst:
            return None
        if self.graph.has_edge(src, dst):
            return self.graph.edges[src, dst].get("relation", "CALLS")
        if self.refs is not None and self.refs.has_edge(src, dst):
            return "REFERENCES"
        return None


# ---------------------------------------------------------------------------------------------- classifying uses
def _parents(tree):
    out = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            out[c] = p
    return out


_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_COMPS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _stores(node, out: set, top: bool = True) -> None:
    """Names a scope binds other than by import or def/class: assignment, loop, `with`, `except` targets (Store
    names), not inside nested functions, classes or comprehensions."""
    for c in ast.iter_child_nodes(node):
        if isinstance(c, _FUNCS + (ast.ClassDef,) + _COMPS):
            continue
        if isinstance(c, ast.Name) and isinstance(c.ctx, ast.Store):
            out.add(c.id)
        elif isinstance(c, ast.ExceptHandler) and c.name:
            out.add(c.name)
        _stores(c, out, False)


def _scope_locals(fn) -> set:
    """A function's or lambda's own names: parameters and the names it binds (minus `global`/`nonlocal` ones)."""
    a = fn.args
    names = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs}
    names |= {x.arg for x in (a.vararg, a.kwarg) if x is not None}
    if not isinstance(fn, ast.Lambda):
        body = ast.Module(body=list(fn.body), type_ignores=[])
        _stores(body, names)
        for g in ast.walk(fn):
            if isinstance(g, (ast.Global, ast.Nonlocal)):
                names -= set(g.names)
    return names


class _Shadow:
    """Whether a bare name at a node is a local binding (a parameter, an assigned name, a loop or comprehension
    variable), not the indexed definition that shares its name: such a "use" is not one. Module-level assignments
    count too, unless the definition is in the same file (a conditional definition and its sibling binding)."""

    def __init__(self, tree, parents):
        self.parents, self._cache = parents, {}
        self.module_stores: set = set()
        for stmt in tree.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
                _stores(stmt, self.module_stores)

    def locals_of(self, fn) -> set:
        if fn not in self._cache:
            self._cache[fn] = _scope_locals(fn)
        return self._cache[fn]

    def __call__(self, node, name: str, def_in_this_file: bool) -> bool:
        c, p = node, self.parents.get(node)
        while p is not None:
            if isinstance(p, _FUNCS):
                in_header = c is getattr(p, "returns", None) or c in getattr(p, "decorator_list", []) \
                    or c is getattr(p, "args", None)
                if not in_header and name in self.locals_of(p):
                    return True
            elif isinstance(p, _COMPS):
                bound: set = set()
                for g in p.generators:
                    _stores(ast.Module(body=[ast.Expr(g.target)], type_ignores=[]), bound)
                    if isinstance(g.target, ast.Name):
                        bound.add(g.target.id)
                if name in bound:
                    return True
            c, p = p, self.parents.get(p)
        return name in self.module_stores and not def_in_this_file


def _location(node, parents, rel) -> str:
    if is_test(rel):
        return "test file"
    c, p = node, parents.get(node)
    while p is not None:
        if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            header = (c in getattr(p, "decorator_list", []) or c in getattr(p, "bases", [])
                      or c in getattr(p, "keywords", []) or c is getattr(p, "args", None)
                      or c is getattr(p, "returns", None))
            if not header:            # a definition's header (decorators, bases, signature) is in the outer scope
                break
        c, p = p, parents.get(p)
    if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return "function body"
    return "class body" if isinstance(p, ast.ClassDef) else "module level"


def _in_annotation(node, parents) -> bool:
    c, p = node, parents.get(node)
    while p is not None and not isinstance(p, ast.stmt):
        if isinstance(p, ast.arg) and p.annotation is c:
            return True
        c, p = p, parents.get(p)
    if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)) and p.returns is c:
        return True
    return isinstance(p, ast.AnnAssign) and p.annotation is c


def _kind(node, parents, target_kind: str) -> str:
    """What the use `node` (the Name, or the Attribute ending in the name) does."""
    p = parents.get(node)
    if isinstance(p, ast.Call) and p.func is node:
        deco_of = parents.get(p)
        if isinstance(deco_of, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and p in deco_of.decorator_list:
            return "decorator"
        return "instantiation" if target_kind == "class" else "call"
    if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node in p.decorator_list:
        return "decorator"
    if isinstance(p, ast.ClassDef) and node in p.bases:
        return "base class"
    if _in_annotation(node, parents):
        return "type annotation"
    q = parents.get(p) if isinstance(p, ast.Tuple) else p
    if isinstance(q, ast.Call) and isinstance(q.func, ast.Name) and q.func.id in ("isinstance", "issubclass"):
        return "isinstance/issubclass"
    if isinstance(q, ast.ExceptHandler) or isinstance(p, ast.ExceptHandler):
        return "except clause"
    if isinstance(p, ast.Subscript):
        return "generic/subscript"
    if isinstance(p, ast.Call):
        return "passed as an argument"
    if isinstance(p, ast.keyword):
        return "passed as an argument"
    if isinstance(p, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        return "assigned as a value"
    if isinstance(p, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
        return "stored in a container"
    if isinstance(p, (ast.Return, ast.Yield, ast.YieldFrom)):
        return "returned as a value"
    if isinstance(p, ast.Attribute):
        return "attribute read on it"
    if isinstance(p, ast.Compare):
        return "compared"
    return "other use"


def file_uses(rel: str, source: bytes, idx: Index) -> list[dict]:
    """Every use, in this file, of a unique indexed name."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    parents, out = _parents(tree), []
    shadow = _Shadow(tree, parents)
    local_names = [0]

    def add(node, name, kind, form):
        tgt = idx.unique[name]
        d = idx.defs[tgt]
        if d[0] == rel and d[1] == getattr(node, "lineno", -1):     # the definition's own line
            return
        if form == "bare" and isinstance(node, ast.Name) and shadow(node, name, d[0] == rel):
            local_names[0] += 1                                        # a local that shares the name: not a use
            return
        src = idx.source(rel, node.lineno)
        rel_ = idx.edge(src, tgt)
        out.append({"target": tgt, "source": src, "kind": kind, "location": _location(node, parents, rel), "form": form,
                    "recorded": rel_ is not None, "surfaced": rel_ in idx.surfaced if rel_ else False,
                    "where": f"{rel}:{node.lineno}"})

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in idx.unique:
            add(node, node.id, _kind(node, parents, idx.defs[idx.unique[node.id]][3]), "bare")
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and node.attr in idx.unique:
            add(node, node.attr, _kind(node, parents, idx.defs[idx.unique[node.attr]][3]), "qualified")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                name = a.name.rsplit(".", 1)[-1]
                if name in idx.unique:
                    add(node, name, "import", "bare")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in idx.unique:
            p = parents.get(node)
            pp = parents.get(p)
            if isinstance(p, (ast.List, ast.Tuple)) and isinstance(pp, (ast.Assign, ast.AugAssign)) and any(
                    isinstance(t, ast.Name) and t.id == "__all__" for t in getattr(pp, "targets", [getattr(pp, "target", None)])):
                add(node, node.value, "__all__ entry", "bare")
            elif isinstance(p, ast.Call) and isinstance(p.func, ast.Name) and p.func.id in GETATTR_FUNCS \
                    and len(p.args) > 1 and p.args[1] is node:
                add(node, node.value, "getattr-style string", "bare")
    idx.local_names += local_names[0]
    return out


def non_python_mentions(root: str, idx: Index, limit: int = 400) -> dict:
    names = sorted(n for n in idx.unique if len(n) >= 6)[:limit]
    by_type = collections.Counter()
    for n in names:
        for f in _git(root, "grep", "-l", "-w", "-I", n, "--", ":!*.py", check=False).split():
            ext = os.path.splitext(f)[1].lower()
            by_type[next((t for t, exts in NON_PY_TYPES if ext in exts), "other")] += 1
    return {"names_sampled": len(names), "files_by_type": dict(by_type)}


# ---------------------------------------------------------------------------------------------- the audit
def all_uses(root: str, builder, idx: "Index | None" = None) -> list[dict]:
    idx = idx or Index(builder, root)
    uses = []
    for rel in _git(root, "ls-files", "*.py").split():
        try:
            uses += file_uses(rel, open(os.path.join(root, rel), "rb").read(), idx)
        except (OSError, RecursionError):
            continue
    return uses


def audit_tree(root: str, builder) -> dict:
    idx = Index(builder, root)
    uses = all_uses(root, builder, idx)
    cells = collections.defaultdict(lambda: {"uses": 0, "recorded": 0, "surfaced": 0, "examples": []})
    for u in uses:
        c = cells[f"{u['kind']}|{u['location']}|{u['form']}"]
        c["uses"] += 1
        c["recorded"] += u["recorded"]
        c["surfaced"] += u["surfaced"]
        if len(c["examples"]) < 3:
            c["examples"].append(u["where"])
    return {"definitions": len(idx.defs), "unique_names": len(idx.unique), "uses": len(uses),
            "local_names_excluded": idx.local_names,
            "surfaced_relations": sorted(idx.surfaced), "cells": dict(cells),
            "non_python": non_python_mentions(root, idx)}


def audit(repo: str, rev: str, label: str) -> dict:
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
    from prism.cli import LANGUAGE_TIER_PERMISSIVE, build_pipeline
    scratch = tempfile.mkdtemp(prefix=f"gcov_{label}_")
    work = os.path.join(scratch, "work")
    _git(repo, "worktree", "add", "-q", "--detach", work, rev)
    try:
        import time
        t0 = time.time()
        builder, _tags = build_pipeline(work, LANGUAGE_TIER_PERMISSIVE, use_cache=False)
        build_seconds = round(time.time() - t0, 1)
        out = audit_tree(work, builder)
        ref = getattr(builder, "reference_graph", None)
        out["build"] = {"seconds": build_seconds, "graph_edges": builder.graph.number_of_edges(),
                        "reference_edges": ref.number_of_edges() if ref is not None else 0,
                        "module_nodes": sum(1 for _n, d in ref.nodes(data=True) if d.get("kind") == "module")
                        if ref is not None else 0}
    finally:
        _git(repo, "worktree", "remove", "--force", work, check=False)
        shutil.rmtree(scratch, ignore_errors=True)
    return {"label": label, "rev": rev, **out}


# ---------------------------------------------------------------------------------------------- shown (sampled)
OTHER_USES_MARK = "Other uses"          # where B10's list starts in both tools' answers


def dependents_part(answer: str) -> str:
    """The part of a tool's answer that lists who depends on the symbol: the change context's section 4, or the blast
    radius's list before its code envelope. A name elsewhere (a directory in section 1, a sibling) is not a listing."""
    if "## 4. Who depends on it" in answer:
        part = answer.split("## 4. Who depends on it", 1)[1]
        return part.split("\n## 5.", 1)[0]
    if "Blast radius of `" in answer:
        return answer.split("\n\n<", 1)[0]
    return ""


def miss_reason(u: dict, parts: list[str], answers: tuple, builder, root: str) -> str:
    """Why a surfaced use is not named in either answer."""
    rel = u["where"].rsplit(":", 1)[0]
    if builder._in_test_file(os.path.join(root, rel)):
        return "PRISM counts its file as test code (left out)"
    if any("By file (the rest" in p and (f"- {rel} (" in p or "more files (" in p) for p in parts):
        return "counted in a grouped listing, not named"
    if any("[cut at" in a for a in answers):
        return "answer cut at the token cap"
    return "not listed"


def shown(repo: str, rev: str, label: str, sample: int = 300, seed: int = 0, tokenizer: str | None = None) -> dict:
    """What the agent gets: `sample` uses outside test files, drawn at random; for each used definition both tools
    are called once through the harness's tool layer, and a use is shown when its source appears in an answer."""
    import random
    import time
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
    from harness.experiments.r3.runner import TOKENIZER
    from harness.experiments.r3.tools import ToolLayer
    from harness.experiments.r3.working_index import WorkingIndex
    scratch = tempfile.mkdtemp(prefix=f"gshow_{label}_")
    work = os.path.join(scratch, "work")
    _git(repo, "worktree", "add", "-q", "--detach", work, rev)
    wi = layer = None
    try:
        wi = WorkingIndex(work, os.path.join(scratch, "shadow"))
        tok = tokenizer or TOKENIZER
        layer = ToolLayer(wi, tok if os.path.exists(tok) else None)
        uses = [u for u in all_uses(wi.shadow, wi.builder) if u["location"] != "test file"]
        rng = random.Random(seed)
        picked = rng.sample(uses, min(sample, len(uses)))
        answers: dict[str, tuple[str, str]] = {}
        t0 = time.time()
        for u in picked:
            if u["target"] not in answers:
                cc = layer.call("prism_change_context", symbol=u["target"])
                br = layer.call("prism_blast_radius", symbol=u["target"])
                answers[u["target"]] = (cc, br)
        rows = collections.defaultdict(lambda: {"uses": 0, "recorded": 0, "surfaced": 0, "shown": 0,
                                                "shown_as_caller": 0, "shown_as_other_use": 0})
        missed = collections.Counter()
        missed_examples = collections.defaultdict(list)
        for u in picked:
            name = f"`{u['source']}`"
            parts = [dependents_part(a).split(OTHER_USES_MARK, 1) for a in answers[u["target"]]]
            as_caller = any(name in p[0] for p in parts)
            as_other = any(len(p) > 1 and name in p[1] for p in parts)
            if u["surfaced"] and not (as_caller or as_other):
                why = miss_reason(u, [dependents_part(a) for a in answers[u["target"]]], answers[u["target"]],
                                  wi.builder, wi.shadow)
                missed[why] += 1
                if len(missed_examples[why]) < 5:
                    missed_examples[why].append(f"{u['source']} -> {u['target']} ({u['kind']}, {u['where']})")
            for key in ("all", u["kind"]):
                r = rows[key]
                r["uses"] += 1
                r["recorded"] += u["recorded"]
                r["surfaced"] += u["surfaced"]
                r["shown"] += as_caller or as_other
                r["shown_as_caller"] += as_caller
                r["shown_as_other_use"] += as_other and not as_caller
                r["in_a_grouped_answer"] = r.get("in_a_grouped_answer", 0) + ((as_caller or as_other) and any(
                    "By file (the rest" in dependents_part(a) for a in answers[u["target"]]))
        return {"label": label, "rev": rev, "sample": len(picked), "targets": len(answers), "seed": seed,
                "tool_seconds": round(time.time() - t0, 1), "capped_by_tokenizer": layer._tok is not None,
                "rows": dict(rows), "surfaced_not_shown": dict(missed),
                "surfaced_not_shown_examples": dict(missed_examples)}
    finally:
        if layer is not None:
            layer.close()
        if wi is not None:
            wi.close()
        _git(repo, "worktree", "prune", check=False)
        _git(repo, "worktree", "remove", "--force", work, check=False)
        shutil.rmtree(scratch, ignore_errors=True)


def shown_markdown(reports: list[dict]) -> str:
    def pct(a, b):
        return f"{a / b:.0%}" if b else "-"
    lines = ["| Repository | Sampled uses | Recorded | Surfaced | Shown | as a caller | in the other-uses list |",
             "|---|---|---|---|---|---|---|"]
    tot = collections.Counter()
    for r in reports:
        a = r["rows"]["all"]
        tot.update(a)
        lines.append(f"| {r['label']} | {a['uses']} | {pct(a['recorded'], a['uses'])} | {pct(a['surfaced'], a['uses'])} "
                     f"| {pct(a['shown'], a['uses'])} | {pct(a['shown_as_caller'], a['uses'])} "
                     f"| {pct(a['shown_as_other_use'], a['uses'])} |")
    lines.append(f"| **all** | {tot['uses']} | {pct(tot['recorded'], tot['uses'])} | {pct(tot['surfaced'], tot['uses'])} "
                 f"| {pct(tot['shown'], tot['uses'])} | {pct(tot['shown_as_caller'], tot['uses'])} "
                 f"| {pct(tot['shown_as_other_use'], tot['uses'])} |")
    kinds = collections.defaultdict(collections.Counter)
    for r in reports:
        for k, v in r["rows"].items():
            if k != "all":
                kinds[k].update(v)
    lines += ["", "| Kind | Sampled uses | Recorded | Shown |", "|---|---|---|---|"]
    for k, v in sorted(kinds.items(), key=lambda x: -x[1]["uses"]):
        lines.append(f"| {k} | {v['uses']} | {pct(v['recorded'], v['uses'])} | {pct(v['shown'], v['uses'])} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------- summaries
def totals(reports: list[dict], by: str) -> dict:
    """{key: {uses, recorded, surfaced}} with key the kind, the location or the form."""
    pos = {"kind": 0, "location": 1, "form": 2}[by]
    t = collections.defaultdict(lambda: {"uses": 0, "recorded": 0, "surfaced": 0})
    for r in reports:
        for key, c in r["cells"].items():
            k = key.split("|")[pos]
            for f in ("uses", "recorded", "surfaced"):
                t[k][f] += c[f]
    return dict(t)


def markdown(reports: list[dict]) -> str:
    lines = ["| Repository | Commit | Definitions | Unique names | Uses | Recorded | Surfaced |", "|---|---|---|---|---|---|---|"]
    for r in reports:
        rec = sum(c["recorded"] for c in r["cells"].values())
        sur = sum(c["surfaced"] for c in r["cells"].values())
        lines.append(f"| {r['label']} | `{r['rev'][:10]}` | {r['definitions']:,} | {r['unique_names']:,} | "
                     f"{r['uses']:,} | {rec / max(1, r['uses']):.0%} | {sur / max(1, r['uses']):.0%} |")
    for by in ("kind", "location", "form"):
        t = totals(reports, by)
        lines += ["", f"| {by.capitalize()} | Uses | Recorded | Surfaced |", "|---|---|---|---|"]
        for k, c in sorted(t.items(), key=lambda x: -x[1]["uses"]):
            lines.append(f"| {k} | {c['uses']:,} | {c['recorded'] / c['uses']:.0%} | {c['surfaced'] / c['uses']:.0%} |")
    np_ = collections.Counter()
    for r in reports:
        np_.update(r["non_python"]["files_by_type"])
    lines += ["", "Non-Python files mentioning a sampled unique name (up to 400 names per repository): "
              + ", ".join(f"{k} {v:,}" for k, v in np_.most_common())]
    return "\n".join(lines) + "\n"


def check(baseline: list[dict], new: list[dict], tolerance: float = 0.005) -> list[str]:
    """The kinds (overall, and per repository) whose recorded or surfaced share fell by more than `tolerance`."""
    drops = []
    def cmp(name, a, b):
        for k, ca in a.items():
            cb = b.get(k)
            if not cb or not ca["uses"] or not cb["uses"]:
                continue
            for f in ("recorded", "surfaced"):
                ra, rb = ca[f] / ca["uses"], cb[f] / cb["uses"]
                if rb < ra - tolerance:
                    drops.append(f"{name} / {k}: {f} {ra:.1%} -> {rb:.1%}")
    cmp("all", totals(baseline, "kind"), totals(new, "kind"))
    nb = {r["label"]: r for r in new}
    for r in baseline:
        if r["label"] in nb:
            cmp(r["label"], totals([r], "kind"), totals([nb[r["label"]]], "kind"))
    return drops


def _load(paths: list[str]) -> list[dict]:
    out = []
    for p in paths:
        files = sorted(os.path.join(p, f) for f in os.listdir(p) if f.endswith(".json")) if os.path.isdir(p) else [p]
        out += [json.load(open(f)) for f in files]
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit")
    a.add_argument("--repo", required=True)
    a.add_argument("--rev", required=True)
    a.add_argument("--label", required=True)
    a.add_argument("--out", required=True)
    s = sub.add_parser("summarize")
    s.add_argument("reports", nargs="+")
    s.add_argument("--markdown")
    c = sub.add_parser("check")
    c.add_argument("--baseline", required=True)
    c.add_argument("--new", required=True)
    w = sub.add_parser("shown")
    w.add_argument("--repo", required=True)
    w.add_argument("--rev", required=True)
    w.add_argument("--label", required=True)
    w.add_argument("--out", required=True)
    w.add_argument("--sample", type=int, default=300)
    w.add_argument("--seed", type=int, default=0)
    m = sub.add_parser("shown-summary")
    m.add_argument("reports", nargs="+")
    m.add_argument("--markdown")
    args = p.parse_args(argv)
    if args.cmd == "shown":
        rep = shown(os.path.abspath(args.repo), args.rev, args.label, args.sample, args.seed)
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        json.dump(rep, open(args.out, "w"), indent=1)
        print(json.dumps({"label": rep["label"], **rep["rows"]["all"]}))
        return 0
    if args.cmd == "shown-summary":
        md = shown_markdown(_load(args.reports))
        if args.markdown:
            open(args.markdown, "w").write(md)
        print(md)
        return 0
    if args.cmd == "audit":
        rep = audit(os.path.abspath(args.repo), args.rev, args.label)
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        json.dump(rep, open(args.out, "w"), indent=1)
        print(json.dumps({"label": rep["label"], "uses": rep["uses"],
                          "recorded": sum(c["recorded"] for c in rep["cells"].values())}))
        return 0
    if args.cmd == "summarize":
        md = markdown(_load(args.reports))
        if args.markdown:
            open(args.markdown, "w").write(md)
        print(md)
        return 0
    drops = check(_load([args.baseline]), _load([args.new]))
    print("\n".join(drops) if drops else "no drops")
    return 1 if drops else 0


if __name__ == "__main__":
    sys.exit(main())
