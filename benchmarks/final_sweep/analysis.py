"""Cross-corpus analysis of final-sweep results with task-clustered
uncertainty.

The per-run `summary.py` intervals resample individual cells, which
treats the seeds of one task as independent draws - they aren't (same
task, same context), so those intervals are too narrow. Here every
interval comes from a stratified cluster bootstrap: resample *tasks*
with replacement within each corpus, keep all of a resampled task's
seeds together, recompute the statistic.

    python -m benchmarks.final_sweep.analysis reports/final_sweep/slm_qwen7b
    python -m benchmarks.final_sweep.analysis reports/final_sweep/slm_qwen7b --out reports/final_sweep/slm_qwen7b/analysis.md
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

from benchmarks.final_sweep import config as C
from benchmarks.final_sweep.runner import load_records
from benchmarks.final_sweep.summary import PAIRED_CONTRASTS

BOOT_ITERS = 4000


def load_root(root: Path) -> list[dict]:
    """Every ok, scored cell under `root/<repo>/cells.jsonl`."""
    rows = []
    for path in sorted(root.glob("*/cells.jsonl")):
        rows += [r for r in load_records(path).values() if r["status"] == "ok" and r["tsr"] is not None]
    return rows


def _task_means(rows: list[dict], field: str) -> dict[tuple[str, str], dict[str, float]]:
    """`{(repo, task): {arm: mean of field over seeds}}`."""
    acc: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        acc[(r["repo"], r["task_id"])][r["engine_id"]].append(float(r[field]))
    return {k: {a: sum(v) / len(v) for a, v in arms.items()} for k, arms in acc.items()}


def _cluster_boot(tasks_by_repo: dict[str, list], stat, iters: int = BOOT_ITERS, seed: int = 0) -> tuple[float, float] | None:
    """95% percentile interval of `stat(list_of_task_keys)` under a
    stratified (by corpus) cluster bootstrap over tasks."""
    rng = random.Random(seed)
    values = []
    for _ in range(iters):
        sample = [rng.choice(keys) for keys in tasks_by_repo.values() for _ in keys]
        v = stat(sample)
        if v is not None:
            values.append(v)
    if len(values) < iters // 2:
        return None
    values.sort()
    return values[int(0.025 * len(values))], values[int(0.975 * len(values)) - 1]


def analyze(rows: list[dict]) -> dict:
    repos = sorted({r["repo"] for r in rows})
    arms = [a for a in C.ARM_ORDER if any(r["engine_id"] == a for r in rows)]
    fields = ("tsr", "cleanliness", "sufficiency_ratio", "context_tokens")
    means = {f: _task_means(rows, f) for f in fields}
    tasks_by_repo: dict[str, list] = defaultdict(list)
    for key in means["tsr"]:
        tasks_by_repo[key[0]].append(key)

    def arm_mean(field, arm, keys):
        vals = [means[field][k][arm] for k in keys if arm in means[field][k]]
        return sum(vals) / len(vals) if vals else None

    all_keys = [k for keys in tasks_by_repo.values() for k in keys]
    per_arm = {}
    for arm in arms:
        per_arm[arm] = {
            "n_cells": sum(1 for r in rows if r["engine_id"] == arm),
            "tsr": arm_mean("tsr", arm, all_keys),
            "tsr_ci95": _cluster_boot(tasks_by_repo, lambda ks, a=arm: arm_mean("tsr", a, ks)),
            "cleanliness": arm_mean("cleanliness", arm, all_keys),
            "sufficiency_ratio": arm_mean("sufficiency_ratio", arm, all_keys),
            "context_tokens": arm_mean("context_tokens", arm, all_keys),
            "per_repo_tsr": {repo: arm_mean("tsr", arm, tasks_by_repo[repo]) for repo in repos},
        }

    def delta(a, b, keys, field="tsr"):
        vals = [means[field][k][a] - means[field][k][b] for k in keys if a in means[field][k] and b in means[field][k]]
        return sum(vals) / len(vals) if vals else None

    contrasts = []
    for a, b, label in PAIRED_CONTRASTS:
        if a not in arms or b not in arms:
            continue
        ci = _cluster_boot(tasks_by_repo, lambda ks, a=a, b=b: delta(a, b, ks))
        contrasts.append({
            "a": a, "b": b, "label": label,
            "delta_tsr": delta(a, b, all_keys), "delta_tsr_ci95": ci,
            "excludes_zero": bool(ci and (ci[0] > 0 or ci[1] < 0)),
            "delta_cleanliness": delta(a, b, all_keys, "cleanliness"),
            "per_repo_delta_tsr": {repo: delta(a, b, tasks_by_repo[repo]) for repo in repos},
        })
    seeds = sorted({r["seed"] for r in rows})
    return {
        "n_cells": len(rows), "repos": repos, "n_tasks": len(all_keys), "seeds": seeds,
        "models": sorted({r["model_requested"] for r in rows}),
        "arms": per_arm, "contrasts": contrasts,
    }


def _f(v, d=3):
    return "n/a" if v is None else f"{v:.{d}f}"


def render(a: dict) -> str:
    repos = a["repos"]
    lines = [
        f"# Final sweep analysis: {', '.join(a['models'])}",
        "",
        f"{a['n_cells']:,} scored cells; {a['n_tasks']} tasks across {', '.join(repos)}; seeds {a['seeds']}. "
        "Intervals: 95% stratified cluster bootstrap over tasks (seeds of a task stay together), "
        f"{BOOT_ITERS:,} resamples. Pooled means weight every task equally.",
        "",
        "| arm | cells | TSR (95% CI) | " + " | ".join(repos) + " | cleanliness | sufficiency | ctx tokens |",
        "|---|---|---|" + "---|" * len(repos) + "---|---|---|",
    ]
    for arm, s in a["arms"].items():
        ci = s["tsr_ci95"]
        tsr = f"{_f(s['tsr'])} [{ci[0]:.3f}, {ci[1]:.3f}]" if ci else _f(s["tsr"])
        per = " | ".join(_f(s["per_repo_tsr"][r]) for r in repos)
        lines.append(f"| `{arm}` | {s['n_cells']} | {tsr} | {per} | {_f(s['cleanliness'])} | "
                     f"{_f(s['sufficiency_ratio'])} | {_f(s['context_tokens'], 0)} |")
    lines += ["", "Paired contrasts (A - B, same task and seed):", "",
              "| A | B | isolates | ΔTSR (95% CI) | " + " | ".join(repos) + " | Δcleanliness |",
              "|---|---|---|---|" + "---|" * len(repos) + "---|"]
    for c in a["contrasts"]:
        ci = c["delta_tsr_ci95"]
        mark = " *" if c["excludes_zero"] else ""
        d = f"{c['delta_tsr']:+.3f} [{ci[0]:+.3f}, {ci[1]:+.3f}]{mark}" if ci else f"{c['delta_tsr']:+.3f}"
        per = " | ".join(f"{v:+.3f}" if v is not None else "n/a" for v in (c["per_repo_delta_tsr"][r] for r in repos))
        lines.append(f"| `{c['a']}` | `{c['b']}` | {c['label']} | {d} | {per} | {c['delta_cleanliness']:+.3f} |")
    lines += ["", "`*` = interval excludes zero."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m benchmarks.final_sweep.analysis")
    p.add_argument("root", help="Directory holding <repo>/cells.jsonl for one model")
    p.add_argument("--out", default=None, help="Also write the markdown here (and .json alongside)")
    args = p.parse_args(argv)
    rows = load_root(Path(args.root))
    if not rows:
        print(f"no scored cells under {args.root}", file=sys.stderr)
        return 1
    result = analyze(rows)
    md = render(result)
    print(md)
    if args.out:
        out = Path(args.out)
        out.write_text(md)
        out.with_suffix(".json").write_text(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
