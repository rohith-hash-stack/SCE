"""Control metric for the TypeScript T5 gold bias (M4).

TypeScript T5 gold comes from typescript-language-server's references, the
server Arm 3 retrieves with, so Arm 3 may have privileged knowledge of it.
This measures whether that bias shows: per arm, the overlap of the
delivered (retrieved) context with the gold affected set on TypeScript T5
cells. The overlap is the scorer's `uniform_cpi` column, |delivered ∩ gold|
/ |gold|, computed the same way for every arm.

    python -m harness.reporting.t5_bias_control RUN_DIR [--out bias.json]

`arm3_ratio` is Arm 3's mean overlap divided by the mean of the other
retrieval arms (Arms 1, 2, 4, 5; Arm 0 retrieves nothing and the Oracle is
the ceiling, so both are left out of the comparison). Only a ratio well
above 1 makes the bias measurable; around 1 it stays theoretical.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd

from harness import config as C

TS_CORPORA = sorted(c for c, lang in C.CORPUS_LANGUAGE.items() if lang == "typescript")
#: arms whose overlap Arm 3's is compared against
COMPARISON_ARMS = ("arm1", "arm2", "arm4", "arm5")


def _mean(xs) -> float | None:
    xs = [float(x) for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return sum(xs) / len(xs) if xs else None


def bias_control(cells: pd.DataFrame) -> dict:
    """Per-arm gold overlap on TypeScript T5 cells, overall and per corpus."""
    ts = cells[(cells.task_type == "T5_blast_radius") & (cells.repo_id.isin(TS_CORPORA))]
    out: dict = {"n_cells": int(len(ts)), "n_tasks": int(ts.task_id.nunique()), "by_arm": {}, "by_corpus": {}}
    for arm, g in ts.groupby("arm"):
        out["by_arm"][arm] = {"mean_overlap": _mean(g.uniform_cpi), "n": int(len(g)),
                              "cells_with_any_overlap": int((g.uniform_cpi > 0).sum())}
    for corpus, g in ts.groupby("repo_id"):
        out["by_corpus"][corpus] = {arm: _mean(h.uniform_cpi) for arm, h in g.groupby("arm")}
    arm3 = out["by_arm"].get("arm3", {}).get("mean_overlap")
    others = _mean(out["by_arm"][a]["mean_overlap"] for a in COMPARISON_ARMS if a in out["by_arm"])
    out["arm3_mean_overlap"] = arm3
    out["comparison_arms_mean_overlap"] = others
    out["arm3_ratio"] = (arm3 / others) if (arm3 is not None and others) else None
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    res = bias_control(pd.read_parquet(Path(a.run_dir) / "cells.parquet"))
    text = json.dumps(res, indent=1)
    if a.out:
        Path(a.out).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
