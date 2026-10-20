"""Derived statistics over the committed Layer 4 tournament cells, for the
paper foundation document (not in TOURNAMENT.md):

1. prism_full - arm1_hybrid_rag on the 80 paired forward (task, seed)
   cells: mean delta, a task-clustered bootstrap 95% CI (resample tasks,
   then seeds within a task; 10,000 resamples, seed 0), task wins, and a
   Wilcoxon signed-rank p over the 8 task means (when scipy is present).
2. A failure classification of the backward ok cells by a fixed regex rule
   over `score.failure` (the cells carry no timeout field, so Timeout is
   read from log text only):

   pass            score.passed
   syntax_error    not score.syntax_ok, or SyntaxError / IndentationError /
                   esbuild "Transform failed"
   timeout         "Timeout of Nms exceeded" / "timed out" / TimeoutExpired
   undefined_api   NameError, "ReferenceError: x is not defined", "has no
                   attribute", "is not a function", ImportError /
                   ModuleNotFoundError / "Cannot find module", "got an
                   unexpected keyword argument"
   wrong_behavior  everything else

    PYTHONPATH=.:src python scripts/layer4_paper_stats.py \
        --out reports/layer4/diagnostics/paper_stats.json
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import re
import statistics as st
from pathlib import Path

ARMS = ("arm0_no_retrieval", "repomap_pagerank", "arm1_hybrid_rag", "prism_ask", "prism_full", "normal_oracle",
        "prism_oracle")
CLASSES = ("pass", "wrong_behavior", "undefined_api", "syntax_error", "timeout")
UNDEFINED = re.compile(r"NameError: name '|ReferenceError: \S+ is not defined|has no attribute|is not a function|"
                       r"ImportError|ModuleNotFoundError|Cannot find module|got an unexpected keyword argument")
SYNTAX = re.compile(r"SyntaxError|IndentationError|Transform failed")
TIMEOUT = re.compile(r"Timeout of \d+ms exceeded|timed out|TimeoutExpired")
RESAMPLES, BOOT_SEED = 10_000, 0


def classify(score: dict) -> str:
    if score.get("passed"):
        return "pass"
    failure = score.get("failure") or ""
    if not score.get("syntax_ok") or SYNTAX.search(failure):
        return "syntax_error"
    if TIMEOUT.search(failure):
        return "timeout"
    if UNDEFINED.search(failure):
        return "undefined_api"
    return "wrong_behavior"


def paired_forward(rows: list[dict], a: str, b: str) -> dict:
    out = {}
    for metric in ("precision", "recall", "f1"):
        cells = {arm: {(r["task_id"], r["seed"]): r["score"][metric] for r in rows
                       if r["arm"] == arm and r["family"] == "forward_blast" and r["status"] == "ok"} for arm in (a, b)}
        keys = sorted(cells[a].keys() & cells[b].keys())
        by_task = collections.defaultdict(list)
        for k in keys:
            by_task[k[0]].append(cells[a][k] - cells[b][k])
        tasks = sorted(by_task)
        rnd = random.Random(BOOT_SEED)
        boots = []
        for _ in range(RESAMPLES):
            picked = [rnd.choice(tasks) for _ in tasks]
            boots.append(st.mean(v for t in picked for v in (rnd.choice(by_task[t]) for _ in by_task[t])))
        boots.sort()
        task_means = [st.mean(by_task[t]) for t in tasks]
        try:
            from scipy.stats import wilcoxon
            p = float(wilcoxon(task_means).pvalue)
        except ImportError:
            p = None
        out[metric] = {"pairs": len(keys), "delta": st.mean(cells[a][k] - cells[b][k] for k in keys),
                       "ci95": [boots[int(0.025 * RESAMPLES)], boots[int(0.975 * RESAMPLES)]],
                       "tasks_won": sum(m > 0 for m in task_means), "tasks": len(tasks), "wilcoxon_p_task_means": p}
    return out


def backward_taxonomy(rows: list[dict]) -> dict:
    table = {arm: collections.Counter() for arm in ARMS}
    for r in rows:
        if r["family"] == "backward_adaptation" and r["status"] == "ok":
            table[r["arm"]][classify(r["score"])] += 1
    return {arm: {"n": sum(c.values()), **{k: c[k] for k in CLASSES}} for arm, c in table.items()}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--cells", type=Path, default=Path("reports/layer4/tournament/cells.jsonl"))
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    rows = [json.loads(line) for line in args.cells.read_text().splitlines() if line.strip()]
    result = {"cells": str(args.cells), "paired_forward_prism_full_vs_arm1_hybrid_rag":
              paired_forward(rows, "prism_full", "arm1_hybrid_rag"), "backward_taxonomy": backward_taxonomy(rows)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
