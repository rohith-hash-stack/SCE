"""Score held-out set 5 once, under the rules pre-registered in README.md.

Arms, all through the shipped `NLQIndex` (no harness re-implementation):
  A  parser fix, test files in the pool   NLQIndex(..., exclude_test_files=False)
  B  parser fix + test-file exclusion     NLQIndex(...)            (shipped, no summaries)
  C  B + summary field                    NLQIndex(..., summaries="auto")  (shipped path)
C reads REPO/.prism/summaries.json (the live `prism summarize` index); the run
stops unless that index resolves to the shipped weight (0.5).

Rule 1 (test-file exclusion), B against A; Rule 2 (summary field), C against B.
Per question the best-ranked gold symbol is compared (unranked = worst, ties
dropped); two-sided exact sign test; mean recall@5 over the 30 questions.

Usage: python -m harness.experiments.heldout_retrieval.set5.score HONO_CHECKOUT OUT_DIR [--summaries FILE]
Refuses to run unless dataset.json is frozen. Writes OUT_DIR/{results.json,DECISION.md}.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import statistics as st
import sys
from pathlib import Path

from harness.experiments.purpose_search.decide import sign_test
from prism.cli import build_pipeline
from prism.query.nlq import NLQIndex
from prism.summaries.index import SUMMARY_WEIGHT, index_path

HERE = Path(__file__).resolve().parent
ALPHA, MAX_RECALL5_DROP = 0.05, 0.02


def _score(ranked: list[str], gold: list[str]) -> dict:
    ranks = {g: (ranked.index(g) + 1 if g in ranked else None) for g in gold}
    return {"ranks": ranks, "recall@5": sum(1 for r in ranks.values() if r and r <= 5) / len(gold),
            "recall@20": sum(1 for r in ranks.values() if r and r <= 20) / len(gold),
            "mrr": max((1 / r for r in ranks.values() if r), default=0.0)}


def _best(row: dict) -> float:
    return min([r for r in row["ranks"].values() if r] or [math.inf])


def compare(rows: dict, arm: str, base: str) -> dict:
    ids = list(rows[base])
    better = sum(_best(rows[arm][q]) < _best(rows[base][q]) for q in ids)
    worse = sum(_best(rows[arm][q]) > _best(rows[base][q]) for q in ids)
    r5 = st.mean(rows[arm][q]["recall@5"] for q in ids) - st.mean(rows[base][q]["recall@5"] for q in ids)
    return {"arm": arm, "against": base, "better": better, "worse": worse, "ties": len(ids) - better - worse,
            "sign_p": round(sign_test(better, worse), 4), "recall5_delta": round(r5, 3),
            "mrr_delta": round(st.mean(rows[arm][q]["mrr"] for q in ids) - st.mean(rows[base][q]["mrr"] for q in ids), 3)}


def decide(rows: dict) -> dict:
    r1 = compare(rows, "B", "A")
    inferior = (r1["worse"] > r1["better"] and r1["sign_p"] < ALPHA) or r1["recall5_delta"] < -MAX_RECALL5_DROP - 1e-12
    r2 = compare(rows, "C", "B")
    confirmed = r2["better"] > r2["worse"] and r2["sign_p"] < ALPHA and r2["recall5_delta"] >= -MAX_RECALL5_DROP - 1e-12
    return {"rule1": {**r1, "verdict": "REVERT the test-file exclusion" if inferior else "KEEP the test-file exclusion"},
            "rule2": {**r2, "verdict": "CONFIRMED: summary field helps on Hono" if confirmed
                      else "NOT CONFIRMED on Hono: investigate (shipped setting unchanged)"}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("checkout")
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--summaries", help="install this summaries.json into the checkout's .prism/ first")
    args = ap.parse_args(argv)
    ds = json.loads((HERE / "dataset.json").read_text())
    if ds.get("status") != "frozen":
        raise SystemExit(f"set 5 is {ds.get('status')!r}, not frozen: refusing to score")
    root = str(Path(args.checkout) / ds["repos"]["hono"]["root"])
    if args.summaries:
        index_path(root).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(args.summaries, index_path(root))
    builder = build_pipeline(root, use_cache=False)[0]
    arms = {"A": NLQIndex(builder, root, exclude_test_files=False), "B": NLQIndex(builder, root),
            "C": NLQIndex(builder, root, summaries="auto")}
    state = arms["C"].summary_state.status()
    if arms["C"].summary_weight != SUMMARY_WEIGHT:
        raise SystemExit(f"summary index does not resolve to w={SUMMARY_WEIGHT}: {state}")
    rows = {a: {q["id"]: _score(idx.ranked(q["question"]), q["gold"]) for q in ds["questions"]} for a, idx in arms.items()}
    d = decide(rows)
    metrics = {a: {m: round(st.mean(r[m] for r in rows[a].values()), 3) for m in ("recall@5", "recall@20", "mrr")} for a in rows}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "results.json").write_text(json.dumps(
        {"summary_index": state, "metrics": metrics, "decision": d, "per_question": rows}, indent=1) + "\n")
    lines = ["# Set 5 (Hono) decision - pre-registered rules (set5/README.md)", "",
             f"**Rule 1:** {d['rule1']['verdict']}  ", f"**Rule 2:** {d['rule2']['verdict']}", "",
             "| arm | recall@5 | recall@20 | MRR |", "|---|---|---|---|"]
    lines += [f"| {a} | {m['recall@5']} | {m['recall@20']} | {m['mrr']} |" for a, m in metrics.items()]
    lines += ["", "| comparison | better / worse / ties | sign p | recall@5 delta | MRR delta |", "|---|---|---|---|---|"]
    lines += [f"| {r['arm']} vs {r['against']} | {r['better']} / {r['worse']} / {r['ties']} | {r['sign_p']} | "
              f"{r['recall5_delta']:+.3f} | {r['mrr_delta']:+.3f} |" for r in (d["rule1"], d["rule2"])]
    lines += ["", f"Summary index: coverage {state['coverage']}, weight {state['summary_weight']}, {state['reason']}."]
    (args.out_dir / "DECISION.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
