"""R1's pre-registered analysis (README, "Tests and decisions").

    python -m harness.experiments.r1.analyze          # -> reports/r1/RESULTS.json
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

from harness.experiments.r1.run import KINDS, SEEDS, repos_of, tasks_of, versions
from harness.experiments.suite import stats
from harness.experiments.suite.analyze import MAX_TRUNCATED, MAX_TRUNCATED_GAP, PY

OUT = Path("reports/r1")
PRIMARY = {"forward": ("FILE_GREPPAD", "callers"), "blast": ("GREP_PAD", "recall"), "newfn": ("FILE_BM25PAD", "reuse")}
FLOOR = {"forward": 0.10, "blast": 0.05, "newfn": 0.05}
INVENTED_MARGIN = 0.05
METRICS = {
    "forward": {"callers": lambda r: len(r["found"]) / max(1, r["changed"]), "wrong": lambda r: float(len(r["extra"])),
                "all_found": lambda r: float(not r["missed"])},
    "blast": {"recall": lambda r: r["recall"], "recall_direct": lambda r: r["recall_direct"],
              "named": lambda r: float(r["named"])},
    "newfn": {"reuse": lambda r: r["reuse"], "invented": lambda r: float(r["invented"]),
              "parses": lambda r: float(r["parses"]), "fence_parses": lambda r: float(r["fence_parses"])}}


def rows(kind: str) -> list[dict]:
    out = []
    for repo in repos_of(kind):
        try:
            out += json.loads((OUT / repo / kind / "scores.json").read_text()).values()
        except (OSError, ValueError):
            continue
    return out


def complete_seeds(kind: str, rs: list[dict]) -> list[str]:
    """The seeds at which every version answered every task."""
    n = sum(len(tasks_of(kind, r)) for r in repos_of(kind))
    count: dict[tuple[str, str], int] = {}
    for r in rs:
        count[(r["version"], r["seed"])] = count.get((r["version"], r["seed"]), 0) + 1
    return [str(s) for s in SEEDS if all(count.get((v, str(s)), 0) >= n for v in versions(kind))]


def mean_of(rs, version: str, metric, seeds) -> float | None:
    vals = list(per_task(rs, version, metric, seeds).values())
    return round(statistics.mean(vals), 3) if vals else None


def per_task(rs: list[dict], version: str, metric, seeds: list[str]) -> dict[str, float]:
    vals: dict[str, list[float]] = {}
    for r in rs:
        if r["version"] == version and r["seed"] in seeds:
            vals.setdefault(r["task"], []).append(metric(r))
    return {t: statistics.mean(v) for t, v in vals.items() if len(v) == len(seeds)}


def compare(rs, a: str, b: str, metric, seeds, langs=None) -> dict:
    """b vs a: Wilcoxon over tasks of per-task means, bootstrap interval of b - a."""
    xa, xb = per_task(rs, a, metric, seeds), per_task(rs, b, metric, seeds)
    lang = {r["task"]: ("python" if r["repo"] in PY else "javascript") for r in rs}
    tasks = sorted(t for t in set(xa) & set(xb) if langs is None or lang[t] in langs)
    va, vb = [xa[t] for t in tasks], [xb[t] for t in tasks]
    w = stats.wilcoxon(va, vb)
    boot = stats.bootstrap_diff(va, vb, tasks)
    return {"a": a, "b": b, "tasks": len(tasks), "a_mean": round(statistics.mean(va), 3) if va else None,
            "b_mean": round(statistics.mean(vb), 3) if vb else None, "p": w["p"] if w else 1.0, "bootstrap": boot}


def validity(rs, a: str, b: str, seeds) -> dict:
    per = {}
    for v in (a, b):
        sel = [r for r in rs if r["version"] == v and r["seed"] in seeds]
        per[v] = round(sum(r.get("done_reason") == "length" for r in sel) / len(sel), 3) if sel else None
    rates = [x for x in per.values() if x is not None]
    return {"truncated": per, "valid": bool(rates) and max(rates) <= MAX_TRUNCATED
            and max(rates) - min(rates) <= MAX_TRUNCATED_GAP}


def determinism(kind: str, rs: list[dict]) -> dict:
    by = {(r["task"], r["version"], r["seed"]): r for r in rs}
    pairs = [(by[(t, v, "42")], r) for (t, v, s), r in by.items() if s == "42replay" and (t, v, "42") in by]
    return {"pairs": len(pairs), "identical": sum(a["raw_sha"] == b["raw_sha"] for a, b in pairs)}


def analyze() -> dict:
    out: dict = {"types": {}}
    primaries = {}
    for kind in KINDS:
        rs = rows(kind)
        if not rs:
            out["types"][kind] = {"pending": True}
            continue
        seeds = complete_seeds(kind, rs)
        if not seeds:
            out["types"][kind] = {"pending": True, "answers": len(rs)}
            continue
        pad, key = PRIMARY[kind]
        m = METRICS[kind]
        entry = {"seeds": seeds,
                 "means": {v: {name: mean_of(rs, v, f, seeds) for name, f in m.items()} for v in versions(kind)},
                 "primary": compare(rs, pad, "PRISM", m[key], seeds),
                 "validity": validity(rs, pad, "PRISM", seeds),
                 "determinism": determinism(kind, rs),
                 "per_seed": {s: compare(rs, pad, "PRISM", m[key], [s]) for s in seeds},
                 "by_language": {lang: compare(rs, pad, "PRISM", m[key], seeds, {lang}) for lang in ("python", "javascript")},
                 "secondary": {}}
        base = versions(kind)[0]
        for v in versions(kind)[2:]:
            entry["secondary"][f"{v}_vs_{base}"] = compare(rs, base, v, m[key], seeds)
            if v != pad:
                entry["secondary"][f"PRISM_vs_{v}"] = compare(rs, v, "PRISM", m[key], seeds)
        if kind == "newfn":
            entry["secondary"]["invented_PRISM_vs_pad"] = compare(rs, pad, "PRISM", m["invented"], seeds)
        if kind == "forward":
            entry["secondary"]["wrong_PRISM_vs_pad"] = compare(rs, pad, "PRISM", m["wrong"], seeds)
        out["types"][kind] = entry
        primaries[kind] = entry["primary"]["p"]
    out["holm"] = stats.holm(primaries) if primaries else {}
    for kind, p_adj in out["holm"].items():
        e = out["types"][kind]
        diff = e["primary"]["bootstrap"]
        checks = {"significant": p_adj < 0.05, "interval_above_zero": diff["low"] > 0,
                  "at_least_floor": diff["mean"] >= FLOOR[kind]}
        if kind == "newfn":
            inv = e["secondary"]["invented_PRISM_vs_pad"]["bootstrap"]["mean"]
            checks["invented_within_margin"] = inv <= INVENTED_MARGIN
        e["decision"] = {**checks, "prism_context_helps": all(checks.values()), "p_holm": p_adj}
    return out


def main() -> int:
    res = analyze()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "RESULTS.json").write_text(json.dumps(res, indent=1) + "\n")
    for kind, e in res["types"].items():
        print(kind, json.dumps(e.get("decision") or e)[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())
