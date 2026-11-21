"""Score held-out set 7 once, under the rule pre-registered in README.md.

Arms, all through the shipped `NLQIndex` (test files excluded), at SUMMARY_WEIGHT:
  BASE    no summaries
  V2      the shipped summarizer's index     SUMMARIES/<repo>/v2.json   (prism.summaries.pipeline.build_index)
  SRC     card + numbered source             SUMMARIES/<repo>/src.json
  DIGEST  card + the function's digest       SUMMARIES/<repo>/digest.json
(written by `python -m harness.experiments.summary_grounding.run generate APPS SUMMARIES --set 7`). Every arm must
cover >= MIN_COVERAGE of its repository's functions.

Per question the best-ranked gold symbol is compared (unranked = worst, ties dropped); two-sided exact sign test;
mean recall@5 over the 120 questions.

Usage: python -m harness.experiments.heldout_retrieval.set7.score APPS SUMMARIES OUT_DIR
Refuses to run unless dataset.json is frozen and its questions hash matches. Writes OUT_DIR/{results.json,DECISION.md}.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics as st
import sys
from pathlib import Path

from harness.experiments.heldout_retrieval.set5.score import _best, _score, compare
from prism.cli import build_pipeline
from prism.query.nlq import NLQIndex
from prism.summaries.index import MIN_COVERAGE, SUMMARY_WEIGHT, function_symbols, read_index, search_text

HERE = Path(__file__).resolve().parent
ALPHA, MAX_RECALL5_DROP = 0.05, 0.02
ARMS = ("BASE", "V2", "SRC", "DIGEST")
SRC_BODY_CAP = 60  # SRC sees the first 60 lines (diagnostic split)


def beats(c: dict) -> bool:
    return c["better"] > c["worse"] and c["sign_p"] < ALPHA and c["recall5_delta"] >= -MAX_RECALL5_DROP - 1e-12


def decide(rows: dict) -> dict:
    """DIGEST qualifies iff (1) DIGEST beats V2, (2) SRC does not beat DIGEST, (3) DIGEST recall@5 >= SRC - 0.02."""
    contrasts = {f"{a} vs {b}": {**compare(rows, a, b)} for a, b in (
        ("DIGEST", "V2"), ("SRC", "DIGEST"), ("DIGEST", "SRC"), ("SRC", "V2"), ("DIGEST", "BASE"), ("SRC", "BASE"),
        ("V2", "BASE"))}
    for c in contrasts.values():
        c["beats"] = beats(c)
    r5 = {a: st.mean(r["recall@5"] for r in rows[a].values()) for a in rows}
    conditions = {
        "1 DIGEST beats V2": contrasts["DIGEST vs V2"]["beats"],
        "2 SRC does not beat DIGEST": not contrasts["SRC vs DIGEST"]["beats"],
        "3 DIGEST recall@5 >= SRC - 0.02": r5["DIGEST"] >= r5["SRC"] - MAX_RECALL5_DROP - 1e-12,
    }
    if all(conditions.values()):
        verdict = "DIGEST QUALIFIES to replace V2"
    elif conditions["1 DIGEST beats V2"] and contrasts["SRC vs DIGEST"]["beats"]:
        verdict = "DIGEST does not qualify: the raw source is better input than the digest"
    elif not conditions["1 DIGEST beats V2"]:
        verdict = "DIGEST does not qualify: digest summaries do not improve on V2"
    else:
        verdict = "DIGEST does not qualify: recall@5 more than 0.02 below SRC"
    return {"conditions": conditions, "verdict": verdict, "contrasts": contrasts}


def frozen_dataset() -> dict:
    ds = json.loads((HERE / "dataset.json").read_text())
    if ds.get("status") != "frozen":
        raise SystemExit(f"set 7 is {ds.get('status')!r}, not frozen: refusing to run")
    if hashlib.sha256(json.dumps(ds["questions"], sort_keys=True).encode()).hexdigest() != ds["questions_sha256"]:
        raise SystemExit("set 7 questions differ from the frozen hash: refusing to run")
    return ds


def _summaries(path: Path, arm: str) -> dict[str, str]:
    if arm == "V2":
        index = read_index(path / "v2.json")
        if not index:
            raise SystemExit(f"{path / 'v2.json'}: missing or unreadable")
        return {q: search_text(e, index.get("pipeline") or {}) for q, e in index["functions"].items()}
    data = json.loads((path / f"{arm.lower()}.json").read_text())
    return {q: e["summary"] for q, e in data.items() if e.get("summary")}


def score(apps: Path, summaries: Path, out: Path) -> dict:
    ds = frozen_dataset()
    rows = {a: {} for a in ARMS}
    coverage, words, long_gold = {}, {a: [] for a in ARMS[1:]}, set()
    for repo, spec in ds["repos"].items():
        root = str(apps / repo / spec["root"])
        builder = build_pipeline(root, use_cache=False)[0]
        base = NLQIndex(builder, root)
        names = {s.qualified_name for s in function_symbols(base.pool)}
        idx = {"BASE": base}
        for arm in ARMS[1:]:
            texts = _summaries(summaries / repo, arm)
            coverage[f"{repo}/{arm}"] = round(len(names & set(texts)) / max(1, len(names)), 3)
            if coverage[f"{repo}/{arm}"] < MIN_COVERAGE:
                raise SystemExit(f"{repo}/{arm}: coverage {coverage[f'{repo}/{arm}']} < {MIN_COVERAGE}")
            words[arm].extend(len(texts[q].split()) for q in names if texts.get(q))
            idx[arm] = NLQIndex(builder, root, summaries=texts, summary_weight=SUMMARY_WEIGHT)
        for q in (q for q in ds["questions"] if q["repo"] == repo):
            lo, hi = builder.symbol_table.get(q["gold"][0]).line_range
            if hi - lo + 1 > SRC_BODY_CAP:
                long_gold.add(q["id"])
            for arm, index in idx.items():
                rows[arm][q["id"]] = {**_score(index.ranked(q["question"]), q["gold"]), "repo": repo}
    decision = decide(rows)
    metrics = {a: {m: round(st.mean(r[m] for r in rows[a].values()), 3) for m in ("recall@5", "recall@20", "mrr")}
               for a in ARMS}
    by_repo = {a: {repo: round(st.mean(r["recall@5"] for r in rows[a].values() if r["repo"] == repo), 3)
                   for repo in ds["repos"]} for a in ARMS}
    split = {}
    for name, ids in (("gold over 60 lines", long_gold), ("gold 60 lines or fewer", set(rows["BASE"]) - long_gold)):
        split[name] = {"n": len(ids), **{f"{a} vs {b}": {
            "better": sum(_best(rows[a][i]) < _best(rows[b][i]) for i in ids),
            "worse": sum(_best(rows[a][i]) > _best(rows[b][i]) for i in ids)}
            for a, b in (("DIGEST", "SRC"), ("DIGEST", "V2"), ("SRC", "V2"))}}
    result = {"verdict": decision["verdict"], "conditions": decision["conditions"], "metrics": metrics,
              "contrasts": decision["contrasts"], "recall@5_by_repo": by_repo, "gold_length_split": split,
              "mean_words": {a: round(st.mean(v), 1) for a, v in words.items() if v}, "coverage": coverage,
              "questions_sha256": ds["questions_sha256"], "per_question": rows}
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(result, indent=1) + "\n")
    (out / "DECISION.md").write_text(render(result) + "\n")
    print(render(result))
    return result


def render(r: dict) -> str:
    L = ["# Held-out set 7: digest-grounded summaries (pre-registered rule, scored once)", "",
         f"**{r['verdict']}**", ""]
    L += [f"- {k}: {'yes' if v else 'no'}" for k, v in r["conditions"].items()]
    L += ["", "| arm | recall@5 | recall@20 | MRR | mean words |", "|---|---|---|---|---|"]
    L += [f"| {a} | {m['recall@5']} | {m['recall@20']} | {m['mrr']} | {r['mean_words'].get(a, '-')} |"
          for a, m in r["metrics"].items()]
    L += ["", "| contrast | better / worse / ties | sign p | recall@5 delta | MRR delta | beats |", "|---|---|---|---|---|---|"]
    L += [f"| {k} | {c['better']} / {c['worse']} / {c['ties']} | {c['sign_p']} | {c['recall5_delta']:+.3f} | "
          f"{c['mrr_delta']:+.3f} | {'yes' if c['beats'] else 'no'} |" for k, c in r["contrasts"].items()]
    repos = list(next(iter(r["recall@5_by_repo"].values())))
    L += ["", "Recall@5 by repository (diagnostic):", "", "| arm | " + " | ".join(repos) + " |", "|---|" + "---|" * len(repos)]
    L += [f"| {a} | " + " | ".join(str(v[x]) for x in repos) + " |" for a, v in r["recall@5_by_repo"].items()]
    L += ["", "By gold function length (diagnostic; SRC sees the first 60 lines):", "",
          "| group | n | DIGEST vs SRC | DIGEST vs V2 | SRC vs V2 |", "|---|---|---|---|---|"]
    L += [f"| {g} | {v['n']} | " + " | ".join(f"{v[c]['better']} / {v[c]['worse']}" for c in
          ("DIGEST vs SRC", "DIGEST vs V2", "SRC vs V2")) + " |" for g, v in r["gold_length_split"].items()]
    L += ["", f"Coverage: {json.dumps(r['coverage'])}", "", f"questions_sha256: `{r['questions_sha256']}`"]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("apps", type=Path)
    ap.add_argument("summaries", type=Path)
    ap.add_argument("out", type=Path)
    args = ap.parse_args(argv)
    score(args.apps, args.summaries, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
