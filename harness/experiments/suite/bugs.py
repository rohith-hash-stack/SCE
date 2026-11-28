"""Type 3 (bug localisation): select, plan, prompt and score (README, type 3).

    python -m harness.experiments.suite.bugs select CANDIDATES.json OUT.json [--per-repo 20] [--seed 5501]
    python -m harness.experiments.suite.bugs plan TASKS.json NAME=CLONE ...

Selection: per repository, the candidates in a seeded order. Planning indexes each task's parent revision
and resolves the answer functions in the index. A task whose answer functions are not all in the index is
skipped and the next one in the seeded order is taken, until `--per-repo` tasks are planned. The order is
fixed by the seed before anything is indexed.

Each planned task carries, computed without a model:
- the repository's source file list (all versions);
- the top 10 of a plain BM25 search over the functions' names, files and code;
- the top 10 of `prism.ask`'s ranking (`NLQIndex.ranked`, no summaries) over the same functions;
- each hit's code and, for PRISM, its digest, within the same token budget for both.
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
import tempfile
from pathlib import Path

ARMS = ("TREE", "BM25", "PRISM")
TOP_K = 10
CONTEXT_BUDGET = 6000      # tokens of hits (code, digests) per version
TREE_CAP = 400             # file paths listed
HIT_LINES = 60             # lines of code shown per hit

INSTRUCTIONS = """You are a software engineer working in the repository `{repo}`.

A bug was reported and fixed. The report (names of the code involved are hidden as <name>):
---
{description}
---

Which functions must change to fix this bug? Name up to 5, most likely first.

Respond with a JSON object:
{{"functions": [{{"file": "<path>", "function": "<function name, with its class if it is a method>", "reason": "<one sentence>"}}]}}
"""

TREE_BLOCK = "The repository's source files:\n{tree}\n"
HITS_BLOCK = "{title}\n\n{hits}\n"


def _rel(path: str, root: str) -> str:
    return os.path.relpath(path, root) if os.path.isabs(path) else path


# ------------------------------------------------------------------ selection
def select(cands: list[dict], seed: int) -> list[dict]:
    """Every candidate, per repository in a seeded order (planning takes the first that resolve)."""
    by_repo: dict[str, list[dict]] = {}
    for c in sorted(cands, key=lambda c: (c["repo"], c["commit"])):
        by_repo.setdefault(c["repo"], []).append(c)
    out = []
    for repo in sorted(by_repo):
        pool = by_repo[repo]
        random.Random(f"{seed}:{repo}").shuffle(pool)
        out += [{**c, "order": i} for i, c in enumerate(pool)]
    return out


# ------------------------------------------------------------------ planning
def _source_tree(builder, root: str) -> list[str]:
    files = sorted({_rel(s.file, root) for q in builder._def_nodes if (s := builder.symbol_table.get(q)) is not None
                    and not builder._in_test_file(s.file)})
    return files


def _hit_text(builder, root: str, qname: str, digests=None) -> str:
    from prism.impact.style import source_of
    sym = builder.symbol_table.get(qname)
    code = "\n".join(source_of(builder, qname).splitlines()[:HIT_LINES])
    head = f"`{qname}` ({_rel(sym.file, root)}:{sym.line_range[0]})"
    if digests is None:
        return f"{head}\n```\n{code}\n```"
    return f"{head}\n```\n{code}\n```\nDigest:\n```\n{digests.digest(qname).render(header=False)}\n```"


def _fill(texts: list[tuple[str, str]], budget: int) -> tuple[list[str], str]:
    from prism.slicer.tokenizer import count_tokens
    shown, parts, spent = [], [], 0
    for q, t in texts:
        cost = count_tokens(t)
        if spent + cost > budget:
            continue
        shown.append(q)
        parts.append(t)
        spent += cost
    return shown, "\n\n".join(parts)


def plan_one(task: dict, work: str) -> dict | None:
    from harness.experiments.change_history.mine import _qname_for
    from prism.cli import build_pipeline
    from prism.query.nlq import NLQIndex, tokenize
    from prism.summaries.digest import DigestBuilder
    b = build_pipeline(work, use_cache=False)[0]
    gold = [_qname_for(b, work, g["file"], g["start"]) for g in task["gold"]]
    if not all(gold):
        return None
    index = NLQIndex(b, work, summaries=None)
    bm25 = [index.names[i] for i in index.bm25.rank(tokenize(task["description"]))][:TOP_K]
    prism = index.ranked(task["description"])[:TOP_K]
    digests = DigestBuilder(b)
    bm25_shown, bm25_text = _fill([(q, _hit_text(b, work, q)) for q in bm25], CONTEXT_BUDGET)
    prism_shown, prism_text = _fill([(q, _hit_text(b, work, q, digests)) for q in prism], CONTEXT_BUDGET)
    tree = _source_tree(b, work)
    return {**task, "gold_qnames": gold, "tree": tree[:TREE_CAP], "tree_total": len(tree),
            "bm25_hits": bm25, "bm25_shown": bm25_shown, "bm25_context": bm25_text,
            "prism_hits": prism, "prism_shown": prism_shown, "prism_context": prism_text}


def plan(path: Path, repos: dict[str, str], per_repo: int) -> None:
    data = json.loads(path.read_text())
    planned = {t["commit"]: t for t in data.get("tasks", [])}
    skipped = set(data.get("skipped", []))
    for repo in sorted({c["repo"] for c in data["candidates"]}):
        for c in sorted((c for c in data["candidates"] if c["repo"] == repo), key=lambda c: c["order"]):
            if sum(1 for t in planned.values() if t["repo"] == repo) >= per_repo:
                break
            if c["commit"] in planned or c["commit"] in skipped:
                continue
            work = tempfile.mkdtemp(prefix="bug_")
            subprocess.run(["git", "-C", repos[repo], "worktree", "add", "-q", "--detach", work, c["parent"]],
                           check=True, capture_output=True)
            try:
                got = plan_one(c, work)
            except Exception as exc:  # recorded as skipped, like an unresolved task
                print(f"  {repo} {c['commit'][:10]}: error {type(exc).__name__}: {exc}", flush=True)
                got = None
            finally:
                subprocess.run(["git", "-C", repos[repo], "worktree", "remove", "--force", work], capture_output=True)
            if got is None:
                skipped.add(c["commit"])
            else:
                planned[c["commit"]] = got
                print(f"  {repo} {c['commit'][:10]}: gold in BM25 top-10 "
                      f"{sum(g in got['bm25_hits'] for g in got['gold_qnames'])}/{len(got['gold_qnames'])}, "
                      f"PRISM {sum(g in got['prism_hits'] for g in got['gold_qnames'])}", flush=True)
            data["tasks"], data["skipped"] = list(planned.values()), sorted(skipped)
            path.write_text(json.dumps(data, indent=1) + "\n")  # resumable
    tasks = sorted(planned.values(), key=lambda t: (t["repo"], t["order"]))
    for i, t in enumerate(tasks, 1):
        t["id"] = f"bug{i:03d}"
    data["tasks"] = tasks
    data["status"] = "frozen"
    data["tasks_sha256"] = hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest()
    path.write_text(json.dumps(data, indent=1) + "\n")
    print(f"planned {len(tasks)} tasks ({len(skipped)} skipped); sha256 {data['tasks_sha256']}")


# ------------------------------------------------------------------ prompts
def prompt(task: dict, arm: str) -> str:
    tree = "\n".join(task["tree"]) + (f"\n... (+{task['tree_total'] - len(task['tree'])} more)"
                                      if task["tree_total"] > len(task["tree"]) else "")
    parts = [INSTRUCTIONS.format(repo=task["repo"], description=task["description"]), TREE_BLOCK.format(tree=tree)]
    if arm == "BM25":
        parts.append(HITS_BLOCK.format(title="Functions a text search over the code ranks highest for the report:",
                                       hits=task["bm25_context"]))
    elif arm == "PRISM":
        parts.append(HITS_BLOCK.format(title="PRISM's search results for the report (code and digest):",
                                       hits=task["prism_context"]))
    return "\n".join(parts)


# ------------------------------------------------------------------ scoring
def _norm_name(name: str) -> str:
    return re.sub(r"\(.*$", "", name.strip().strip("`")).replace("::", ".").replace("#", ".")


def matches(answer: dict, gold: dict) -> bool:
    """An answer names a gold function: the same leaf name and, when both name one, the same class; the
    file must agree when the answer gives one."""
    a = _norm_name(str(answer.get("function", "")))
    if not a:
        return False
    g = gold["name"]
    a_leaf, g_leaf = a.rsplit(".", 1)[-1], g.rsplit(".", 1)[-1]
    if a_leaf != g_leaf:
        return False
    a_owner = a.rsplit(".", 2)[-2] if a.count(".") else None
    g_owner = g.rsplit(".", 2)[-2] if g.count(".") else None
    if a_owner and g_owner and a_owner != g_owner and a_owner not in gold["file"].replace("/", "."):
        return False
    f = str(answer.get("file", "")).strip().lstrip("./")
    return not f or f == gold["file"] or gold["file"].endswith(f) or f.endswith(gold["file"])


def parse_answer(raw: str) -> list[dict]:
    try:
        got = json.loads(raw)
    except (ValueError, TypeError):
        return []
    items = got.get("functions", []) if isinstance(got, dict) else []
    return [x for x in items if isinstance(x, dict)][:5]


def score_task(task: dict, raw: str) -> dict:
    answers = parse_answer(raw)
    hit_rank = [next((i for i, a in enumerate(answers) if matches(a, g)), None) for g in task["gold"]]
    return {"answers": len(answers),
            "top1": any(r == 0 for r in hit_rank),
            "top5_any": any(r is not None for r in hit_rank),
            "recall5": sum(r is not None for r in hit_rank) / len(hit_rank),
            "gold_found": [r is not None for r in hit_rank]}


def retrieval_recall(task: dict, arm: str) -> float:
    hits = {"BM25": task["bm25_hits"], "PRISM": task["prism_hits"]}.get(arm)
    return sum(g in hits for g in task["gold_qnames"]) / len(task["gold_qnames"]) if hits is not None else 0.0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("candidates")
    s.add_argument("out")
    s.add_argument("--seed", type=int, default=5501)
    s.add_argument("--per-repo", type=int, default=20)
    pl = sub.add_parser("plan")
    pl.add_argument("tasks")
    pl.add_argument("repos", nargs="+")
    args = p.parse_args(argv)
    if args.cmd == "select":
        cands = select(json.loads(Path(args.candidates).read_text()), args.seed)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"type": "bug localisation", "status": "selected", "seed": args.seed,
                                              "per_repo": args.per_repo, "candidates": cands}, indent=1) + "\n")
        print(f"{len(cands)} candidates in seeded order")
    else:
        data = json.loads(Path(args.tasks).read_text())
        plan(Path(args.tasks), dict(a.split("=", 1) for a in args.repos), data["per_repo"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
