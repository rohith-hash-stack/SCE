"""Phase 3 target scouting: methods whose uses grep cannot tell apart (the user's decision, 2026-11-22: hidden
dependencies, real code only, at most 12 places, answer keys from PRISM's resolved uses).

    python -B -m harness.experiments.r4.hidden_scout OUT.json NAME=CLONE@REV ...

No model calls, no test runs. Phase 2's targets had unique names, so `grep -w` found every place (recall 100%). Here
the target is a method whose simple name other objects also use (`.close`, `.send`, `.get_schema`): the places to
change are only the calls PRISM resolves to *this* method, and the look-alikes must stay.

**The places** come from PRISM itself, site by site: while the graph is built, every call edge into the method is
recorded with the position of the call (`record_call_sites`; PRISM's graph keeps only one edge per caller), and every
non-call use from the reference graph's lines. A candidate is kept only if PRISM's reading is unambiguous:
- no tentative (guessed) call into it, no override in either direction, no edge into it that no recorded place
  explains;
- every reference-graph line holds exactly as many uses of the name as the edge counts;
- no string literal anywhere equals the name (a `getattr(obj, "name")` PRISM does not read);
- 2-12 places outside the definition, in 2+ files other than its own, at least one of them production code.

**How hidden** (the ranking): `lookalikes`, the uses of the same name (`x.name`, `name=` and bare names) that are not
places; `grep_precision`, the files with a place / the files `git grep -w` finds.
"""
from __future__ import annotations

import ast
import collections
import json
import os
import subprocess
import sys
import tempfile

MIN_PLACES, MAX_PLACES, MIN_FILES = 2, 12, 2
#: Phase 3 amendment 3 (large fan-out, the user's decision 2026-11-22): `--places 15-30 --min-files 3 --kinds
#: method,function,class`
KINDS = ("method",)


def _git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, errors="replace").stdout


def record_call_sites(work: str):
    """Build PRISM's graphs for `work` and record each call edge's site: `(builder, [{caller, target, line, col,
    tentative}])`, `col` in UTF-8 bytes (as `ast` counts). The build is PRISM's own; only two functions are wrapped while it runs."""
    import networkx as nx
    import prism.graph.concrete_builder as CB
    from prism.cli import LANGUAGE_TIER_PERMISSIVE, build_pipeline
    last: dict = {}
    sites: list[dict] = []
    orig_ctx, orig_add = CB.compute_call_site_context, nx.DiGraph.add_edge

    def ctx(call_node, def_node, lang, source):
        last["node"], last["source"] = call_node, source
        return orig_ctx(call_node, def_node, lang, source)

    def add_edge(self, u, v, **kw):
        node = last.pop("node", None)
        if node is not None and kw.get("relation") == "CALLS":
            fn = node.child_by_field_name("function")
            name = fn.child_by_field_name("attribute") if fn is not None and fn.type == "attribute" else fn
            if name is not None:
                row, bcol = name.start_point
                sites.append({"caller": u, "target": v, "line": row + 1, "col": bcol,
                              "name": name.text.decode("utf8", "replace"), "tentative": kw.get("kind") == "TENTATIVE_CALL"})
        return orig_add(self, u, v, **kw)

    CB.compute_call_site_context, nx.DiGraph.add_edge = ctx, add_edge
    try:
        b, _ = build_pipeline(work, LANGUAGE_TIER_PERMISSIVE, use_cache=False)
    finally:
        CB.compute_call_site_context, nx.DiGraph.add_edge = orig_ctx, orig_add
    return b, sites


def name_nodes(tree, leaf: str) -> list[tuple[int, int, str]]:
    """Every use of `leaf` in a module: `(line, col, form)` for `x.leaf`, a bare `leaf`, a `leaf=` keyword, a def or
    class named `leaf`, an import alias; `col` (UTF-8 bytes) is where the name itself starts."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and n.attr == leaf:
            out.append((n.end_lineno, n.end_col_offset - len(leaf), "attribute"))
        elif isinstance(n, ast.Name) and n.id == leaf:
            out.append((n.lineno, n.col_offset, "name"))
        elif isinstance(n, ast.keyword) and n.arg == leaf:
            out.append((n.lineno, n.col_offset, "keyword"))
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == leaf:
            out.append((n.lineno, -1, "def"))
        elif isinstance(n, ast.alias) and leaf in (n.name.rsplit(".", 1)[-1], n.asname):
            exact = n.asname is None or n.asname == leaf and n.name.rsplit(".", 1)[-1] == leaf
            col = n.col_offset + len(n.name) - len(leaf) if exact and hasattr(n, "col_offset") else -1
            out.append((n.lineno, col, "import"))           # `import x as leaf`: no column, the candidate is dropped
        elif isinstance(n, ast.Constant) and n.value == leaf and n.lineno == n.end_lineno \
                and n.end_col_offset - n.col_offset == len(leaf) + 2:
            out.append((n.lineno, n.col_offset + 1, "string"))   # a plain quoted string ('leaf' or "leaf")
    return out


def string_uses(tree, leaf: str) -> int:
    return sum(1 for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == leaf)


def scout(name: str, clone: str, rev: str) -> dict:
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
    from harness.experiments.graph_coverage.benchmark import is_test
    from harness.experiments.r4.derive_tasks import NOT_PRODUCT
    scratch = tempfile.mkdtemp(prefix=f"hscout_{name}_")
    work = os.path.join(scratch, "w")
    subprocess.run(["git", "-C", clone, "worktree", "add", "-q", "--detach", work, rev], check=True)
    try:
        from prism.cli import LANGUAGE_TIER_PERMISSIVE, build_pipeline
        b, _ = build_pipeline(work, LANGUAGE_TIER_PERMISSIVE, use_cache=False)   # call edges keep their `sites`
        g, rg = b.graph, getattr(b, "reference_graph", None)
        rel = lambda p: os.path.relpath(p, work) if p and os.path.isabs(p) else p
        trees = {}
        for f in _git(work, "ls-files", "*.py").split():
            try:
                trees[f] = ast.parse(open(os.path.join(work, f), "rb").read())
            except (SyntaxError, ValueError, OSError, RecursionError):
                continue
        by_leaf = collections.Counter(q.rsplit(".", 1)[-1] for q, s in b.symbol_table._symbols.items()
                                      if getattr(s, "kind", None) in ("method", "function"))
        out, reasons = [], collections.Counter()
        for q, sym in list(b.symbol_table._symbols.items()):
            if getattr(sym, "kind", None) not in KINDS:
                continue
            leaf = q.rsplit(".", 1)[-1]
            own = rel(sym.file)
            if leaf.startswith("_") or len(leaf) < 3 or is_test(own) or NOT_PRODUCT.search(own) or own not in trees:
                continue
            if KINDS == ("method",) and by_leaf[leaf] < 2 and not any(
                    f for f, t in trees.items() for x in name_nodes(t, leaf) if x[2] == "attribute"):
                continue
            why = None
            places = []                                 # (file, line, col, how)
            if q in g:
                for src, _d, e in g.in_edges(q, data=True):
                    r = e.get("relation")
                    ss = b.symbol_table.get(src)
                    f = rel(ss.file) if ss is not None else None
                    if r == "OVERRIDES":
                        why = "override"
                    elif r in ("CALLS", "INSTANTIATES"):
                        if e.get("kind") == "TENTATIVE_CALL":
                            why = "tentative call"
                        elif e.get("via") == "name_match":
                            why = "a call matched by name only"
                        elif not e.get("sites") or f is None:
                            why = "call edge without a site"
                        else:
                            text = open(os.path.join(work, f), "rb").read().split(b"\n")
                            for line, col in e["sites"]:
                                if text[line - 1][col:col + len(leaf)] != leaf.encode():
                                    why = "a call site does not hold the name"
                                    break
                                places.append((f, line, col, "instantiate" if r == "INSTANTIATES" else "call"))
                    elif r in ("EXTENDS", "IMPLEMENTS"):
                        cls_line = next((n.lineno for n in ast.walk(trees.get(f)) if isinstance(n, ast.ClassDef)
                                         and n.name == src.rsplit(".", 1)[-1]), None) if trees.get(f) else None
                        bases = [x for x in name_nodes(trees[f], leaf) if x[0] == cls_line
                                 and x[2] in ("attribute", "name")] if cls_line else []
                        if len(bases) != 1:
                            why = "a subclass's base not found"
                        else:
                            places.append((f, bases[0][0], bases[0][1], "base"))
                    elif r != "ENCLOSES":
                        why = f"{r} edge"
                    if why:
                        break
                if why is None and any(e.get("relation") == "OVERRIDES" for _s, _d, e in g.out_edges(q, data=True)):
                    why = "override"
            if why is None and rg is not None and q in rg:
                for src, _d, e in rg.in_edges(q, data=True):
                    node = rg.nodes[src]
                    f = rel(node.get("file")) if node.get("kind") == "module" else \
                        rel(getattr(b.symbol_table.get(src), "file", None))
                    tree = trees.get(f)
                    lines = set(e.get("lines", ()))
                    found = [x for x in name_nodes(tree, leaf) if x[0] in lines
                             and x[2] in ("attribute", "name", "import", "string")] if tree is not None else []
                    if len(found) != e["count"] or any(x[1] < 0 for x in found):
                        why = "reference lines do not account for each use exactly"   # cut at 5 lines, a look-alike
                        break
                    places += [(f, ln, col, k) for ln, col, k in found]
            if why is None:
                pk = {(p[0], p[1], p[2]) for p in places}
                stray = [(f, x[0]) for f, t in trees.items() for x in name_nodes(t, leaf)
                         if x[2] == "string" and (f, x[0], x[1]) not in pk]
                if stray or any(string_uses(t, leaf) > sum(1 for x in name_nodes(t, leaf) if x[2] == "string")
                                for t in trees.values()):
                    why = "the name appears as a string PRISM does not resolve"
            seen = {}
            for p in places:
                seen.setdefault(p[:3], p)
            places = sorted(seen.values())
            files = {p[0] for p in places}
            if why is None and not MIN_PLACES <= len(places) <= MAX_PLACES:
                why = f"{len(places)} places"
            if why is None and len(files - {own}) < MIN_FILES:
                why = f"fewer than {MIN_FILES} other files"
            if why is None and all(is_test(f) for f in files - {own}):
                why = "only tests use it"
            if why:
                reasons[why] += 1
                continue
            place_keys = {(p[0], p[1], p[2]) for p in places}
            look = [(f, x[0]) for f, t in trees.items() for x in name_nodes(t, leaf)
                    if x[2] in ("attribute", "name", "keyword") and (f, x[0], x[1]) not in place_keys
                    and not (f == own and x[2] == "def")]
            word_files = set(_git(work, "grep", "-l", "-w", "-I", leaf, "--").split()) - {own}
            out.append({
                "repo": name, "rev": rev, "qname": q, "file": own, "lines": list(sym.line_range), "kind": sym.kind,
                "leaf": leaf, "places": [{"file": f, "line": ln, "col": c, "how": h} for f, ln, c, h in places],
                "n_places": len(places), "files": sorted(files), "test_files": sorted(f for f in files if is_test(f)),
                "lookalikes": len(look), "lookalike_files": len({f for f, _ in look}),
                "word_files": len(word_files),
                "grep_precision": round(len(files & word_files) / len(word_files), 3) if word_files else None,
                "same_name_defs": by_leaf[leaf],
            })
        return {"repo": name, "rev": rev, "candidates": out, "rejected": dict(reasons)}
    finally:
        subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", work], capture_output=True)
        subprocess.run(["rm", "-rf", scratch])


def main(argv=None) -> int:
    global MIN_PLACES, MAX_PLACES, MIN_FILES, KINDS
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("out")
    p.add_argument("specs", nargs="+")
    p.add_argument("--places", default=f"{MIN_PLACES}-{MAX_PLACES}")
    p.add_argument("--min-files", type=int, default=MIN_FILES)
    p.add_argument("--kinds", default=",".join(KINDS))
    a = p.parse_args(argv)
    MIN_PLACES, MAX_PLACES = (int(x) for x in a.places.split("-"))
    MIN_FILES, KINDS = a.min_files, tuple(a.kinds.split(","))
    out_path, specs = a.out, a.specs
    reports = json.load(open(out_path)) if os.path.exists(out_path) else {}
    for spec in specs:
        name, rest = spec.split("=", 1)
        clone, rev = rest.split("@", 1)
        if name in reports:
            continue
        reports[name] = scout(name, clone, rev)
        json.dump(reports, open(out_path, "w"), indent=1)
        print(name, len(reports[name]["candidates"]), reports[name]["rejected"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
