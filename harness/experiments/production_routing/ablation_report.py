"""R1 vs R0 T5 ablation report (with B0M4 from the stored M4 cells).

Input layout, one directory per config, mirroring `reports/harness_m4`:
    <config_root>/<corpus>/cells.parquet
    <config_root>/<corpus>/bundles/arm5_<task_id>_s<seed>.json
B0M4 is read from `reports/harness_m4` itself.

Per cell (Arm 5, T5 only): config, corpus, seed, task_id, TSR, gold coverage
(gold symbols delivered / gold, seed excluded), selection precision and recall
(Turn-1 picks that resolved against the manifest - requested names minus the
ones Turn 2 skipped - vs. gold; recall is over all gold), turn1_source
("rule" when the R0 rule made the Turn-1 selection, else "llm"), plus
diagnostics for the anomaly scan. Gold is read here, at analysis time only.

Statistics follow the M4 analysis exactly: per-task 3-seed mean TSR; 95% CI
from a 10,000-resample cluster bootstrap over tasks, one resample matrix per
corpus seeded by [20261007, crc32(sorted task ids)] and shared by every config
(paired); two-sided paired bootstrap p; Holm. Primary family: R0 vs R1 across
the four corpora. Secondary family: B0M4 vs R1 and B0M4 vs R0 (8 tests).

    python -m harness.experiments.production_routing.ablation_report \\
        --config R1=<dir> --config R0=<dir> --out <report_dir>
"""
from __future__ import annotations

import argparse
import json
import math
import zlib
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
M4 = REPO / "reports" / "harness_m4"
CORPORA = ("fastapi", "django", "express", "trpc")
T5 = "T5_blast_radius"
SEEDS = (42, 43, 44)
B = 10_000
RNG_SEED = 20261007
ALPHA = 0.05
ANECDOTAL = {"express"}            # n = 2 T5 tasks


def _gold(corpus: str) -> dict[str, tuple[str, set[str]]]:
    from benchmarks.corpora.resolver import resolve
    from harness.tasks.loaders import load_tasks
    return {t.task_id: (t.seed_symbol, set(t.ground_truth.pipeline_symbols))
            for t in load_tasks(corpus, task_types=[T5], repo_root=str(resolve(corpus)))}


def cell_metrics(bundle: dict, seed_symbol: str, gold: set[str]) -> dict:
    """Gold coverage, selection precision/recall and turn1_source for one cell."""
    meta = bundle["build_meta"]
    target = gold - {seed_symbol}
    delivered = {s for it in bundle["items"] for s in (it.get("symbols") or [])}
    picks = set(meta.get("requested_symbols") or []) - set(meta.get("skipped_hallucinated") or []) - {seed_symbol}
    turns = meta.get("retrieval_turns") or []
    source = meta.get("turn1_source") or ("rule" if turns and turns[0].get("model") == "rule_callers" else "llm")
    return {"gold_coverage": len(target & delivered) / len(target) if target else math.nan,
            "selection_precision": len(target & picks) / len(picks) if picks else math.nan,
            "selection_recall": len(target & picks) / len(target) if target else math.nan,
            "turn1_source": source, "n_picks": len(picks), "n_gold": len(target),
            "manifest_candidates": meta.get("manifest_candidates"), "turn1_parsed_ok": meta.get("turn1_parsed_ok"),
            "turn1_degenerate": meta.get("turn1_degenerate"), "over_budget": meta.get("over_budget")}


def load_config(name: str, root: Path, gold: dict) -> list[dict]:
    import pandas as pd
    rows = []
    for corpus in CORPORA:
        path = root / corpus / "cells.parquet"
        if not path.exists():
            continue
        cells = pd.read_parquet(path)
        cells = cells[(cells.arm == "arm5") & (cells.task_type == T5)]
        for r in cells.itertuples():
            row = {"config": name, "corpus": corpus, "seed": int(r.seed), "task_id": r.task_id, "tsr": float(r.tsr),
                   "extraction_success": bool(r.extraction_success)}
            bpath = root / corpus / "bundles" / f"arm5_{r.task_id}_s{int(r.seed)}.json"
            if bpath.exists() and r.task_id in gold[corpus]:
                seed_symbol, g = gold[corpus][r.task_id]
                row.update(cell_metrics(json.loads(bpath.read_text())["bundle"], seed_symbol, g))
            else:
                row["missing_bundle"] = True
            rows.append(row)
    return rows


def _holm(ps: list[float]) -> list[float]:
    order = np.argsort(ps, kind="stable")
    adj, running = [0.0] * len(ps), 0.0
    for k, i in enumerate(order):
        running = max(running, min(1.0, (len(ps) - k) * ps[i]))
        adj[i] = running
    return adj


def analyse(rows: list[dict], configs: list[str]) -> dict:
    import pandas as pd
    df = pd.DataFrame(rows)
    matrix, pairs = {}, {"primary": [], "secondary": []}
    for corpus in CORPORA:
        sub = df[df.corpus == corpus]
        if sub.empty:
            continue
        piv = sub.pivot_table(index="task_id", columns="config", values="tsr", aggfunc="mean").sort_index()
        present = [c for c in configs if c in piv.columns]
        common = piv[present].dropna()
        tasks = list(common.index)
        rng = np.random.default_rng([RNG_SEED, zlib.crc32("|".join(tasks).encode())])
        idx = rng.integers(0, len(tasks), size=(B, len(tasks)))
        boots = {c: common[c].to_numpy()[idx].mean(axis=1) for c in present}
        matrix[corpus] = {}
        for c in present:
            lo, hi = np.percentile(boots[c], [2.5, 97.5])
            x = sub[sub.config == c]
            matrix[corpus][c] = {"mean_tsr": float(common[c].mean()), "ci_low": float(lo), "ci_high": float(hi),
                                 "n_tasks": len(tasks), "n_cells": int(len(x)),
                                 "per_seed": {str(s): float(x[x.seed == s].groupby("task_id").tsr.mean().mean()) for s in SEEDS if (x.seed == s).any()},
                                 "gold_coverage": float(x.gold_coverage.mean()) if "gold_coverage" in x else math.nan,
                                 "selection_precision": float(x.selection_precision.mean()) if "selection_precision" in x else math.nan,
                                 "selection_recall": float(x.selection_recall.mean()) if "selection_recall" in x else math.nan}
        for a, b, fam in (("R0", "R1", "primary"), ("R1", "B0M4", "secondary"), ("R0", "B0M4", "secondary")):
            if a in boots and b in boots:
                d = boots[a] - boots[b]
                p = 1.0 if np.all(d == 0) else min(1.0, 2 * min(np.mean(d <= 0), np.mean(d >= 0)))
                lo, hi = np.percentile(d, [2.5, 97.5])
                pairs[fam].append({"corpus": corpus, "a": a, "b": b, "delta": float(common[a].mean() - common[b].mean()),
                                   "ci_low": float(lo), "ci_high": float(hi), "p_raw": float(p), "n_tasks": len(tasks),
                                   "anecdotal": corpus in ANECDOTAL})
    for fam in pairs.values():
        for r, ph in zip(fam, _holm([r["p_raw"] for r in fam])):
            r["p_holm"] = float(ph)
            r["significant"] = bool(ph < ALPHA and (r["ci_low"] > 0 or r["ci_high"] < 0))
    return {"matrix": matrix, "pairwise": pairs, "anomalies": anomalies(df, configs)}


def anomalies(df, configs: list[str]) -> list[str]:
    out = []
    for c in configs:
        x = df[df.config == c]
        if len(x) != 96:
            out.append(f"{c}: {len(x)} T5 cells (expected 96)")
        for col, bad in (("missing_bundle", x.get("missing_bundle", False) == True),          # noqa: E712
                         ("tsr is NaN", x.tsr.isna()), ("extraction failed", ~x.extraction_success.astype(bool))):
            n = int(np.sum(bad)) if hasattr(bad, "__len__") else 0
            if n:
                out.append(f"{c}: {n} cells with {col}")
        if "turn1_source" in x:
            want = "rule" if c == "R0" else "llm"
            n = int((x.turn1_source != want).sum())
            if n:
                out.append(f"{c}: {n} cells with turn1_source != {want!r}")
        for col in ("over_budget", "turn1_degenerate"):
            if col in x and int(x[col].fillna(False).astype(bool).sum()):
                out.append(f"{c}: {int(x[col].fillna(False).astype(bool).sum())} cells with {col}")
        if c != "R0" and "turn1_parsed_ok" in x and int((x.turn1_parsed_ok == False).sum()):      # noqa: E712
            out.append(f"{c}: {int((x.turn1_parsed_ok == False).sum())} cells where Turn 1 did not parse")  # noqa: E712
    return out


def to_markdown(res: dict, configs: list[str]) -> str:
    L = ["## T5 matrix (mean TSR [95% CI], per-task 3-seed mean; cluster bootstrap over tasks)", "",
         "| config | " + " | ".join(f"{c}{' (n=2, anecdotal)' if c in ANECDOTAL else ''}" for c in CORPORA) + " |",
         "|---" * (len(CORPORA) + 1) + "|"]
    for c in configs:
        cells = []
        for corpus in CORPORA:
            m = res["matrix"].get(corpus, {}).get(c)
            cells.append(f"{m['mean_tsr']:.3f} [{m['ci_low']:.3f}, {m['ci_high']:.3f}]" if m else "–")
        L.append(f"| {c} | " + " | ".join(cells) + " |")
    L += ["", "## Secondary metrics (cell means)", "", "| config | corpus | gold coverage | selection precision | selection recall | cells |",
          "|---|---|---|---|---|---|"]
    for c in configs:
        for corpus in CORPORA:
            m = res["matrix"].get(corpus, {}).get(c)
            if m:
                L.append(f"| {c} | {corpus} | {m['gold_coverage']:.3f} | {m['selection_precision']:.3f} | {m['selection_recall']:.3f} | {m['n_cells']} |")
    for fam, title in (("primary", "R0 vs R1 (Holm over the four corpora)"), ("secondary", "vs B0M4 (Holm over these 8 tests)")):
        L += ["", f"## Pairwise: {title}", "", "| corpus | comparison | Δ TSR | 95% CI | p_raw | p_holm | significant |", "|---|---|---|---|---|---|---|"]
        for r in res["pairwise"][fam]:
            L.append(f"| {r['corpus']}{' (anecdotal)' if r['anecdotal'] else ''} | {r['a']} − {r['b']} | {r['delta']:+.3f} | "
                     f"[{r['ci_low']:+.3f}, {r['ci_high']:+.3f}] | {r['p_raw']:.4f} | {r['p_holm']:.4f} | {'yes' if r['significant'] else 'no'} |")
    L += ["", "## Anomalies / FAIL rows", ""] + ([f"* {a}" for a in res["anomalies"]] or ["* none"])
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="R1 vs R0 T5 ablation report")
    ap.add_argument("--config", action="append", default=[], help="NAME=DIR (R1=..., R0=...)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    gold = {c: _gold(c) for c in CORPORA}
    configs, rows = ["B0M4"], load_config("B0M4", M4, gold)
    for spec in args.config:
        name, path = spec.split("=", 1)
        configs.append(name)
        rows += load_config(name, Path(path), gold)
    res = analyse(rows, configs)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    import pandas as pd
    pd.DataFrame(rows).to_csv(out / "per_cell.csv", index=False)
    (out / "ablation.json").write_text(json.dumps(res, indent=1))
    (out / "ablation.md").write_text(to_markdown(res, configs))
    print(to_markdown(res, configs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
