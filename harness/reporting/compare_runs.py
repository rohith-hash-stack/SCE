"""Per-cell comparison of two gate runs over the same tasks and seed, e.g. the
post-fix single-arm Arm 5 baseline against the M2 run:

    python -m harness.reporting.compare_runs NEW_DIR OLD_DIR --arms arm0,arm5,oracle --out diff.json

Each run directory is what `harness.kaggle_m1` writes (cells.parquet,
bundles/). For every (arm, task) present in both runs it reports the old
and new value of tsr, answer_gold_recall, hallucination_rate and
finish_reason, and the hydrated (delivered) symbol list with whether its
set or its order changed. Arm 5 cells also carry turn1_parsed_ok; Arm 4 cells
carry turn_count, tool_fpr, digest_safety_loss and forced_answer.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd

METRICS = ("tsr", "answer_gold_recall", "hallucination_rate", "finish_reason")
#: Arm 4's own diagnostics (parquet columns), compared when both runs have them
ARM4_METRICS = ("turn_count", "tool_fpr", "digest_safety_loss")


def _value(v):
    if isinstance(v, float) and math.isnan(v):
        return None
    if hasattr(v, "item"):          # numpy scalar
        v = v.item()
    return v


def _bundle(run_dir: Path, arm: str, task_id: str, seed: int) -> dict | None:
    p = run_dir / "bundles" / f"{arm}_{task_id}_s{seed}.json"
    return json.loads(p.read_text())["bundle"] if p.exists() else None


def _delivered(bundle: dict | None) -> list[str]:
    if not bundle:
        return []
    return [s for it in sorted(bundle["items"], key=lambda it: it["rank"]) for s in it["symbols"]]


def compare(new_dir: str | Path, old_dir: str | Path, arms: list[str]) -> dict:
    new_dir, old_dir = Path(new_dir), Path(old_dir)
    new = pd.read_parquet(new_dir / "cells.parquet")
    old = pd.read_parquet(old_dir / "cells.parquet")
    key = ["arm", "task_id", "seed"]
    both = new[new.arm.isin(arms)].merge(old[old.arm.isin(arms)], on=key, suffixes=("_new", "_old"))
    cells, changed = [], []
    for _, r in both.sort_values(key).iterrows():
        seed = int(r.seed)
        nb, ob = _bundle(new_dir, r.arm, r.task_id, seed), _bundle(old_dir, r.arm, r.task_id, seed)
        nd, od = _delivered(nb), _delivered(ob)
        cell = {"arm": r.arm, "task_id": r.task_id, "seed": seed}
        for m in METRICS:
            cell[m] = {"old": _value(r[f"{m}_old"]), "new": _value(r[f"{m}_new"])}
        cell["hydrated"] = {"old": od, "new": nd, "set_changed": set(od) != set(nd),
                            "order_changed": set(od) == set(nd) and od != nd}
        if r.arm == "arm5":
            cell["turn1_parsed_ok"] = {"old": (ob or {}).get("build_meta", {}).get("turn1_parsed_ok"),
                                       "new": (nb or {}).get("build_meta", {}).get("turn1_parsed_ok")}
        compared = list(METRICS)
        if r.arm == "arm4":
            for m in ARM4_METRICS:
                if f"{m}_old" in r.index and f"{m}_new" in r.index:
                    cell[m] = {"old": _value(r[f"{m}_old"]), "new": _value(r[f"{m}_new"])}
                    compared.append(m)
            cell["forced_answer"] = {"old": (ob or {}).get("build_meta", {}).get("forced_answer"),
                                     "new": (nb or {}).get("build_meta", {}).get("forced_answer")}
            compared.append("forced_answer")
        diffs = [m for m in compared if cell[m]["old"] != cell[m]["new"]
                 and not (cell[m]["old"] is None and cell[m]["new"] is None)]
        if cell["hydrated"]["set_changed"] or cell["hydrated"]["order_changed"]:
            diffs.append("hydrated")
        cell["changed"] = diffs
        if diffs:
            changed.append(f"{r.arm}/{r.task_id}: {', '.join(diffs)}")
        cells.append(cell)
    real = both[~both.task_id.str.startswith("syn_")]          # Gate B: real tasks only, never pooled with Gate A
    mean = {a: {"old": _value(real[real.arm == a].tsr_old.mean()), "new": _value(real[real.arm == a].tsr_new.mean())}
            for a in arms if (real.arm == a).any()}
    missing = sorted(set(map(tuple, new[new.arm.isin(arms)][key].values)) ^ set(map(tuple, old[old.arm.isin(arms)][key].values)))
    return {"new_run": str(new_dir), "old_run": str(old_dir), "arms": arms, "n_cells_compared": len(cells),
            "mean_tsr_real_tasks": mean, "changed_cells": changed, "cells_in_only_one_run": [list(m) for m in missing],
            "cells": cells}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("new_dir")
    ap.add_argument("old_dir")
    ap.add_argument("--arms", default="arm0,arm5,oracle")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = compare(a.new_dir, a.old_dir, [x.strip() for x in a.arms.split(",") if x.strip()])
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    print(f"compared {out['n_cells_compared']} cells; real-task mean tsr {out['mean_tsr_real_tasks']}; "
          f"{len(out['changed_cells'])} changed: {out['changed_cells']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
