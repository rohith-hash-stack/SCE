"""The pre-registered set-4 ship decision (CANDIDATE.md), applied mechanically.

Rule: evaluate FIELD w=0.5 first. It passes if, over all set-4 questions,
  (1) it beats BASE: more questions improve than worsen, with a two-sided sign
      test p < 0.05 (per question: the rank of the best-ranked gold symbol,
      unranked = worst; ties dropped), and
  (2) its mean recall@5 is at most 0.02 below BASE's.
If w=0.5 passes, ship w=0.5. Otherwise apply the same test to FIELD w=1.0 and
ship it if it passes. If neither passes, do not ship.

Usage: python -m harness.experiments.purpose_search.decide OUT_DIR
Writes OUT_DIR/DECISION.md and OUT_DIR/decision.json.
"""
from __future__ import annotations

import json
import math
import statistics as st
import sys
from pathlib import Path

SET4 = ("flask", "httpie", "fastify", "zod")
ORDER = ("FIELD w=0.5", "FIELD w=1.0")  # pre-registered order
ALPHA = 0.05
MAX_RECALL5_DROP = 0.02


def sign_test(better: int, worse: int) -> float:
    """Two-sided exact binomial sign test (ties already dropped)."""
    n, k = better + worse, min(better, worse)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0


def _best(row: dict) -> float:
    ranks = [r for r in row["ranks"].values() if r]
    return min(ranks) if ranks else math.inf


def evaluate(results: dict[str, dict], arm: str) -> dict:
    better = worse = 0
    r5_arm, r5_base, mrr_arm, mrr_base = [], [], [], []
    for repo in SET4:
        base = {q["id"]: q for q in results[repo]["search"]["BASE"]["per_question"]}
        cand = {q["id"]: q for q in results[repo]["search"][arm]["per_question"]}
        for qid, b in base.items():
            c = cand[qid]
            better += _best(c) < _best(b)
            worse += _best(c) > _best(b)
            r5_arm.append(c["recall@5"]); r5_base.append(b["recall@5"])
            mrr_arm.append(c["mrr"]); mrr_base.append(b["mrr"])
    p = sign_test(better, worse)
    delta_r5 = st.mean(r5_arm) - st.mean(r5_base)
    beats = better > worse and p < ALPHA
    keeps_r5 = delta_r5 >= -MAX_RECALL5_DROP - 1e-12
    return {"arm": arm, "questions": len(r5_arm), "better": better, "worse": worse, "ties": len(r5_arm) - better - worse,
            "sign_p": round(p, 4), "recall5_base": round(st.mean(r5_base), 3), "recall5_arm": round(st.mean(r5_arm), 3),
            "recall5_delta": round(delta_r5, 3), "mrr_base": round(st.mean(mrr_base), 3),
            "mrr_arm": round(st.mean(mrr_arm), 3), "beats_base": beats, "recall5_ok": keeps_r5,
            "passes": beats and keeps_r5}


def decide(results: dict[str, dict]) -> dict:
    missing = [r for r in SET4 if r not in results]
    if missing:
        raise SystemExit(f"set-4 results missing for {missing}: no decision")
    tests = [evaluate(results, arm) for arm in ORDER]
    shipped = next((t["arm"] for t in tests if t["passes"]), None)
    verdict = ("SHIP " + shipped) if shipped == ORDER[0] else (
        "FALL BACK: ship " + shipped) if shipped else "HALT: do not ship"
    return {"verdict": verdict, "ship": shipped, "tests": tests,
            "rule": {"order": ORDER, "alpha": ALPHA, "max_recall5_drop": MAX_RECALL5_DROP}}


def render(d: dict) -> str:
    L = ["# Set 4 ship decision (pre-registered rule, CANDIDATE.md)", "", f"**{d['verdict']}**", "",
         "| arm | better / worse / ties | sign p | beats BASE | recall@5 BASE -> arm (delta) | within 0.02 | MRR BASE -> arm | passes |",
         "|---|---|---|---|---|---|---|---|"]
    for t in d["tests"]:
        L.append(f"| {t['arm']} | {t['better']} / {t['worse']} / {t['ties']} | {t['sign_p']} | {'yes' if t['beats_base'] else 'no'} | "
                 f"{t['recall5_base']} -> {t['recall5_arm']} ({t['recall5_delta']:+.3f}) | {'yes' if t['recall5_ok'] else 'no'} | "
                 f"{t['mrr_base']} -> {t['mrr_arm']} | {'yes' if t['passes'] else 'no'} |")
    L += ["", "Rule: evaluate w=0.5 first; it passes if it beats BASE (more questions better than worse, two-sided sign test "
          "p < 0.05) and its mean recall@5 drops by at most 0.02. Otherwise apply the same test to w=1.0. If neither "
          "passes, do not ship."]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    out = Path((argv or sys.argv[1:])[0])
    results = {r: json.loads((out / f"{r}.json").read_text()) for r in SET4 if (out / f"{r}.json").exists()}
    d = decide(results)
    (out / "decision.json").write_text(json.dumps(d, indent=1) + "\n")
    (out / "DECISION.md").write_text(render(d))
    print(render(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
