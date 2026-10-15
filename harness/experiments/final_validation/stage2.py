"""Stage 2 of the two-stage validation, pooled over the four final-sweep
corpora: the causal-trace success rate (TSR, `score_debug_causal`) of each
arm on the 90 debug tasks, and paired contrasts tested with tasks as the
unit (seeds are averaged within a task first, so 10 seeds of one task
never count as 10 independent observations).

Cells come from `benchmarks.final_sweep.runner` (one `cells.jsonl` per
corpus); the arms are `benchmarks.final_sweep.config.STAGE2_ARMS`.
"""
from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

from harness.experiments.final_validation.stage1 import sign_test_p

#: (a, b, label) - display names (keys of STAGE2_ARMS).
CONTRASTS = (
    ("prism_full", "arm1_hybrid_rag", "PRISM two-pass vs hybrid RAG"),
    ("prism_ask", "arm1_hybrid_rag", "PRISM ask payload vs hybrid RAG"),
    ("prism_oracle", "normal_oracle", "entry breadcrumbs over the gold slice"),
    ("arm1_hybrid_rag", "arm0_no_retrieval", "hybrid RAG vs no retrieval"),
    ("repomap_pagerank", "arm1_hybrid_rag", "PageRank repo-map vs hybrid RAG"),
    ("prism_full", "repomap_pagerank", "PRISM two-pass vs PageRank repo-map"),
)
BOOT_ITERS = 4000


def load_cells(out_root: Path, repos) -> list[dict]:
    rows = []
    for repo in repos:
        path = Path(out_root) / repo / "cells.jsonl"
        if path.exists():
            rows += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return rows


def task_means(rows: list[dict], engine: str) -> dict[tuple[str, str], float]:
    """`{(repo, task): mean TSR over that task's scored seeds}`."""
    acc: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        if r["engine_id"] == engine and r["status"] == "ok" and r.get("tsr") is not None:
            acc[(r["repo"], r["task_id"])].append(float(r["tsr"]))
    return {k: statistics.fmean(v) for k, v in acc.items()}


def cluster_boot(by_repo: dict[str, list[float]], iters: int = BOOT_ITERS, seed: int = 0) -> tuple[float, float] | None:
    """95% interval of the mean of per-task values, resampling tasks within corpus."""
    pools = [v for v in by_repo.values() if v]
    if sum(len(v) for v in pools) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(statistics.fmean([rng.choice(v) for v in pools for _ in v]) for _ in range(iters))
    return means[int(0.025 * iters)], means[int(0.975 * iters) - 1]


def analyse(rows: list[dict], arms: dict[str, str]) -> dict:
    """`arms`: `{display name: engine_id}`."""
    out = {"arms": {}, "contrasts": []}
    means = {name: task_means(rows, engine) for name, engine in arms.items()}
    for name, engine in arms.items():
        cells = [r for r in rows if r["engine_id"] == engine]
        scored = [r for r in cells if r["status"] == "ok" and r.get("tsr") is not None]
        by_repo: dict[str, list[float]] = defaultdict(list)
        for (repo, _), v in means[name].items():
            by_repo[repo].append(v)
        out["arms"][name] = {
            "engine_id": engine, "n_cells": len(cells), "n_scored": len(scored), "n_tasks": len(means[name]),
            "tsr": statistics.fmean(r["tsr"] for r in scored) if scored else None,
            "tsr_ci95": cluster_boot(by_repo),
            "per_repo": {repo: statistics.fmean(v) for repo, v in sorted(by_repo.items())},
            "mean_context_tokens": statistics.fmean(r["context_tokens"] for r in scored if r.get("context_tokens") is not None)
            if any(r.get("context_tokens") is not None for r in scored) else None,
        }
    for a, b, label in CONTRASTS:
        if a not in means or b not in means:
            continue
        shared = sorted(set(means[a]) & set(means[b]))
        if not shared:
            continue
        by_repo: dict[str, list[float]] = defaultdict(list)
        for k in shared:
            by_repo[k[0]].append(means[a][k] - means[b][k])
        deltas = [d for v in by_repo.values() for d in v]
        wins = sum(1 for d in deltas if d > 0)
        losses = sum(1 for d in deltas if d < 0)
        out["contrasts"].append({
            "a": a, "b": b, "label": label, "n_tasks": len(shared), "delta_tsr": statistics.fmean(deltas),
            "ci95": cluster_boot(by_repo), "task_wins": wins, "task_losses": losses,
            "sign_p": sign_test_p(wins, losses),
        })
    return out


def render_stage2_markdown(result: dict, model: str) -> str:
    lines = ["## Stage 2 - causal-trace success rate on the 90 debug tasks", "",
             f"Model `{model}`. TSR here is the **causal-trace success rate** (`score_debug_causal`: the answer "
             "names the root cause along the gold causal chain), not a test-suite pass rate. 95% CIs: bootstrap over "
             "tasks, stratified by corpus, seeds averaged within task. `normal_oracle` (the gold slice) is a sanity "
             "reference.", "",
             "| arm | TSR | 95% CI | tasks | scored cells | mean context tokens | per corpus |",
             "|---|---|---|---|---|---|---|"]
    for name, a in result["arms"].items():
        tsr = "-" if a["tsr"] is None else f"{a['tsr']:.3f}"
        ci = f"[{a['tsr_ci95'][0]:.3f}, {a['tsr_ci95'][1]:.3f}]" if a["tsr_ci95"] else "-"
        ctx = "-" if a["mean_context_tokens"] is None else f"{a['mean_context_tokens']:.0f}"
        per = ", ".join(f"{k} {v:.2f}" for k, v in a["per_repo"].items()) or "-"
        lines.append(f"| `{name}` | {tsr} | {ci} | {a['n_tasks']} | {a['n_scored']}/{a['n_cells']} | {ctx} | {per} |")
    if result["contrasts"]:
        lines += ["", "| a | b | contrast | tasks | Δ TSR | 95% CI | task wins-losses | sign-test p |",
                  "|---|---|---|---|---|---|---|---|"]
        for c in result["contrasts"]:
            ci = f"[{c['ci95'][0]:+.3f}, {c['ci95'][1]:+.3f}]" if c["ci95"] else "-"
            lines.append(f"| `{c['a']}` | `{c['b']}` | {c['label']} | {c['n_tasks']} | {c['delta_tsr']:+.3f} | {ci} | "
                         f"{c['task_wins']}-{c['task_losses']} | {c['sign_p']:.3f} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    import argparse

    from benchmarks.final_sweep.config import FINAL_SWEEP_REPOS, STAGE2_ARMS
    p = argparse.ArgumentParser(description="Pool the Stage 2 cells and render the TSR table.")
    p.add_argument("--out-root", required=True, help="<root>/<repo>/cells.jsonl from benchmarks.final_sweep.runner")
    p.add_argument("--model", required=True)
    args = p.parse_args(argv)
    result = analyse(load_cells(Path(args.out_root), FINAL_SWEEP_REPOS), dict(STAGE2_ARMS))
    (Path(args.out_root) / "stage2_results.json").write_text(json.dumps(result, indent=1))
    md = render_stage2_markdown(result, args.model)
    (Path(args.out_root) / "stage2.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
