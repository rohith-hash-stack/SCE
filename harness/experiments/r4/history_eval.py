"""POC 2 amendment 1's end-state measurement, a separate evaluation process after the run, the same for every arm.

    python -B -m harness.experiments.r4.history_eval TASK.json CLONE START_TREE FINAL_TREE

`runner.run_task` calls it (as a child process) for deletion tasks in every POC 2 arm, after the agent's run has ended
and before the scorer changes the tree. It is given only git tree ids:
- `START_TREE`: the run's start state;
- `FINAL_TREE`: the agent's working tree when the run ended.

Both are objects in the clone's store, so nothing here touches the run's files or its process. It:
1. extracts both trees into temporary folders;
2. builds PRISM's index of the start tree (`build_pipeline`, the same build the PRISM arms' snapshot holds);
3. finds the task's target (`must_be_absent`, read only here, after the run) and its start-state places;
4. gives each place's state in the final tree (`prism.history.HistoricalGraph.status`: gone, still uses the name,
   confirmed broken).

It prints one JSON object (its own costs included). Nothing it computes reaches any agent: the run is over, and the
record goes only to the run's score file. BASELINE_LSP's run never constructs or holds a snapshot
(`runner.HISTORY_ARMS`); this measurement is the only place its end state is read against PRISM's start index, and it
runs in its own process after that run.
"""
from __future__ import annotations

import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time


def _extract(clone: str, tree: str, dest: str) -> None:
    os.makedirs(dest, exist_ok=True)
    archive = subprocess.run(["git", "-C", clone, "archive", "--format=tar", tree], capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", dest], input=archive.stdout, check=True)


def target_places(snap, task: dict) -> tuple[str, dict] | None:
    """The task's target in the start index, and its start-state places {(rel, line)}: the definition, every recorded
    call or instantiation site, and every reference-graph line (what `prism_find_references` lists)."""
    from prism.graph.references import references_to
    absent = task.get("must_be_absent")
    if not absent or "::" not in absent:
        return None
    f, name = absent.split("::", 1)
    try:
        q = snap.resolve(f"{f.removesuffix('.py').replace('/', '.')}.{name}")
    except Exception:
        return None
    b = snap.builder
    rel = lambda p: os.path.relpath(p, snap.root) if p and os.path.isabs(p) else p
    places = {(rel(b.symbol_table.get(q).file), snap.def_line(q))}
    for src, _d, e in b.graph.in_edges(q, data=True):
        if e.get("relation") in ("CALLS", "INSTANTIATES") and (ss := b.symbol_table.get(src)) is not None:
            places |= {(rel(ss.file), ln) for ln, _c in (e.get("sites") or [(ss.line_range[0], 0)])}
    for r in references_to(b, q):
        places |= {(rel(r["file"]), ln) for ln in r["lines"]}
    return q, {"file": rel(b.symbol_table.get(q).file), "leaf": name, "places": sorted(places)}


def evaluate(task: dict, clone: str, start_tree: str, final_tree: str) -> dict:
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
    from prism.cli import LANGUAGE_TIER_PERMISSIVE, build_pipeline
    from prism.history import HistoricalGraph
    t0 = time.time()
    tmp = tempfile.mkdtemp(prefix="histeval_")
    try:
        start, final = os.path.join(tmp, "start"), os.path.join(tmp, "final")
        _extract(clone, start_tree, start)
        _extract(clone, final_tree, final)
        t1 = time.time()
        builder, _tags = build_pipeline(start, LANGUAGE_TIER_PERMISSIVE, use_cache=False)    # as WorkingIndex builds
        t2 = time.time()
        snap = HistoricalGraph(builder, start)
        out = {"start_tree": start_tree, "final_tree": final_tree, "end_state": {}}
        tp = target_places(snap, task)
        if tp is not None:
            q, t = tp
            st = snap.status(t["places"], t["leaf"], t["file"], final)
            counts = {k: sum(1 for v in st.values() if v["status"] == k) for k in ("gone", "still_references",
                                                                                 "confirmed_broken")}
            out["end_state"][q] = {"places": len(st), **counts,
                                   "unresolved": sorted(f"{f}:{ln}" for (f, ln), v in st.items()
                                                        if v["status"] != "gone")}
        else:
            out["error"] = "the task's target is not a definition of the start index"
        out["cost"] = {"extract_s": round(t1 - t0, 2), "index_s": round(t2 - t1, 2),
                       "total_s": round(time.time() - t0, 2),
                       "max_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024}
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run(task: dict, clone: str, start_tree: str, final_tree: str, timeout: int = 1800) -> dict:
    """`evaluate` in a child process (its memory and time never count against the run's own process)."""
    fd, path = tempfile.mkstemp(prefix="histeval_task_", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(task, fh)
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([root, os.path.join(root, "src"),
                                                           os.environ.get("PYTHONPATH", "")]))
        r = subprocess.run([sys.executable, "-B", "-m", "harness.experiments.r4.history_eval", path, clone, start_tree,
                            final_tree], capture_output=True, text=True, timeout=timeout, env=env, cwd=root)
        if r.returncode != 0:
            return {"error": (r.stderr or r.stdout)[-1500:]}
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as exc:                      # secondary: recorded, never fatal to the run
        return {"error": f"{type(exc).__name__}: {exc}"[:1500]}
    finally:
        os.remove(path)


def main(argv=None) -> int:
    a = list(argv if argv is not None else sys.argv[1:])
    if len(a) != 4:
        raise SystemExit(__doc__)
    print(json.dumps(evaluate(json.load(open(a[0])), a[1], a[2], a[3])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
