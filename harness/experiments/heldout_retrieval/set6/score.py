"""Score held-out set 6 once, under the rule pre-registered in README.md
(harness/experiments/purpose_search/CANDIDATE_V5.md).

Arms, all through the shipped `NLQIndex` (test files excluded):
  BASE  no summaries
  V2    the locked v2 index           SUMMARIES/<repo>/v2.json  at w = SUMMARY_WEIGHT
  V4    candidate v4 (diagnostic)     SUMMARIES/<repo>/v4.json
  V5    candidate v5 (the decision)   SUMMARIES/<repo>/v5.json  (summary + effects)
Each arm's text is `prism.summaries.index.search_text` of its entries. Every
index must cover >= MIN_COVERAGE of its repository's functions.

Per question the best-ranked gold symbol is compared (unranked = worst, ties
dropped); two-sided exact sign test; mean recall@5 over the 120 questions.

Usage: python -m harness.experiments.heldout_retrieval.set6.score APPS SUMMARIES OUT_DIR
Refuses to run unless dataset.json is frozen and its questions hash matches.
Writes OUT_DIR/{results.json,DECISION.md}.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics as st
import sys
from pathlib import Path

from harness.experiments.heldout_retrieval.set5.score import _score, compare
from prism.cli import build_pipeline
from prism.query.nlq import NLQIndex
from prism.summaries.index import MIN_COVERAGE, SUMMARY_WEIGHT, function_symbols, read_index, search_text

HERE = Path(__file__).resolve().parent
ALPHA, MAX_RECALL5_DROP = 0.05, 0.02
ARMS = ("BASE", "V2", "V4", "V5")


def decide(rows: dict) -> dict:
    """V5 qualifies iff it beats V2 and beats BASE (more better than worse,
    sign p < ALPHA, recall@5 at most MAX_RECALL5_DROP below)."""
    out = {}
    for against in ("V2", "BASE"):
        c = compare(rows, "V5", against)
        c["passes"] = c["better"] > c["worse"] and c["sign_p"] < ALPHA and c["recall5_delta"] >= -MAX_RECALL5_DROP - 1e-12
        out[f"V5 vs {against}"] = c
    out["diagnostic"] = {c: compare(rows, *c.split(" vs ")) for c in ("V4 vs V2", "V2 vs BASE", "V5 vs V4")}
    out["verdict"] = ("V5 QUALIFIES" if out["V5 vs V2"]["passes"] and out["V5 vs BASE"]["passes"]
                      else "V5 does not qualify: v2 stays locked")
    return out


def frozen_dataset() -> dict:
    ds = json.loads((HERE / "dataset.json").read_text())
    if ds.get("status") != "frozen":
        raise SystemExit(f"set 6 is {ds.get('status')!r}, not frozen: refusing to score")
    if hashlib.sha256(json.dumps(ds["questions"], sort_keys=True).encode()).hexdigest() != ds["questions_sha256"]:
        raise SystemExit("set 6 questions differ from the frozen hash: refusing to score")
    return ds


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("apps", type=Path)
    ap.add_argument("summaries", type=Path)
    ap.add_argument("out_dir", type=Path)
    args = ap.parse_args(argv)
    ds = frozen_dataset()
    rows, coverage = {a: {} for a in ARMS}, {}
    for repo, spec in ds["repos"].items():
        root = str(args.apps / repo / spec["root"])
        builder = build_pipeline(root, use_cache=False)[0]
        idx = {"BASE": NLQIndex(builder, root)}
        names = {s.qualified_name for s in function_symbols(idx["BASE"].pool)}
        for arm in ARMS[1:]:
            data = read_index(args.summaries / repo / f"{arm.lower()}.json")
            if data is None:
                raise SystemExit(f"{repo}: no {arm.lower()} index")
            texts = {q: search_text(e, data.get("pipeline") or {}) for q, e in data["functions"].items()}
            coverage[f"{repo}/{arm}"] = round(len(names & set(texts)) / max(1, len(names)), 3)
            if coverage[f"{repo}/{arm}"] < MIN_COVERAGE:
                raise SystemExit(f"{repo}/{arm}: coverage {coverage[f'{repo}/{arm}']} < {MIN_COVERAGE}")
            idx[arm] = NLQIndex(builder, root, summaries=texts, summary_weight=SUMMARY_WEIGHT)
        for q in (q for q in ds["questions"] if q["repo"] == repo):
            for arm, index in idx.items():
                rows[arm][q["id"]] = _score(index.ranked(q["question"]), q["gold"])
    d = decide(rows)
    metrics = {a: {m: round(st.mean(r[m] for r in rows[a].values()), 3) for m in ("recall@5", "recall@20", "mrr")} for a in rows}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "results.json").write_text(json.dumps(
        {"coverage": coverage, "metrics": metrics, "decision": d, "per_question": rows}, indent=1) + "\n")
    fmt = lambda c: (f"| {c['arm']} vs {c['against']} | {c['better']} / {c['worse']} / {c['ties']} | {c['sign_p']} | "
                     f"{c['recall5_delta']:+.3f} | {c['mrr_delta']:+.3f} |")
    lines = ["# Set 6 decision - pre-registered rule (set6/README.md, CANDIDATE_V5.md)", "", f"**{d['verdict']}**", "",
             "| comparison | better / worse / ties | sign p | recall@5 delta | MRR delta |", "|---|---|---|---|---|",
             fmt(d["V5 vs V2"]), fmt(d["V5 vs BASE"]), "", "Diagnostic (decides nothing):", "",
             "| comparison | better / worse / ties | sign p | recall@5 delta | MRR delta |", "|---|---|---|---|---|"]
    lines += [fmt(c) for c in d["diagnostic"].values()]
    lines += ["", "| arm | recall@5 | recall@20 | MRR |", "|---|---|---|---|"]
    lines += [f"| {a} | {m['recall@5']} | {m['recall@20']} | {m['mrr']} |" for a, m in metrics.items()]
    (args.out_dir / "DECISION.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
