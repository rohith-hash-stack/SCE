"""Phase E target scouting (`harness/experiments/r4/PHASE2_PLAN.md`; the user's decision, 2026-11-21: derived tasks).

    python -B -m harness.experiments.r4.derive_scout OUT.json NAME=CLONE@REV ...

No model calls, no test runs. For each repository, at one revision, every production function, method and class
whose simple name is unique among the indexed definitions (the graph-coverage benchmark's rule) is measured:

- **dependent files:** the files other than its own with a use of it, as two independent readings agree:
  - PRISM's graphs: a main-graph edge (any relation) or a reference-graph edge into it, from a definition or module
    in that file;
  - the benchmark's own `ast` reading (`graph_coverage/benchmark.py`: locals, outside imports and builtins excluded).
  `dependents` is the union, `agreed` the files both readings name;
- **how the uses look:** imports under another name (`import ... as`), re-exports (a package `__init__`, `__all__`),
  qualified uses (`obj.name`, often through a typed variable), test files among the dependents;
- **grep noise:** files where `git grep -w` finds the name but neither reading finds a use, against the dependents
  (`grep_precision` = dependents found by the word / files with the word);
- **for the signature family:** the call sites passing each parameter positionally, per file;
- **for the move family:** whether a module-level function's body uses only builtins, its own parameters and names
  its module imports from elsewhere (it can move to a new module without an import cycle), and whether every
  dependent reaches it through `from <module> import <name>`.
"""
from __future__ import annotations

import ast
import builtins
import collections
import json
import os
import re
import subprocess
import sys
import tempfile

BUILTINS = set(dir(builtins))


def _git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, errors="replace").stdout


def _module(rel: str) -> str:
    m = rel[:-3].replace("/", ".")
    return m[:-9] if m.endswith(".__init__") else m


def _imported_as(trees: dict, leaf: str) -> dict:
    """{file: [(module, asname)]} for every `from X import leaf [as y]` and its line."""
    out = collections.defaultdict(list)
    for rel, tree in trees.items():
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                for a in n.names:
                    if a.name == leaf:
                        out[rel].append({"module": n.module, "level": n.level, "asname": a.asname, "line": n.lineno})
    return out


def _free_names(fn) -> set:
    """Names a function body (with decorators and annotations) reads that it does not bind itself."""
    bound = {a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs}
    bound |= {a.arg for a in (fn.args.vararg, fn.args.kwarg) if a}
    reads = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Name):
            (bound.add if isinstance(n.ctx, ast.Store) else reads.add)(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n is not fn:
            bound.add(n.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
    return reads - bound


def scout(name: str, clone: str, rev: str, min_files: int = 3) -> dict:
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
    from harness.experiments.graph_coverage.benchmark import Index, all_uses, is_test
    from prism.cli import LANGUAGE_TIER_PERMISSIVE, build_pipeline
    scratch = tempfile.mkdtemp(prefix=f"scout_{name}_")
    work = os.path.join(scratch, "w")
    subprocess.run(["git", "-C", clone, "worktree", "add", "-q", "--detach", work, rev], check=True)
    try:
        b, _ = build_pipeline(work, LANGUAGE_TIER_PERMISSIVE, use_cache=False)
        idx = Index(b, work)
        uses = all_uses(work, b, idx)
        by_target = collections.defaultdict(list)
        for u in uses:
            by_target[u["target"]].append(u)
        g, rg = b.graph, getattr(b, "reference_graph", None)

        def src_file(src: str) -> str | None:
            s = b.symbol_table.get(src)
            if s is not None:
                return os.path.relpath(s.file, work)
            if rg is not None and src in rg and rg.nodes[src].get("file"):
                return os.path.relpath(rg.nodes[src]["file"], work)
            return None

        py_files = [f for f in _git(work, "ls-files", "*.py").split()]
        trees = {}
        for rel in py_files:
            try:
                trees[rel] = ast.parse(open(os.path.join(work, rel), "rb").read())
            except (SyntaxError, ValueError, OSError, RecursionError):
                continue
        out = []
        for q, (rel, lo, hi, kind) in idx.defs.items():
            leaf = q.rsplit(".", 1)[-1]
            if idx.unique.get(leaf) != q or is_test(rel) or len(leaf) <= 3 or leaf.startswith("_"):
                continue
            prism_files = set()
            for container in (g, rg):
                if container is not None and q in container:
                    for src, _d in container.in_edges(q):
                        f = src_file(src)
                        if f and f != rel:
                            prism_files.add(f)
            bench = [u for u in by_target.get(q, []) if u["where"].rsplit(":", 1)[0] != rel]
            bench_files = {u["where"].rsplit(":", 1)[0] for u in bench}
            dependents = prism_files | bench_files
            if len(dependents) < min_files:
                continue
            word_files = set(_git(work, "grep", "-l", "-w", "-I", leaf, "--").split()) - {rel}
            found = dependents & word_files
            imports = _imported_as(trees, leaf)
            aliased = sum(1 for f, xs in imports.items() for x in xs if x["asname"] and x["asname"] != leaf)
            reexports = sorted(f for f in imports if f.endswith("__init__.py"))
            rec = {
                "repo": name, "rev": rev, "qname": q, "file": rel, "lines": [lo, hi], "kind": kind, "leaf": leaf,
                "dependents": sorted(dependents), "n_dependents": len(dependents),
                "agreed": sorted(prism_files & bench_files), "prism_only": sorted(prism_files - bench_files),
                "benchmark_only": sorted(bench_files - prism_files),
                "test_dependents": sorted(f for f in dependents if is_test(f)),
                "word_files": len(word_files), "grep_noise_files": len(word_files - dependents),
                "grep_precision": round(len(found) / len(word_files), 3) if word_files else None,
                "qualified_uses": sum(1 for u in bench if u["form"] == "qualified"),
                "aliased_imports": aliased, "reexports": reexports,
                "use_kinds": dict(collections.Counter(u["kind"] for u in bench)),
            }
            node = _def_node(trees.get(rel), q, rel)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                rec["params"] = [a.arg for a in node.args.posonlyargs + node.args.args]
                rec["kwonly"] = [a.arg for a in node.args.kwonlyargs]
                rec["vararg"] = bool(node.args.vararg)
                rec["positional_calls"] = _positional_calls(trees, leaf, rel, dependents)
                if kind == "function" and "." not in q[len(_module(rel)) + 1:]:
                    mod_tree = trees[rel]
                    own = {n.name for n in mod_tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                                           ast.ClassDef))}
                    own |= {t.id for n in mod_tree.body if isinstance(n, (ast.Assign, ast.AnnAssign))
                            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)}
                    free = _free_names(node) - BUILTINS
                    rec["move_free_names"] = sorted(free)
                    rec["move_self_contained"] = not (free & (own - {leaf}))
                    rec["move_from_imports_only"] = all(
                        any(x["line"] for x in imports.get(f, [])) for f in dependents if f.endswith(".py"))
            out.append(rec)
        return {"repo": name, "rev": rev, "definitions": len(idx.defs), "candidates": out}
    finally:
        subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", work], capture_output=True)
        subprocess.run(["rm", "-rf", scratch])


def _def_node(tree, q: str, rel: str):
    if tree is None:
        return None
    parts = q[len(_module(rel)) + 1:].split(".")
    body = tree.body
    node = None
    for p in parts:
        node = next((n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                     and n.name == p), None)
        if node is None:
            return None
        body = node.body
    return node


def _positional_calls(trees: dict, leaf: str, own: str, dependents: set) -> dict:
    """{file: [positional argument counts of each call to `leaf`]} in the dependent files and its own."""
    out = {}
    for rel in sorted(dependents | {own}):
        tree = trees.get(rel)
        if tree is None:
            continue
        counts = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                f = n.func
                name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
                if name == leaf and not any(isinstance(a, ast.Starred) for a in n.args):
                    counts.append(len(n.args))
        if counts:
            out[rel] = counts
    return out


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    out_path, specs = argv[0], argv[1:]
    reports = json.load(open(out_path)) if os.path.exists(out_path) else {}
    for spec in specs:
        name, rest = spec.split("=", 1)
        clone, rev = rest.split("@", 1)
        if name in reports:
            continue
        reports[name] = scout(name, clone, rev)
        json.dump(reports, open(out_path, "w"), indent=1)
        print(name, len(reports[name]["candidates"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
