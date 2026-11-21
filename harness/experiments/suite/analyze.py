"""The suite's pre-registered analysis (README, "Tests and decisions").

    python -m harness.experiments.suite.analyze            # reports/suite/RESULTS.json and REPORT.md

Primary comparison per type, PRISM against the strongest baseline:

    type 1 forward   callers found           McNemar over (task, caller)        PRISM vs FILE
    type 2 newfn     helper reuse per task   Wilcoxon                           PRISM vs FILE
    type 3 bugs      a bug function in top 5 McNemar over tasks                 PRISM vs BM25
    type 4 blast     traced-caller recall    Wilcoxon                           PRISM vs GREP
    type 6 tests     the tests pass          McNemar over tasks                 PRISM vs FILE

- The five p-values are Holm-adjusted together.
- Type 5 (conceptual) gets the same test as type 3, reported separately and never used for a decision.
- Type 1 also tests PRISM_BREAKS against PRISM on callers found, with wrong functions per task as the
  side condition.
- Each comparison carries the bootstrap 95% interval of the per-task difference (10,000 resamples,
  seed 5501).
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from harness.experiments.suite import stats

ROOT = Path("harness/experiments/suite/repos")
OUT = Path("reports/suite")
PY = {"werkzeug", "marshmallow", "rich", "tornado", "aiohttp", "scrapy"}
REPOS = ("marshmallow", "werkzeug", "rich", "axios", "mocha", "tornado", "hapi", "fastify", "scrapy", "aiohttp")


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 3) if xs else None


# ------------------------------------------------------------------ per-type item tables
def forward_rows() -> list[dict]:
    rows = []
    for repo in REPOS:
        res = _load(OUT / repo / "forward" / "results.json")
        if res:
            rows += res["rows"]
    return rows


def scores(kind: str, name: str = "scores.json") -> list[dict]:
    rows = []
    for repo in REPOS:
        s = _load(OUT / repo / kind / name)
        if not s:
            continue
        for key, v in s.items():
            task, arm = key.rsplit("/", 1)
            rows.append({**v, "task": task, "arm": arm, "repo": repo})
    return rows


def paired(rows: list[dict], a: str, b: str, key) -> tuple[list, list, list]:
    """Values of `key(row)` for arm a and arm b on the tasks both have; also the task ids."""
    by = {}
    for r in rows:
        by.setdefault(r["task"] if "task" in r else r["id"], {})[r["arm"]] = r
    xa, xb, tasks = [], [], []
    for t, arms in sorted(by.items()):
        if a in arms and b in arms:
            xa.append(key(arms[a]))
            xb.append(key(arms[b]))
            tasks.append(t)
    return xa, xb, tasks


def compare_items(rows, a, b, items_of) -> dict:
    """McNemar over items (`items_of(row)` -> {item: bool}), bootstrap over tasks of found counts."""
    by = {}
    for r in rows:
        by.setdefault(r.get("task") or r["id"], {})[r["arm"]] = r
    ia, ib, ga, gb, groups = [], [], [], [], []
    for t, arms in sorted(by.items()):
        if a not in arms or b not in arms:
            continue
        xa, xb = items_of(arms[a]), items_of(arms[b])
        for k in sorted(set(xa) | set(xb)):
            ia.append(bool(xa.get(k)))
            ib.append(bool(xb.get(k)))
            groups.append(t)
    m = stats.mcnemar(ia, ib)
    boot = stats.bootstrap_diff([float(x) for x in ia], [float(x) for x in ib], groups)
    return {"a": a, "b": b, "items": len(ia), "a_yes": sum(ia), "b_yes": sum(ib), **m, "bootstrap": boot}


def compare_scores(rows, a, b, key) -> dict:
    xa, xb, tasks = paired(rows, a, b, key)
    w = stats.wilcoxon(xa, xb)
    boot = stats.bootstrap_diff(xa, xb, tasks)
    return {"a": a, "b": b, "tasks": len(tasks), "a_mean": _mean(xa), "b_mean": _mean(xb),
            "p": w["p"] if w else 1.0, "wilcoxon": w, "bootstrap": boot}


def by_arm(rows, metrics: dict) -> dict:
    out = {}
    for arm in sorted({r["arm"] for r in rows}):
        rs = [r for r in rows if r["arm"] == arm]
        out[arm] = {"n": len(rs), **{name: _mean([f(r) for r in rs]) for name, f in metrics.items()},
                    "seconds": _mean([r.get("seconds") for r in rs]),
                    "prompt_tokens": _mean([r.get("prompt_tokens") for r in rs])}
    return out


def split(rows, metrics) -> dict:
    return {"all": by_arm(rows, metrics),
            "python": by_arm([r for r in rows if r["repo"] in PY], metrics),
            "javascript": by_arm([r for r in rows if r["repo"] not in PY], metrics),
            "repos": {repo: by_arm([r for r in rows if r["repo"] == repo], metrics)
                      for repo in REPOS if any(r["repo"] == repo for r in rows)}}


NUM_CTX, NUM_PREDICT = 20480, 4096
MAX_TRUNCATED, MAX_TRUNCATED_GAP = 0.10, 0.05


def validity(rows, arms) -> dict:
    """Per version: the share of answers cut at the answer cap (`done_reason` "length"), and of prompts so long that
    the window could not also hold a full answer (Ollama would cut the prompt). A comparison is invalid when either
    version cuts more than 10% of its answers or the two differ by more than 5 points."""
    per = {}
    for arm in arms:
        rs = [r for r in rows if r["arm"] == arm]
        if not rs:
            continue
        per[arm] = {"n": len(rs),
                    "truncated": round(sum(r.get("done_reason") == "length" for r in rs) / len(rs), 3),
                    "prompt_near_window": round(sum(int(r.get("prompt_tokens") or 0) >= NUM_CTX - NUM_PREDICT
                                                    for r in rs) / len(rs), 3)}
    rates = [v["truncated"] for v in per.values()]
    return {"arms": per, "valid": bool(rates) and max(rates) <= MAX_TRUNCATED
            and max(rates) - min(rates) <= MAX_TRUNCATED_GAP}


# ------------------------------------------------------------------ the analysis
def analyze() -> dict:
    out: dict = {"types": {}}
    fw = forward_rows()
    if fw:
        found = lambda r: {c: c in r["found"] for c in r["found"] + r["missed"]}
        metrics = {"callers_found_share": lambda r: len(r["found"]) / max(1, r["changed"]),
                   "wrong_functions": lambda r: len(r["extra"]),
                   "all_found": lambda r: float(not r["missed"])}
        # timing and tokens come from answers.json
        ans = {}
        for repo in REPOS:
            a = _load(OUT / repo / "forward" / "answers.json") or {}
            ans.update({k: v for k, v in a.items()})
        for r in fw:
            a = ans.get(f"{r['id']}/{r['arm']}", {})
            r["seconds"], r["prompt_tokens"] = a.get("seconds"), a.get("prompt_tokens")
            r["done_reason"], r["task"] = a.get("done_reason"), r["id"]
        out["types"]["forward"] = {
            "arms": split(fw, metrics),
            "primary": compare_items(fw, "FILE", "PRISM", found),
            "breaks_vs_prism": compare_items(fw, "PRISM", "PRISM_BREAKS", found),
            "wrong_functions_breaks_vs_prism": compare_scores(fw, "PRISM", "PRISM_BREAKS", lambda r: float(len(r["extra"]))),
            "tiers": {tier: by_arm([r for r in fw if r["tier"] == tier], metrics) for tier in ("easy", "medium", "hard")},
            "validity": validity(fw, ("FILE", "PRISM", "PRISM_BREAKS"))}
    for kind, base, pmetric, metrics, test in (
            ("bugs", "BM25", "top5_any", {"top1": lambda r: float(r["top1"]), "top5": lambda r: float(r["top5_any"]),
                                          "recall5": lambda r: r["recall5"]}, "items"),
            ("concepts", "BM25", "top5_any", {"top1": lambda r: float(r["top1"]), "top5": lambda r: float(r["top5_any"]),
                                              "recall5": lambda r: r["recall5"]}, "items"),
            ("blast", "GREP", "recall", {"recall": lambda r: r["recall"], "recall_direct": lambda r: r["recall_direct"],
                                         "named": lambda r: r["named"],
                                         "outside_traced_and_prism": lambda r: r["outside_traced_and_prism"]}, "scores"),
            ("newfn", "FILE", "reuse", {"reuse": lambda r: r["reuse"], "invented": lambda r: r["invented"],
                                        "parses": lambda r: float(r["parses"])}, "scores"),
            ("tests", "FILE", "all_pass", {"all_pass": lambda r: float(r.get("all_pass") or False),
                                           "calls_target": lambda r: float(r.get("calls_target") or False),
                                           "coverage_vs_developer": lambda r: r.get("coverage_vs_developer"),
                                           "tests_written": lambda r: r.get("tests")}, "items")):
        rows = scores(kind)
        if kind == "tests":
            ans = {}
            for repo in REPOS:
                ans.update(_load(OUT / repo / "tests" / "answers.json") or {})
            for r in rows:
                a = ans.get(f"{r['task']}/{r['arm']}", {})
                r["seconds"], r["prompt_tokens"] = a.get("seconds"), a.get("prompt_tokens")
                r["done_reason"] = a.get("done_reason")
        if not rows:
            out["types"][kind] = {"pending": True}
            continue
        if test == "items":
            primary = compare_items(rows, base, "PRISM", lambda r: {"x": bool(r.get(pmetric))})
        else:
            primary = compare_scores(rows, base, "PRISM", lambda r: float(r.get(pmetric) or 0.0))
        entry = {"arms": split(rows, metrics), "primary": primary, "validity": validity(rows, (base, "PRISM"))}
        if not entry["validity"]["valid"]:  # the primary again, without the tasks where either version was cut off
            cut = {r["task"] for r in rows if r.get("done_reason") == "length" and r["arm"] in (base, "PRISM")}
            kept = [r for r in rows if r["task"] not in cut]
            entry["validity"]["tasks_cut"] = len(cut)
            entry["validity"]["primary_without_cut"] = (
                compare_items(kept, base, "PRISM", lambda r: {"x": bool(r.get(pmetric))}) if test == "items"
                else compare_scores(kept, base, "PRISM", lambda r: float(r.get(pmetric) or 0.0)))
        if kind == "tests":
            entry["coverage"] = compare_scores(rows, "FILE", "PRISM", lambda r: float(r.get("coverage_vs_developer") or 0.0))
        if kind in ("bugs", "concepts"):
            entry["prism_vs_tree"] = compare_items(rows, "TREE", "PRISM", lambda r: {"x": bool(r.get(pmetric))})
        if kind == "blast":
            entry["prism_vs_file"] = compare_scores(rows, "FILE", "PRISM", lambda r: float(r.get("recall") or 0.0))
        out["types"][kind] = entry
    salv = scores("tests", "scores_salvage.json")
    if salv:  # sensitivity analysis decided after the run (README, type 6): not a primary test
        tm = {"all_pass": lambda r: float(r.get("all_pass") or False),
              "calls_target": lambda r: float(r.get("calls_target") or False),
              "coverage_vs_developer": lambda r: r.get("coverage_vs_developer"), "tests_written": lambda r: r.get("tests"),
              "tests_passed": lambda r: r.get("passed")}
        out["tests_salvage"] = {
            "arms": split(salv, tm),
            "all_pass": compare_items(salv, "FILE", "PRISM", lambda r: {"x": bool(r.get("all_pass"))}),
            "coverage": compare_scores(salv, "FILE", "PRISM", lambda r: float(r.get("coverage_vs_developer") or 0.0)),
            "pass_share": compare_scores(salv, "FILE", "PRISM",
                                         lambda r: (r.get("passed") or 0) / r["ran"] if r.get("ran") else 0.0)}
    fence = scores("newfn", "scores_fence.json")
    if fence:  # sensitivity analysis decided after the run: a fence wrapping the whole code removed, every version alike
        out["newfn_outer_fence"] = {
            "arms": split(fence, {"reuse": lambda r: r["reuse"], "invented": lambda r: r["invented"],
                                  "parses": lambda r: float(r["parses"])}),
            "parses": compare_items(fence, "FILE", "PRISM", lambda r: {"x": bool(r.get("parses"))}),
            "reuse": compare_scores(fence, "FILE", "PRISM", lambda r: float(r.get("reuse") or 0.0))}
    primaries = {k: v["primary"]["p"] for k, v in out["types"].items()
                 if k != "concepts" and isinstance(v, dict) and "primary" in v}
    out["holm"] = stats.holm(primaries) if primaries else {}
    fwd = out["types"].get("forward")
    if fwd:
        b = fwd["breaks_vs_prism"]
        w = fwd["arms"]["all"]
        out["breaks_decision"] = {
            "more_callers_significant": b["p"] < 0.05 and b["b_yes"] > b["a_yes"],
            "wrong_functions_not_higher": (w["PRISM_BREAKS"]["wrong_functions"] or 0) <= (w["PRISM"]["wrong_functions"] or 0),
        }
        out["breaks_decision"]["default"] = all(out["breaks_decision"].values())
    return out


def main() -> int:
    res = analyze()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "RESULTS.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps({k: (v.get("primary", v) if isinstance(v, dict) else v) for k, v in res["types"].items()}, indent=1)[:6000])
    print("holm", res["holm"], "breaks", res.get("breaks_decision"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
