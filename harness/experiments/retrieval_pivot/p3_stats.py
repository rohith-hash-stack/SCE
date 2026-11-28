"""Phase P3 statistics over a Layer 4 run's cells (`<run>/cells.jsonl`), as pre-registered in P3.md.

- Per arm (5 designs x 4k / 8k) and family: cells scored, mean forward F1 / precision / recall,
  fabrication rate (named symbols that are not in the corpus index, over all names), mean context
  tokens, backward pass rate.
- Win/loss matrix over the 10 arms: for each ordered pair, tasks better / worse / tied on the
  task's mean forward F1 (mean over its seeds), with the two-sided exact sign-test p.
- The fixed-sequence rule (P3b's decision; computed on P3a as a diagnostic only), at 8k:
    1 reach        R1_A0  beats  R0_A0    more tasks better than worse, sign p < ALPHA
    2 compression  R1_A1n vs     R1_A0    mean F1 at most NONINF_MARGIN lower, and not significantly worse
    3 profiles     R1_A5  beats  R1_A1n   more tasks better than worse, sign p < ALPHA
  Stop at the first failing step; an arm whose fabrication rate is more than FABRICATION_MARGIN above
  the baseline's (R0_A0 at 8k) is disqualified, and a step whose candidate is disqualified fails.
  The winner is the last passing step's candidate (R0_A0 if step 1 fails).

Usage: python -m harness.experiments.retrieval_pivot.p3_stats RUN_DIR [--phase P3a|P3b]
Writes RUN_DIR/p3_stats.json and RUN_DIR/P3_STATS.md.
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

from harness.experiments.purpose_search.decide import sign_test
from harness.experiments.retrieval_pivot.p3 import ARMS, DESIGNS

ALPHA = 0.05
NONINF_MARGIN = 0.02
FABRICATION_MARGIN = 0.05
DECISION_BUDGET = "8k"
BASELINE = f"p3_R0_A0@{DECISION_BUDGET}"
STEPS = (("reach", "R1_A0", "R0_A0", "superiority"),
         ("compression", "R1_A1n", "R1_A0", "noninferiority"),
         ("profiles", "R1_A5", "R1_A1n", "superiority"))


def arm_id(design: str, budget: str = DECISION_BUDGET) -> str:
    return f"p3_{design}@{budget}"


def load_cells(run: Path) -> list[dict]:
    """The latest record per (task, arm, seed) - a resumed run appends - of P3 arms only."""
    latest = {}
    for line in (run / "cells.jsonl").read_text().splitlines():
        if line.strip():
            c = json.loads(line)
            if c.get("arm") in ARMS:
                latest[(c["task_id"], c["arm"], c["seed"])] = c
    return list(latest.values())


def named_count(cell: dict) -> int:
    from harness.experiments.layer4.tasks import MAX_ANSWER_SYMBOLS, parse_forward
    parsed = parse_forward(cell.get("reply") or "")
    names = [n for n in (parsed or {}).get("affected_symbols") or [] if isinstance(n, (str, int))]
    return min(len(names), MAX_ANSWER_SYMBOLS)


def per_arm(cells: list[dict]) -> dict:
    out = {}
    for arm in ARMS:
        cs = [c for c in cells if c["arm"] == arm and c["status"] == "ok"]
        fwd = [c for c in cs if c["family"] == "forward_blast"]
        bwd = [c for c in cs if c["family"] == "backward_adaptation"]
        named = sum(named_count(c) for c in fwd)
        unknown = sum(len(c["score"].get("unknown_names") or []) for c in fwd)
        mean = lambda xs: round(st.mean(xs), 4) if xs else None
        out[arm] = {
            "cells": {s: sum(1 for c in cells if c["arm"] == arm and c["status"] == s)
                      for s in ("ok", "leak", "error", "timeout", "dry_run")},
            "forward_n": len(fwd), "f1": mean([c["score"]["f1"] for c in fwd]),
            "precision": mean([c["score"]["precision"] for c in fwd]), "recall": mean([c["score"]["recall"] for c in fwd]),
            "names": named, "unknown_names": unknown, "fabrication_rate": round(unknown / named, 4) if named else None,
            "backward_n": len(bwd), "backward_pass_rate": mean([1.0 if c["score"]["passed"] else 0.0 for c in bwd]),
            "context_tokens": mean([c["tokens"]["context"] for c in cs]),
        }
    return out


def task_means(cells: list[dict]) -> dict[str, dict[str, float]]:
    """arm -> task -> mean forward F1 over its ok seeds."""
    acc = defaultdict(lambda: defaultdict(list))
    for c in cells:
        if c["status"] == "ok" and c["family"] == "forward_blast":
            acc[c["arm"]][c["task_id"]].append(c["score"]["f1"])
    return {arm: {t: st.mean(v) for t, v in tasks.items()} for arm, tasks in acc.items()}


def compare(means: dict, a: str, b: str) -> dict:
    """a against b over the tasks both have."""
    ta, tb = means.get(a, {}), means.get(b, {})
    common = sorted(set(ta) & set(tb))
    better = sum(ta[t] > tb[t] for t in common)
    worse = sum(ta[t] < tb[t] for t in common)
    diff = st.mean(ta[t] - tb[t] for t in common) if common else None
    return {"arm": a, "against": b, "tasks": len(common), "better": better, "worse": worse,
            "ties": len(common) - better - worse, "sign_p": round(sign_test(better, worse), 4) if better + worse else 1.0,
            "mean_f1_delta": round(diff, 4) if diff is not None else None}


def win_loss(means: dict) -> dict:
    return {a: {b: compare(means, a, b) for b in ARMS if b != a} for a in ARMS}


def decide(arms: dict, means: dict) -> dict:
    base_fab = arms[BASELINE]["fabrication_rate"]

    def disqualified(arm: str) -> bool:
        fab = arms[arm]["fabrication_rate"]
        return base_fab is not None and fab is not None and fab - base_fab > FABRICATION_MARGIN

    steps, winner = [], "R0_A0"
    for name, cand, ref, kind in STEPS:
        c = compare(means, arm_id(cand), arm_id(ref))
        if kind == "superiority":
            ok = c["better"] > c["worse"] and c["sign_p"] < ALPHA
        else:
            significantly_worse = c["worse"] > c["better"] and c["sign_p"] < ALPHA
            ok = c["mean_f1_delta"] is not None and c["mean_f1_delta"] >= -NONINF_MARGIN - 1e-12 and not significantly_worse
        dq = disqualified(arm_id(cand))
        passes = ok and not dq and c["tasks"] > 0
        steps.append({"step": name, "kind": kind, **c, "disqualified": dq, "passes": passes})
        if not passes:
            break
        winner = cand
    return {"steps": steps, "winner": winner, "baseline_fabrication_rate": base_fab}


def render(result: dict) -> str:
    phase = result["phase"]
    L = [f"# Phase {phase} statistics ({'exploratory - decides nothing' if phase == 'P3a' else 'the pre-registered decision'})", "",
         f"Run: `{result['run']}`. Forward F1 per task = mean over its seeds; sign tests over tasks.", "",
         "| arm | ok cells | forward n | F1 | precision | recall | fabrication | backward pass | context tok |",
         "|---|---|---|---|---|---|---|---|---|"]
    for arm, a in result["arms"].items():
        fmt = lambda v: "-" if v is None else v
        L.append(f"| {arm} | {a['cells']['ok']} | {a['forward_n']} | {fmt(a['f1'])} | {fmt(a['precision'])} | "
                 f"{fmt(a['recall'])} | {fmt(a['fabrication_rate'])} | {fmt(a['backward_pass_rate'])} | {fmt(a['context_tokens'])} |")
    d = result["decision"]
    L += ["", f"## Fixed-sequence rule at {DECISION_BUDGET} ({'diagnostic on P3a' if phase == 'P3a' else 'decision'})", "",
          "| step | candidate vs reference | better / worse / ties | sign p | mean F1 delta | disqualified | passes |",
          "|---|---|---|---|---|---|---|"]
    for s in d["steps"]:
        L.append(f"| {s['step']} | {s['arm']} vs {s['against']} | {s['better']} / {s['worse']} / {s['ties']} | {s['sign_p']} | "
                 f"{s['mean_f1_delta']} | {s['disqualified']} | {s['passes']} |")
    L += ["", f"**Winner: {d['winner']}**" + (" (exploratory: not a decision)" if phase == "P3a" else ""), "",
          "## Win/loss matrix (forward F1, tasks better / worse / tied; row vs column)", "",
          "| row \\ column | " + " | ".join(a.removeprefix("p3_") for a in ARMS) + " |",
          "|---|" + "---|" * len(ARMS)]
    for a in ARMS:
        cells = [("-" if b == a else f"{result['win_loss'][a][b]['better']}/{result['win_loss'][a][b]['worse']}/"
                  f"{result['win_loss'][a][b]['ties']}") for b in ARMS]
        L.append(f"| {a.removeprefix('p3_')} | " + " | ".join(cells) + " |")
    return "\n".join(L) + "\n"


def analyse(run: Path, phase: str = "P3a") -> dict:
    cells = load_cells(run)
    arms = per_arm(cells)
    means = task_means(cells)
    result = {"phase": phase, "run": str(run), "designs": DESIGNS, "arms": arms, "task_means": means,
              "win_loss": win_loss(means), "decision": decide(arms, means)}
    (run / "p3_stats.json").write_text(json.dumps(result, indent=1) + "\n")
    (run / "P3_STATS.md").write_text(render(result))
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run", type=Path)
    ap.add_argument("--phase", choices=("P3a", "P3b"), default="P3a")
    a = ap.parse_args(argv)
    print(render(analyse(a.run, a.phase)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
