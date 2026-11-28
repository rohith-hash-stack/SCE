"""B0M4: the M4 Arm 5 baseline of the production-routing ablation, read from
the stored M4 cells (`reports/harness_m4/<corpus>/cells.parquet`) - no
re-run. `summarize` recomputes Arm 5's mean TSR per (corpus, task type) the
way the published M4 analysis does (per-task 3-seed mean, then the mean over
tasks), plus per-seed means and the secondary context metrics;
`check_against_published` compares it with
`reports/harness_m4/analysis/summary.json`.

    python -m harness.experiments.production_routing.b0m4 [--out b0m4.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
M4 = REPO / "reports" / "harness_m4"
CORPORA = ("fastapi", "django", "express", "trpc")
MATRIX = {"T2_localization": "t2_matrix", "T5_blast_radius": "t5_matrix"}


def summarize(m4_dir: Path = M4, arm: str = "arm5") -> dict:
    import pandas as pd

    out: dict = {}
    for corpus in CORPORA:
        cells = pd.read_parquet(m4_dir / corpus / "cells.parquet")
        cells = cells[cells.arm == arm]
        for task_type, group in cells.groupby("task_type"):
            per_task = group.groupby("task_id")["tsr"].mean()
            out[f"{corpus}|{task_type}"] = {
                "mean_tsr": float(per_task.mean()), "n_tasks": int(per_task.size), "n_cells": int(len(group)),
                "per_seed": {str(int(s)): float(g.groupby("task_id")["tsr"].mean().mean()) for s, g in group.groupby("seed")},
                "uniform_cpi": float(group["uniform_cpi"].mean()), "context_recall": float(group["context_recall"].mean()),
                "context_precision": float(group["context_precision"].mean()),
            }
    return out


def check_against_published(summary: dict, published_path: Path = M4 / "analysis" / "summary.json",
                            arm: str = "arm5", tol: float = 1e-12) -> list[str]:
    """Mismatches between `summary` and the published M4 matrices (empty = identical)."""
    published = json.loads(published_path.read_text())
    problems = []
    for key, row in summary.items():
        corpus, task_type = key.split("|")
        pub = published[MATRIX[task_type]][corpus][arm]
        if abs(row["mean_tsr"] - pub["mean"]) > tol:
            problems.append(f"{key}: mean {row['mean_tsr']} != published {pub['mean']}")
        if (row["n_tasks"], row["n_cells"]) != (pub["n_tasks"], pub["n_cells"]):
            problems.append(f"{key}: n {row['n_tasks']}/{row['n_cells']} != published {pub['n_tasks']}/{pub['n_cells']}")
        for seed, value in pub["per_seed"].items():
            if abs(row["per_seed"].get(seed, float("nan")) - value) > tol:
                problems.append(f"{key}: seed {seed} {row['per_seed'].get(seed)} != published {value}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    summary = summarize()
    problems = check_against_published(summary)
    for key, row in summary.items():
        print(f"{key:32s} tsr {row['mean_tsr']:.4f}  n={row['n_tasks']} tasks / {row['n_cells']} cells  "
              f"uniform_cpi {row['uniform_cpi']:.4f}  context_recall {row['context_recall']:.4f}")
    print("matches published M4 matrices" if not problems else "MISMATCH:\n  " + "\n  ".join(problems))
    if args.out:
        Path(args.out).write_text(json.dumps({"b0m4": summary, "mismatches": problems}, indent=1))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
