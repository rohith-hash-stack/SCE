"""Stage 2 of a candidate validation (CANDIDATE_V4.md, CANDIDATE_V5.md): sets 2-5, diagnostic only.

Arms, all through the shipped `NLQIndex` (test files excluded):
  BASE  no summaries
  V2    the stage 1 v2 index (STAGE1_OUT/<repo>/v2.json) at w = SUMMARY_WEIGHT
  V4    the stage 1 v4 index (<repo>/v4.json) at w = SUMMARY_WEIGHT
  V5    the stage 1 v5 index (<repo>/v5.json; summary + effects), when --candidate v5
Each arm's text is `prism.summaries.index.search_text` of its entries.
Per question the best-ranked gold symbol is compared (unranked = worst, ties
dropped); two-sided exact sign test; mean recall@5 - as in set 5.

Stop rule (fixed before this runs): the candidate is dropped before set 6 if,
pooled over sets 2-5, more questions are worse than better against V2. Nothing
else is decided here: every set has been scored before.

Usage: python -m harness.experiments.purpose_search.stage2 APPS STAGE1_OUT OUT [--candidate v5]
APPS/<repo> is each repository at its dataset.json commit.
Writes OUT/{stage2.json,STAGE2.md}.
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

from harness.experiments.heldout_retrieval.set5.score import _best, _score, compare
from prism.summaries.index import SUMMARY_WEIGHT, read_index, search_text

SETS = ("set2", "set3", "set4", "set5")
DATASETS = Path(__file__).resolve().parents[1] / "heldout_retrieval"
ARMS = ("BASE", "V2", "V4")


def _texts(path: Path) -> dict:
    idx = read_index(path)
    return {q: search_text(e, idx.get("pipeline") or {}) for q, e in idx["functions"].items()}


def score(apps: Path, stage1: Path, arms=ARMS) -> dict:
    from prism.cli import build_pipeline
    from prism.query.nlq import NLQIndex
    rows, coverage, missing = {s: {a: {} for a in arms} for s in SETS}, {}, {}
    for s in SETS:
        ds = json.loads((DATASETS / s / "dataset.json").read_text())
        for repo, spec in ds["repos"].items():
            root = str(apps / repo / spec["root"])
            builder = build_pipeline(root, use_cache=False)[0]
            summaries = {a: _texts(stage1 / repo / f"{a.lower()}.json") for a in arms if a != "BASE"}
            idx = {"BASE": NLQIndex(builder, root)} | {
                a: NLQIndex(builder, root, summaries=t, summary_weight=SUMMARY_WEIGHT) for a, t in summaries.items()}
            pool = {sym.qualified_name for sym in idx["BASE"].pool}
            fns = [q for q in pool if q in summaries["V2"]]
            coverage[repo] = round(len(fns) / max(1, len(summaries["V2"])), 3)
            for q in (q for q in ds["questions"] if q["repo"] == repo):
                missing.update({q["id"]: g for g in q["gold"] if g not in pool})
                for a in arms:
                    rows[s][a][q["id"]] = _score(idx[a].ranked(q["question"]), q["gold"])
    return {"rows": rows, "summaries_in_pool": coverage, "gold_not_in_pool": missing}


def report(scored: dict, cand: str = "V4") -> dict:
    rows = scored["rows"]
    arms = list(next(iter(rows.values())))
    pairs = [f"{cand} vs V2", f"{cand} vs BASE", "V2 vs BASE"] + [f"{cand} vs {a}" for a in arms if a not in ("BASE", "V2", cand)]
    pooled = {a: {f"{s}:{q}": r for s in SETS for q, r in rows[s][a].items()} for a in arms}
    per_set = {s: {c: compare(rows[s], *c.split(" vs ")) for c in pairs} for s in SETS}
    total = {c: compare(pooled, *c.split(" vs ")) for c in pairs}
    metrics = {s: {a: {m: round(st.mean(r[m] for r in rows[s][a].values()), 3) for m in ("recall@5", "mrr")}
                   for a in arms} for s in SETS}
    v = total[f"{cand} vs V2"]
    stop = v["worse"] > v["better"]
    moved = sorted(((q, _best(pooled["V2"][q]), _best(pooled[cand][q])) for q in pooled["V2"]
                    if _best(pooled["V2"][q]) != _best(pooled[cand][q])), key=lambda t: t[0])
    return {"candidate": cand, "per_set": per_set, "pooled": total, "metrics": metrics, "moved": moved,
            "verdict": f"DROP {cand.lower()} before set 6" if stop else "CONTINUE to set 6",
            "summaries_in_pool": scored["summaries_in_pool"], "gold_not_in_pool": scored["gold_not_in_pool"]}


def render(r: dict) -> str:
    fmt = lambda c: (f"{c['better']} / {c['worse']} / {c['ties']} | {c['sign_p']} | {c['recall5_delta']:+.3f} | "
                     f"{c['mrr_delta']:+.3f}")
    arms = list(next(iter(r["metrics"].values())))
    L = [f"# Stage 2: sets 2-5, diagnostic ({r['candidate']})", "",
         f"**Stop rule ({r['candidate']} worse than better against V2, pooled): {r['verdict']}**", "",
         "| comparison | better / worse / ties | sign p | recall@5 delta | MRR delta |", "|---|---|---|---|---|"]
    L += [f"| pooled {c} | {fmt(v)} |" for c, v in r["pooled"].items()]
    L += [f"| {s} {c} | {fmt(v)} |" for s, cs in r["per_set"].items() for c, v in cs.items()]
    L += ["", "| set | " + " | ".join(f"{a} recall@5 | {a} MRR" for a in arms) + " |", "|---|" + "---|---|" * len(arms)]
    L += [f"| {s} | " + " | ".join(f"{m[a]['recall@5']} | {m[a]['mrr']}" for a in arms) + " |" for s, m in r["metrics"].items()]
    L += ["", f"Questions whose best gold rank moved (V2 -> {r['candidate']}; inf = unranked):", ""]
    L += [f"- {q}: {a} -> {b}" for q, a, b in r["moved"]]
    if r["gold_not_in_pool"]:
        L += ["", f"Gold symbols not in the current pool (scored unranked in every arm): {r['gold_not_in_pool']}"]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("apps", type=Path)
    ap.add_argument("stage1", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--candidate", default="v4", choices=("v4", "v5"))
    args = ap.parse_args(argv)
    arms = ARMS + (("V5",) if args.candidate == "v5" else ())
    scored = score(args.apps, args.stage1, arms)
    r = report(scored, args.candidate.upper())
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "stage2.json").write_text(json.dumps({**r, "per_question": scored["rows"]}, indent=1, default=str) + "\n")
    (args.out / "STAGE2.md").write_text(render(r))
    print(render(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
