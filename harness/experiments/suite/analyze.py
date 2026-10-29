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


def scores(kind: str) -> list[dict]:
    rows = []
    for repo in REPOS:
        s = _load(OUT / repo / kind / "scores.json")
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
            r["task"] = r["id"]
        out["types"]["forward"] = {
            "arms": split(fw, metrics),
            "primary": compare_items(fw, "FILE", "PRISM", found),
            "breaks_vs_prism": compare_items(fw, "PRISM", "PRISM_BREAKS", found),
            "wrong_functions_breaks_vs_prism": compare_scores(fw, "PRISM", "PRISM_BREAKS", lambda r: float(len(r["extra"]))),
            "tiers": {tier: by_arm([r for r in fw if r["tier"] == tier], metrics) for tier in ("easy", "medium", "hard")}}
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
        if not rows:
            out["types"][kind] = {"pending": True}
            continue
        if test == "items":
            primary = compare_items(rows, base, "PRISM", lambda r: {"x": bool(r.get(pmetric))})
        else:
            primary = compare_scores(rows, base, "PRISM", lambda r: float(r.get(pmetric) or 0.0))
        entry = {"arms": split(rows, metrics), "primary": primary}
        if kind == "tests":
            entry["coverage"] = compare_scores(rows, "FILE", "PRISM", lambda r: float(r.get("coverage_vs_developer") or 0.0))
        if kind in ("bugs", "concepts"):
            entry["prism_vs_tree"] = compare_items(rows, "TREE", "PRISM", lambda r: {"x": bool(r.get(pmetric))})
        if kind == "blast":
            entry["prism_vs_file"] = compare_scores(rows, "FILE", "PRISM", lambda r: float(r.get("recall") or 0.0))
        out["types"][kind] = entry
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
