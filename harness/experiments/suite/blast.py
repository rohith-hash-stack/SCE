"""Type 4 (blast radius): trace each repository's test suite, choose anchors, plan, prompt and score.

    python -m harness.experiments.suite.blast trace NAME CLONE ENVS_DIR OUT_EDGES.json [--tests DIR]
    python -m harness.experiments.suite.blast plan OUT.json EDGES_DIR NAME=CLONE ... [--per-repo 15] [--seed 5501]

`trace` runs the repository's tests at its checked-out commit (in its own environment) under
`trace_edges`. The output is the production call edges that really ran.

`plan`:
- **Anchor:** a production function with at least 3 traced production callers (at any depth) and at least
  one direct one. At most 15 per repository, drawn in a seeded order.
- **Answer key:** the traced callers, from the closure of the traced edges.
- **Versions:**
  - `FILE`: the anchor's file window;
  - `GREP`: + every line in the repository's production code that mentions the anchor's name, within the
    budget;
  - `PRISM`: + `prism.blast_radius` (production callers by hop, and the envelope with their code), within
    the same budget.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from harness.experiments.suite.execute import build_env

ARMS = ("FILE", "GREP", "PRISM")
BUDGET = 6000
FILE_WINDOW = 200
TEST_DIRS = {"tornado": "tornado/test"}
TRACE_TIMEOUT = 5400

INSTRUCTIONS = """You are a software engineer working in the repository `{repo}`.

The function `{anchor}` (in `{file}`) is about to change its behaviour. Which production functions would be
affected: the ones that call it, directly or through other functions? List every one you can find, nearest
first. Leave out tests.

Respond with a JSON object:
{{"affected": [{{"function": "<qualified name, e.g. package.module.Class.method>", "how": "<calls it directly | through <function>>"}}]}}
"""

FILE_BLOCK = """`{file}` (lines {lo}-{hi}):
```
{text}
```
"""
GREP_BLOCK = """Every line of production code in the repository that mentions `{leaf}`:
```
{lines}
```
"""
PRISM_BLOCK = """PRISM blast radius for `{anchor}` (production callers by distance, then their code):

{callers}

{envelope}
"""


# ------------------------------------------------------------------ tracing
def trace(name: str, clone: str, envs: str, out: str, tests: str | None = None) -> int:
    py, log = build_env(name, clone, envs)
    if py is None:
        print(log[-2000:])
        return 1
    env = dict(os.environ, SUITE_ROOT=clone, SUITE_OUT=os.path.abspath(out),
               PYTHONPATH=os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    tests = tests or TEST_DIRS.get(name, "tests")
    cmd = [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "harness.experiments.suite.trace_edges",
           "-o", "addopts=", "-x" if False else "-q", tests]
    try:
        r = subprocess.run(cmd, cwd=clone, env=env, capture_output=True, text=True, timeout=TRACE_TIMEOUT)
        tail = r.stdout[-1500:]
    except subprocess.TimeoutExpired:
        tail = "timeout (edges saved so far are kept)"
    print(tail)
    return 0 if os.path.exists(out) else 1


# ------------------------------------------------------------------ planning
def ancestors(edges: list[list], anchor: str) -> dict[str, int]:
    """Traced callers of `anchor` at any depth: `{caller: smallest distance}`."""
    up: dict[str, set[str]] = defaultdict(set)
    for u, v, _c in edges:
        up[v].add(u)
    dist, frontier, d = {}, [anchor], 0
    while frontier:
        d += 1
        nxt = []
        for x in frontier:
            for u in up.get(x, ()):
                if u != anchor and u not in dist:
                    dist[u] = d
                    nxt.append(u)
        frontier = nxt
    return dist


def _grep(clone: str, leaf: str, files: list[str]) -> str:
    out = []
    for f in files:
        try:
            for i, line in enumerate(Path(clone, f).read_text(errors="replace").splitlines(), 1):
                if re.search(rf"\b{re.escape(leaf)}\b", line):
                    out.append(f"{f}:{i}: {line.strip()[:160]}")
        except OSError:
            continue
    return "\n".join(out)


def _cap(text: str, budget: int) -> str:
    from prism.slicer.tokenizer import count_tokens
    if count_tokens(text) <= budget:
        return text
    lines, kept = text.splitlines(), []
    for ln in lines:
        if count_tokens("\n".join(kept + [ln])) > budget:
            kept.append(f"... ({len(lines) - len(kept)} more lines not shown)")
            break
        kept.append(ln)
    return "\n".join(kept)


def plan(out: Path, edges_dir: str, repos: dict[str, str], per_repo: int, seed: int) -> None:
    from prism.cli import build_pipeline
    from prism.mcp import server
    tasks = []
    for name, clone in sorted(repos.items()):
        path = Path(edges_dir, f"{name}.json")
        if not path.exists():
            print(f"{name}: no trace, not covered")
            continue
        data = json.loads(path.read_text())
        edges = data["edges"]
        b = build_pipeline(clone, use_cache=False)[0]
        prod = {q for q in b._def_nodes if (s := b.symbol_table.get(q)) is not None and s.kind in ("function", "method")
                and not b._in_test_file(s.file)}
        direct = defaultdict(set)
        for u, v, _c in edges:
            if u in prod and v in prod:
                direct[v].add(u)
        pool = sorted(a for a in prod if direct.get(a) and
                      len({u for u in ancestors(edges, a) if u in prod}) >= 3)
        random.Random(f"{seed}:{name}").shuffle(pool)
        files = sorted({os.path.relpath(b.symbol_table.get(q).file, clone) for q in prod})
        commit = subprocess.run(["git", "-C", clone, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        print(f"{name}: {len(edges)} traced edges, {len(pool)} eligible anchors", flush=True)
        server._cache.clear()
        for anchor in pool[:per_repo]:
            sym = b.symbol_table.get(anchor)
            rel = os.path.relpath(sym.file, clone)
            src = Path(sym.file).read_text(errors="replace").splitlines()
            lo = max(1, sym.line_range[0] - FILE_WINDOW // 2)
            hi = min(len(src), lo + FILE_WINDOW - 1)
            gold = {u: d for u, d in ancestors(edges, anchor).items() if u in prod}
            blast = server.prism_blast_radius(repo_path=clone, seed_symbol=anchor, budget_tokens=BUDGET,
                                              include_tests=False, format="xml")
            callers_text = "\n".join(f"- hop {c['hop']}: `{c['symbol']}` ({c['file']}:{c['line']})"
                                     for c in blast["callers"])
            leaf = anchor.rsplit(".", 1)[-1]
            tasks.append({
                "repo": name, "commit": commit, "anchor": anchor, "file": rel, "lines": list(sym.line_range),
                "file_window": "\n".join(src[lo - 1:hi]), "window": [lo, hi],
                "gold": gold, "gold_direct": sorted(u for u in direct[anchor] if u in prod),
                "grep_context": _cap(_grep(clone, leaf, files), BUDGET),
                "prism_callers": [c["symbol"] for c in blast["callers"]],
                "prism_context": _cap(PRISM_BLOCK.format(anchor=anchor, callers=callers_text, envelope=blast["envelope"]),
                                      BUDGET)})
            print(f"  {anchor}: {len(gold)} traced callers ({len(tasks[-1]['gold_direct'])} direct); "
                  f"PRISM lists {sum(g in tasks[-1]['prism_callers'] for g in gold)}", flush=True)
        server._cache.clear()
    for i, t in enumerate(tasks, 1):
        t["id"] = f"bl{i:03d}"
    body = {"type": "blast radius", "status": "frozen", "seed": seed, "per_repo": per_repo, "tasks": tasks,
            "tasks_sha256": hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest()}
    out.write_text(json.dumps(body, indent=1) + "\n")
    print(f"planned {len(tasks)} anchors; sha256 {body['tasks_sha256']}")


# ------------------------------------------------------------------ prompts / scoring
def prompt(task: dict, arm: str) -> str:
    lo, hi = task["window"]
    parts = [INSTRUCTIONS.format(repo=task["repo"], anchor=task["anchor"], file=task["file"]),
             FILE_BLOCK.format(file=task["file"], lo=lo, hi=hi, text=task["file_window"])]
    if arm == "GREP":
        parts.append(GREP_BLOCK.format(leaf=task["anchor"].rsplit(".", 1)[-1], lines=task["grep_context"]))
    elif arm == "PRISM":
        parts.append(task["prism_context"])
    return "\n".join(parts)


def _match(answer: str, qname: str) -> bool:
    a = re.sub(r"\(.*$", "", answer.strip().strip("`")).replace("::", ".")
    if not a:
        return False
    if a == qname or qname.endswith("." + a):
        return True
    a_parts, q_parts = a.split("."), qname.split(".")
    return a_parts[-1] == q_parts[-1] and (len(a_parts) == 1 or a_parts[-2] == q_parts[-2])


def score_task(task: dict, raw: str) -> dict:
    try:
        got = json.loads(raw)
        items = [x.get("function", "") for x in got.get("affected", []) if isinstance(x, dict)]
    except (ValueError, TypeError, AttributeError):
        items = []
    gold = task["gold"]
    found = [g for g in gold if any(_match(a, g) for a in items)]
    direct = [g for g in task["gold_direct"] if g in found]
    known = set(gold) | set(task["prism_callers"])
    unknown = [a for a in items if not any(_match(a, k) for k in known)]
    return {"answered": bool(items), "named": len(items), "recall": len(found) / len(gold) if gold else 0.0,
            "recall_direct": len(direct) / len(task["gold_direct"]) if task["gold_direct"] else 0.0,
            "found": found, "outside_traced_and_prism": len(unknown),
            "gold_items": [g in found for g in sorted(gold)]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("trace")
    t.add_argument("name")
    t.add_argument("clone")
    t.add_argument("envs")
    t.add_argument("out")
    t.add_argument("--tests")
    pl = sub.add_parser("plan")
    pl.add_argument("out")
    pl.add_argument("edges")
    pl.add_argument("repos", nargs="+")
    pl.add_argument("--per-repo", type=int, default=15)
    pl.add_argument("--seed", type=int, default=5501)
    args = p.parse_args(argv)
    if args.cmd == "trace":
        return trace(args.name, args.clone, args.envs, args.out, args.tests)
    plan(Path(args.out), args.edges, dict(a.split("=", 1) for a in args.repos), args.per_repo, args.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
