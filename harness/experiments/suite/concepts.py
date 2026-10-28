"""Type 5 (conceptual questions, hand-written; reported separately): plan, prompt and score.

    python -m harness.experiments.suite.concepts plan OUT.json QUESTIONS_DIR NAME=CLONE ...

Each repository's frozen questions (`questions/<repo>.json`) are planned at the commit they were written
against (the clone must be checked out there). There are three versions, as for bug localisation (type 3),
with the same budget:
- `TREE`: the source file list;
- `BM25`: + the top 10 functions a BM25 search over the code ranks for the question;
- `PRISM`: + the top 10 of `prism.ask`'s ranking, with code and digests.

Score: recall of the answer functions among the AI's (up to 5) answers, and the retrieval's own recall@10.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from harness.experiments.suite import bugs

ARMS = bugs.ARMS

INSTRUCTIONS = """You are a software engineer working in the repository `{repo}`.

A question about how the code works:
---
{question}
---

Which functions implement this? Name up to 5, most important first.

Respond with a JSON object:
{{"functions": [{{"file": "<path>", "function": "<function name, with its class if it is a method>", "reason": "<one sentence>"}}]}}
"""


def plan(out: Path, qdir: str, repos: dict[str, str]) -> None:
    from prism.cli import build_pipeline
    from prism.query.nlq import NLQIndex, tokenize
    from prism.summaries.digest import DigestBuilder
    tasks = []
    for name, clone in sorted(repos.items()):
        q = json.loads(Path(qdir, f"{name}.json").read_text())
        head = subprocess.run(["git", "-C", clone, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        if q["status"] != "frozen" or head != q["commit"]:
            raise SystemExit(f"{name}: questions not frozen, or the clone is not at {q['commit'][:10]}")
        b = build_pipeline(clone, use_cache=False)[0]
        index = NLQIndex(b, clone, summaries=None)
        digests = DigestBuilder(b)
        tree = bugs._source_tree(b, clone)
        for i, item in enumerate(q["questions"], 1):
            gold = []
            for a in item["answer"]:
                s = b.symbol_table.get(a)
                rel = os.path.relpath(s.file, clone)
                module = os.path.splitext(rel)[0].replace("/", ".")
                gold.append({"file": rel, "name": a[len(module) + 1:] if a.startswith(module + ".") else a.rsplit(".", 1)[-1]})
            bm25 = [index.names[j] for j in index.bm25.rank(tokenize(item["q"]))][:bugs.TOP_K]
            prism = index.ranked(item["q"])[:bugs.TOP_K]
            _s1, bm25_text = bugs._fill([(x, bugs._hit_text(b, clone, x)) for x in bm25], bugs.CONTEXT_BUDGET)
            _s2, prism_text = bugs._fill([(x, bugs._hit_text(b, clone, x, digests)) for x in prism], bugs.CONTEXT_BUDGET)
            tasks.append({"repo": name, "commit": head, "question": item["q"], "doc": item["doc"], "gold": gold,
                          "gold_qnames": item["answer"], "tree": tree[:bugs.TREE_CAP], "tree_total": len(tree),
                          "bm25_hits": bm25, "bm25_context": bm25_text, "prism_hits": prism,
                          "prism_context": prism_text, "number": i})
            print(f"  {name} q{i}: BM25 top-10 {sum(g in bm25 for g in item['answer'])}/{len(item['answer'])}, "
                  f"PRISM {sum(g in prism for g in item['answer'])}", flush=True)
    for i, t in enumerate(tasks, 1):
        t["id"] = f"cq{i:03d}"
    body = {"type": "conceptual questions", "status": "frozen", "tasks": tasks,
            "tasks_sha256": hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest()}
    out.write_text(json.dumps(body, indent=1) + "\n")
    print(f"planned {len(tasks)} questions; sha256 {body['tasks_sha256']}")


def prompt(task: dict, arm: str) -> str:
    tree = "\n".join(task["tree"]) + (f"\n... (+{task['tree_total'] - len(task['tree'])} more)"
                                      if task["tree_total"] > len(task["tree"]) else "")
    parts = [INSTRUCTIONS.format(repo=task["repo"], question=task["question"]), bugs.TREE_BLOCK.format(tree=tree)]
    if arm == "BM25":
        parts.append(bugs.HITS_BLOCK.format(title="Functions a text search over the code ranks highest for the question:",
                                            hits=task["bm25_context"]))
    elif arm == "PRISM":
        parts.append(bugs.HITS_BLOCK.format(title="PRISM's search results for the question (code and digest):",
                                            hits=task["prism_context"]))
    return "\n".join(parts)


def score_task(task: dict, raw: str) -> dict:
    return bugs.score_task(task, raw)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("cmd", choices=("plan",))
    p.add_argument("out")
    p.add_argument("questions")
    p.add_argument("repos", nargs="+")
    args = p.parse_args(argv)
    plan(Path(args.out), args.questions, dict(a.split("=", 1) for a in args.repos))
    return 0


if __name__ == "__main__":
    sys.exit(main())
