"""Derive T5 (blast) tasks from a corpus's T2 (debug) tasks (M4 item 4).

    python -m benchmarks.scripts.derive_t5_from_t2 --corpus fastapi [--write] [--report out.json]

For each T2 task, the seed is the T2 task's seed, and the gold affected set
is the seed's transitive callers, restricted to production code:

- Python (FastAPI, Django): PRISM's call graph (`prism.cli.build_pipeline`,
  `use_cache=False`). Callers are predecessors over `CALLS` and
  `INSTANTIATES` edges, followed transitively.
- TypeScript (Express, tRPC): typescript-language-server, the server Arm 3
  uses. `textDocument/references` on the seed's definition; each reference
  that is a call (`f(...)`, `a.f(...)`, `new F(...)`, checked with
  tree-sitter) maps to its enclosing outline symbol (the nearest named
  one: anonymous callbacks are not symbols), and that symbol's callers are
  followed in turn. Call sites outside any symbol
  (module-level code) have no caller symbol and are counted, not kept.

Production code: callers defined under test, example, docs, benchmark or
script directories, or in test files (`test_*.py`, `*_test.py`,
`tests.py`, `conftest.py`, `*.test.ts`, `*.spec.ts`), are left out, following the
existing T5 tasks (their gold is production call sites only).

Determinism: the derivation runs twice, each from a fresh build or a fresh
server, the second with the seeds in reverse order. A task whose two runs
differ is excluded and logged. For TypeScript every source file is opened
before the first query: tsserver searches references only in the projects
of open files, so results would otherwise depend on query order. The two runs
are written as `annotation_a` and `annotation_b`, so the loader's
agreement gate checks reproducibility: identical sets give 1.0. This is a
determinism check, not human inter-annotator agreement.

Gold names drop PRISM's `#N` collision suffix (`fastapi.routing.app#2` is a
second nested `app`; no answer can name the suffix), merging such entries.

Seeds with fewer than `MIN_CALLERS` production callers are skipped: there is
no blast radius to measure. T2 task files are never modified.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import deque
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIN_CALLERS = 2
PY_CORPORA = {"fastapi", "django"}
NON_PRODUCTION = re.compile(
    r"(^|/)(tests?|__tests__|testing|docs_src|docs?|examples?|benchmarks?|scripts)(/|$)"
    r"|(^|/)test_[^/]*\.py$|_tests?\.py$|(^|/)tests?\.py$|(^|/)conftest\.py$|\.(test|spec)\.[jt]sx?$"
)


def is_production(rel_path: str) -> bool:
    return not NON_PRODUCTION.search(rel_path.replace(os.sep, "/"))


_IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")


def named_fqn(module: str, outline: list[dict], line: int, character: int, contains) -> tuple[str, dict | None]:
    """The fully-qualified name of the innermost *named* outline symbol whose
    range contains the position, and that symbol (None at module level).
    The walk stops at the first symbol whose name is not an identifier:
    tsserver names anonymous callbacks `<function>` or
    `self.process_params() callback`, so a call inside one is credited to
    the nearest named enclosing symbol."""
    parts, node, best = [module], outline, None
    while True:
        hit = next((s for s in node if contains(s, line, character)), None)
        if hit is None or not _IDENT.match(hit.get("name", "")):
            break
        parts.append(hit["name"])
        best, node = hit, hit.get("children") or []
    return ".".join(p for p in parts if p), best


_COLLISION_SUFFIX = re.compile(r"#\d+$")


def gold_names(callers: dict[str, tuple[int, str]], seed: str | set[str]) -> dict[str, int]:
    """Production callers as gold names, {name: depth}. PRISM's collision
    suffix (`app#2`: a second definition sharing a qualified name) is an
    internal disambiguator no answer can name, so it is dropped and the
    entries merge at their smallest depth."""
    seeds = {seed} if isinstance(seed, str) else set(seed)
    out: dict[str, int] = {}
    for caller, (depth, rel) in callers.items():
        name = _COLLISION_SUFFIX.sub("", caller)
        if is_production(rel) and name not in seeds:
            out[name] = min(depth, out.get(name, depth))
    return out


def closure(callers_of, seed: str) -> dict[str, tuple[int, str]]:
    """{caller: (depth, rel_file)} over `callers_of(symbol) -> {(caller,
    rel_file)}`, breadth-first, deterministic order."""
    seen: dict[str, tuple[int, str]] = {seed: (0, "")}
    dq = deque([seed])
    while dq:
        x = dq.popleft()
        for caller, rel in sorted(callers_of(x)):
            if caller not in seen:
                seen[caller] = (seen[x][0] + 1, rel)
                dq.append(caller)
    seen.pop(seed)
    return seen


# ------------------------------------------------------------------ Python
def python_run(root: str, seeds: list[str]) -> dict[str, dict]:
    from prism.cli import build_pipeline

    builder, _ = build_pipeline(root, use_cache=False)
    preds: dict[str, set[str]] = {}
    for u, v, data in builder.graph.edges(data=True):
        if data.get("relation") in ("CALLS", "INSTANTIATES"):
            preds.setdefault(v, set()).add(u)

    def rel_of(sym: str) -> str:
        s = builder.symbol_table.get(sym)
        return os.path.relpath(s.file, root) if s is not None else ""

    def callers_of(sym: str) -> set[tuple[str, str]]:
        return {(p, rel_of(p)) for p in preds.get(sym, ())}

    return {seed: {"seed_found": seed in builder.graph, "callers": closure(callers_of, seed), "module_level": 0}
            for seed in seeds}


# -------------------------------------------------------------- TypeScript
def typescript_run(root: str, corpus: str, seeds: list[str], reverse: bool = False) -> dict[str, dict]:
    from harness.arms.arm3_lsp import Arm3PyrightLSP
    from harness.ast_splitter import TypeScriptSplitter
    from harness.pyright_client import path_to_uri, uri_to_path

    class _Count:
        name = "count"
        def count(self, t): return len(t.split())

    arm = Arm3PyrightLSP(tokenizer=_Count())
    arm.index(root, {"repo_id": corpus})
    root = arm.repo_root
    # tsserver searches references only in the projects of open files, so the
    # results would depend on query order: open every source file first
    from harness.ast_splitter import iter_source_files
    for f in iter_source_files(root, "typescript"):
        arm._open(str(Path(f).resolve()), {})
    arm.client.project_info(arm.client.anchor)
    splitter, trees = TypeScriptSplitter(tokenizer=None), {}

    def tree(path: str):
        if path not in trees:
            trees[path] = splitter._parser_for(os.path.relpath(path, root)).parse(Path(path).read_bytes())
        return trees[path]

    def is_call(path: str, line: int, char: int) -> bool:
        text = arm._lines(path)[line]
        col = len(text[:char].encode("utf-8"))
        n = tree(path).root_node.descendant_for_point_range((line, col), (line, col + 1))
        while n is not None and n.type not in ("identifier", "property_identifier"):
            n = n.parent
        if n is None:
            return False
        p = n.parent
        if p is not None and p.type == "member_expression" and p.child_by_field_name("property") == n:
            n, p = p, p.parent
        return p is not None and (
            (p.type == "call_expression" and p.child_by_field_name("function") == n)
            or (p.type == "new_expression" and p.child_by_field_name("constructor") == n))

    def locate(fqn: str):
        path, inner = arm._resolve_seed(fqn)
        if path is None or not inner:
            return None
        sym = arm._find_symbol(arm._symbols(path, {}), inner)
        if sym is None:
            return None
        pos = sym.get("selectionRange", sym["range"])["start"]
        return path, pos["line"], pos["character"]

    module_level = {"n": 0}

    def callers_of(fqn: str) -> set[tuple[str, str]]:
        loc = locate(fqn)
        if loc is None:
            return set()
        path, line, char = loc
        arm._open(path, {})
        refs = arm.client.request("textDocument/references", {
            "textDocument": {"uri": path_to_uri(path)}, "position": {"line": line, "character": char},
            "context": {"includeDeclaration": False}}) or []
        out = set()
        for r in refs:
            p = str(Path(uri_to_path(r["uri"])).resolve())
            if not arm._in_repo(p):
                continue
            start = r["range"]["start"]
            arm._open(p, {})
            if not is_call(p, start["line"], start["character"]):
                continue
            caller, sym = named_fqn(arm._module_of(p), arm._symbols(p, {}), start["line"], start["character"],
                                    arm._contains)
            if sym is None:
                module_level["n"] += 1
                continue
            if caller != fqn:
                out.add((caller, os.path.relpath(p, root)))
        return out

    result = {}
    try:
        for seed in (reversed(seeds) if reverse else seeds):
            module_level["n"] = 0
            loc = locate(seed)
            # the seed's own name in tsserver's outline nesting: Express's T2 seed `lib.router.next`
            # is `lib.router.handle.next` there, and its recursive calls must not make it its own caller
            alias = named_fqn(arm._module_of(loc[0]), arm._symbols(loc[0], {}), loc[1], loc[2],
                              arm._contains)[0] if loc else seed
            result[seed] = {"seed_found": loc is not None, "callers": closure(callers_of, seed) if loc else {},
                            "module_level": module_level["n"], "aliases": sorted({seed, alias})}
    finally:
        arm.close()
    return result


# ------------------------------------------------------------------ driver
def derive(corpus: str) -> dict:
    from benchmarks.corpora.resolver import resolve
    from benchmarks.ground_truth.loader import load_tasks_from_dir

    root = str(resolve(corpus))
    t2 = sorted((t for t in load_tasks_from_dir(REPO_ROOT / f"benchmarks/ground_truth/tasks/{corpus}").accepted
                 if t.task_type == "debug"), key=lambda t: t.task_id)
    seeds = sorted({t.seed_symbol for t in t2})
    # two independent runs; the second takes the seeds in reverse order, so an
    # order-dependent result (e.g. a language server's loaded project set) shows up
    if corpus in PY_CORPORA:
        first, second = python_run(root, seeds), python_run(root, seeds[::-1])
    else:
        first, second = typescript_run(root, corpus, seeds), typescript_run(root, corpus, seeds, reverse=True)
    rows = []
    for t in t2:
        a, b = first[t.seed_symbol], second[t.seed_symbol]
        seed_names = set(a.get("aliases") or [t.seed_symbol])
        prod_a, prod_b = gold_names(a["callers"], seed_names), gold_names(b["callers"], seed_names)
        row = {"t2_task_id": t.task_id, "seed": t.seed_symbol, "seed_found": a["seed_found"],
               "all_callers": len(a["callers"]), "production_callers": prod_a,
               "module_level_call_sites": a["module_level"], "deterministic": a == b and prod_a == prod_b}
        if not row["deterministic"]:
            row["status"] = "excluded: nondeterministic"
        elif not a["seed_found"]:
            row["status"] = "skipped: seed not found"
        elif len(prod_a) < MIN_CALLERS:
            row["status"] = f"skipped: {len(prod_a)} production caller(s) < {MIN_CALLERS}"
        else:
            row["status"] = "derived"
        rows.append(row)
    return {"corpus": corpus, "root": root, "rows": rows}


def short_name(t2_task_id: str) -> str:
    m = re.match(r"^[a-z]+_t\d+_\d+_(.+)$", t2_task_id)
    return m.group(1) if m else t2_task_id


def _yaml_list(items: list[str], indent: str = "    ") -> str:
    return "\n".join(f"{indent}- {s}" for s in items)


def render_yaml(corpus: str, pinned_commit: str, task_id: str, row: dict) -> str:
    seed, callers = row["seed"], row["production_callers"]
    gold = sorted(callers)
    direct = sum(1 for d in callers.values() if d == 1)
    deepest = max(callers.values())
    method = ("PRISM's call graph (prism.cli.build_pipeline, use_cache=False): CALLS/INSTANTIATES predecessors"
              if corpus in PY_CORPORA else
              "typescript-language-server textDocument/references (call sites only), mapped to the enclosing symbol")
    summary = (f"{len(gold)} production callers of {seed}: {direct} direct, "
               f"{len(gold) - direct} transitive (up to {deepest} hops).")
    body = "\n".join([
        f"# Derived T5 (blast) task. Derived from T2 task: {row['t2_task_id']}",
        "# Generated by benchmarks/scripts/derive_t5_from_t2.py; do not edit by hand.",
        f"# Gold affected set: the seed's transitive callers via {method},",
        "# production code only (test/example/docs/benchmark/script code excluded).",
        "# annotation_a and annotation_b are two independent derivation runs (fresh",
        "# build / fresh server); their agreement checks determinism, not human judgement.",
        f"task_id: {task_id}",
        f"repo: {corpus}",
        f"pinned_commit: {pinned_commit}",
        f"seed_symbol: {seed}",
        "task_type: blast",
        f"prompt: \"What breaks if `{seed}` changes?\"",
        "",
    ])
    for key, who in (("annotation_a", "derivation_run_1"), ("annotation_b", "derivation_run_2"),
                     ("adjudicated", "adjudicator")):
        body += (f"\n{key}:\n  annotator_id: {who}\n  critical_callers:\n{_yaml_list(gold)}\n"
                 f"  expected_solution: >\n    {summary}\n")
    return body


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--write", action="store_true", help="write the derived tasks' YAML files")
    ap.add_argument("--report", default=None, help="write the per-task derivation report as JSON")
    a = ap.parse_args(argv)
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10_000))
    out = derive(a.corpus)
    pins = json.loads((REPO_ROOT / "benchmarks/corpora/pinned_commits.json").read_text())
    pinned = pins[a.corpus]["pinned_commit"] if isinstance(pins.get(a.corpus), dict) else pins[a.corpus]
    task_dir = REPO_ROOT / f"benchmarks/ground_truth/tasks/{a.corpus}"
    n = 0
    for row in out["rows"]:
        if row["status"] != "derived":
            continue
        n += 1
        row["t5_task_id"] = f"{a.corpus}_t5_{n:03d}_{short_name(row['t2_task_id'])}"
        if a.write:
            (task_dir / f"{row['t5_task_id']}.yaml").write_text(render_yaml(a.corpus, pinned, row["t5_task_id"], row))
    for row in out["rows"]:
        print(f"{row['t2_task_id']:55s} prod={len(row['production_callers']):3d} all={row['all_callers']:4d} "
              f"{row['status']}{'  -> ' + row['t5_task_id'] if 't5_task_id' in row else ''}")
    print(f"{a.corpus}: {n} derived T5 tasks{' written' if a.write else ''}")
    if a.report:
        Path(a.report).write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
