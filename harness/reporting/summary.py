"""Summary tables. Rules, asserted in code:
- never a single "overall" row: every row is one (arm, task_type, corpus);
- every mean comes with its 95% CI (paired within-repo bootstrap);
- per-type sample sizes are always reported;
- no composite score column.
"""
from __future__ import annotations

from collections import defaultdict

import pandas as pd

from harness import config as C
from harness.reporting.output_schema import FORBIDDEN_FRAGMENTS
from harness.scoring import registry as R
from harness.scoring.bootstrap import bootstrap_ci

KEYS = ["arm", "task_type", "corpus"]


def retrieval_lift(arm_mean_tsr: float, arm0_mean_tsr: float) -> float:
    if arm0_mean_tsr == 0:
        return float("nan")
    return (arm_mean_tsr - arm0_mean_tsr) / arm0_mean_tsr


def retrieval_efficiency(arm_mean_tsr: float, arm0_mean_tsr: float, oracle_mean_tsr: float) -> float:
    headroom = oracle_mean_tsr - arm0_mean_tsr
    if headroom == 0:
        return float("nan")
    return (arm_mean_tsr - arm0_mean_tsr) / headroom


def per_type_sample_sizes(results_df: pd.DataFrame) -> pd.DataFrame:
    """n cells and n distinct tasks per (arm, task_type, corpus)."""
    g = results_df.groupby(KEYS, dropna=False)
    return g.agg(n_cells=("task_id", "size"), n_tasks=("task_id", "nunique")).reset_index()


def _metrics_for(task_type: str) -> list[str]:
    code = R.TASK_TYPE_CODES[task_type]
    out = []
    for name in R.bootstrappable_metrics():
        t = R.METRICS[name].get("type")
        if t is None or t == code:
            out.append(name)
    # arm-family diagnostics (arm4/prism) are reported where present
    return [m for m in out if R.METRICS[m].get("type") not in ("arm4", "prism")]


def summary_table(results, corpus_of: dict[str, str] | None = None, n_reps: int = C.BOOTSTRAP_REPS,
                  seed: int = C.BOOTSTRAP_SEED) -> pd.DataFrame:
    """One row per (arm, task_type, corpus): n, and mean/ci_low/ci_high for
    every metric applicable to the task type; plus retrieval lift and
    efficiency from mean tsr against arm0 and the oracle in the same
    (task_type, corpus)."""
    groups: dict[tuple, list] = defaultdict(list)
    for r in results:
        corpus = (corpus_of or {}).get(r.repo_id, r.repo_id)
        groups[(r.arm, r.task_type, corpus)].append(r)
    rows = []
    for (arm, tt, corpus), scores in sorted(groups.items()):
        row = {"arm": arm, "task_type": tt, "corpus": corpus,
               "n_cells": len(scores), "n_tasks": len({s.task_id for s in scores})}
        for m in _metrics_for(tt):
            mean, lo, hi = bootstrap_ci(scores, m, n_reps=n_reps, seed=seed)
            row[f"mean_{m}"], row[f"ci_low_{m}"], row[f"ci_high_{m}"] = mean, lo, hi
        rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    lifts, effs = [], []
    for _, row in df.iterrows():
        same = df[(df.task_type == row.task_type) & (df.corpus == row.corpus)]
        a0 = same[same.arm == "arm0"]["mean_tsr"]
        orc = same[same.arm == "oracle"]["mean_tsr"]
        a0v = float(a0.iloc[0]) if len(a0) else float("nan")
        orv = float(orc.iloc[0]) if len(orc) else float("nan")
        lifts.append(retrieval_lift(row.mean_tsr, a0v) if a0v == a0v else float("nan"))
        effs.append(retrieval_efficiency(row.mean_tsr, a0v, orv) if (a0v == a0v and orv == orv) else float("nan"))
    df["retrieval_lift"] = lifts
    df["retrieval_efficiency"] = effs
    df["retrieval_lift_any_gold"], df["retrieval_lift_note"] = _t2_secondary_lift(df)
    assert_reporting_rules(df)
    return df


T2_LIFT_NOTE = (
    "N/A: Arm 0's strict T2 tsr is 0 (every gold symbol must be named, and Arm 0 has no context), so "
    "Retrieval Lift has a zero denominator. See retrieval_lift_any_gold, computed on answer_names_any_gold. "
    "Caution: in every real T2 task the seed symbol is gold and is named in the prompt, so answer_names_any_gold "
    "is easily 1.0 for every arm and this secondary lift is often 0."
)


def _t2_secondary_lift(df: pd.DataFrame) -> tuple[list[float], list[str]]:
    """T2 only: lift computed on answer_names_any_gold, plus a note on rows
    whose primary (strict) lift is N/A."""
    lifts, notes = [], []
    col = "mean_answer_names_any_gold"
    for _, row in df.iterrows():
        if row.task_type != "T2_localization" or col not in df.columns:
            lifts.append(float("nan"))
            notes.append("")
            continue
        same = df[(df.task_type == row.task_type) & (df.corpus == row.corpus) & (df.arm == "arm0")][col]
        a0 = float(same.iloc[0]) if len(same) else float("nan")
        lifts.append(retrieval_lift(float(row[col]), a0) if a0 == a0 else float("nan"))
        primary = row.retrieval_lift
        notes.append(T2_LIFT_NOTE if (primary != primary) else "")
    return lifts, notes


def assert_reporting_rules(df: pd.DataFrame) -> None:
    for k in KEYS:
        if k not in df.columns or df[k].isna().any():
            raise AssertionError(f"every summary row must be stratified by {KEYS}")
    for v in df["arm"].astype(str).tolist() + df["task_type"].astype(str).tolist() + df["corpus"].astype(str).tolist():
        if v.lower() in ("all", "overall", "*", "total"):
            raise AssertionError("an overall/aggregate row is not allowed")
    bad = [c for c in df.columns if any(f in c.lower() for f in FORBIDDEN_FRAGMENTS)]
    if bad:
        raise AssertionError(f"composite columns not allowed: {bad}")
    for c in df.columns:
        if c.startswith("mean_"):
            m = c[len("mean_"):]
            if f"ci_low_{m}" not in df.columns or f"ci_high_{m}" not in df.columns:
                raise AssertionError(f"{c} reported without its CI")
    if "n_tasks" not in df.columns:
        raise AssertionError("per-type sample sizes must be reported")
