"""Static call-graph coverage of the Layer 4 runtime traces: for each of the
16 tasks' trace manifests (the focal function's callers and callees seen
under the corpus's own tests, with their call depth), how much of it the
static index reaches.

    PYTHONPATH=.:src python scripts/layer4_static_coverage.py --out reports/layer4/diagnostics/static_coverage.json

Per task and corpus:

- **direct callers**: runtime depth-1 ancestors with a static CALLS /
  INSTANTIATES edge to the focal (edge recall at hop 1);
- **callers within depth**: runtime ancestors within the task's depth
  (forward: `gold.max_depth`; backward: 2) reached by the static upstream
  walk within that many hops (`_upstream_walk`, tests excluded) - the
  blast-radius walk's own reach;
- **callees within depth**: runtime descendants within the same depth
  reached downstream over CALLS / INSTANTIATES edges.

Only names the index knows (application functions/methods) are counted:
the rest cannot be reached by any static method. Deterministic; no model.
"""
from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path

from harness.experiments.layer4 import context as X
from harness.experiments.layer4 import runner as R

RELATIONS = ("CALLS", "INSTANTIATES")


def downstream(graph, seed: str, hops: int) -> dict[str, int]:
    seen, queue = {seed: 0}, deque([seed])
    while queue:
        node = queue.popleft()
        if seen[node] >= hops or node not in graph:
            continue
        for succ in graph.successors(node):
            if succ not in seen and (graph.get_edge_data(node, succ) or {}).get("relation") in RELATIONS:
                seen[succ] = seen[node] + 1
                queue.append(succ)
    seen.pop(seed)
    return seen


def task_coverage(corpus, task: dict) -> dict:
    from prism.packer.candidate_index import _upstream_walk
    b = corpus.state.builder
    g = b.graph
    focal = task["focal"]["qname"]
    depth = task["gold"].get("max_depth") or 2
    man = json.load(open(task["gold"].get("trace_manifest") or f"benchmarks/layer4/manifests/{task['task_id']}.json"))
    known = corpus.known
    anc = {q: d for q, d in man.get("ancestor_depth", {}).items() if q in known and d <= depth and q != focal}
    desc = {q: d for q, d in man.get("descendant_depth", {}).items() if q in known and d <= depth and q != focal}
    direct = {q for q, d in anc.items() if d == 1}
    linked = {p for p in g.predecessors(focal) if (g.get_edge_data(p, focal) or {}).get("relation") in RELATIONS} \
        if focal in g else set()
    up = _upstream_walk(b, focal, max_hops=depth) if focal in b.symbol_table else {}
    down = downstream(g, focal, depth)
    return {
        "task_id": task["task_id"], "corpus": task["corpus"], "focal": focal, "depth": depth,
        "direct_callers": len(direct), "direct_callers_linked": len(direct & linked),
        "callers": len(anc), "callers_reached": len(set(anc) & set(up)),
        "callees": len(desc), "callees_reached": len(set(desc) & set(down)),
        "callers_missed": sorted(set(anc) - set(up)), "callees_missed": sorted(set(desc) - set(down)),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--corpora", default="fastapi,django,express,trpc")
    args = p.parse_args(argv)
    tasks = R.load_tasks(R.TASKS_DIR)
    rows = []
    for name in args.corpora.split(","):
        corpus = X.Corpus.load(name)
        for tid in sorted(t for t, v in tasks.items() if v["corpus"] == name):
            rows.append(task_coverage(corpus, tasks[tid]))
            r = rows[-1]
            print(f"{tid:20} direct {r['direct_callers_linked']}/{r['direct_callers']}  callers {r['callers_reached']}/"
                  f"{r['callers']}  callees {r['callees_reached']}/{r['callees']}", flush=True)
        del corpus
    tot = {k: sum(r[k] for r in rows) for k in ("direct_callers", "direct_callers_linked", "callers", "callers_reached",
                                                   "callees", "callees_reached")}
    print(f"TOTAL direct-caller edge recall {tot['direct_callers_linked']}/{tot['direct_callers']}, callers reached "
          f"{tot['callers_reached']}/{tot['callers']}, callees reached {tot['callees_reached']}/{tot['callees']}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"total": tot, "tasks": rows}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
