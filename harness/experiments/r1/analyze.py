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
MAX_CUT_SHARE = 0.20       # amendment A1: more cut tasks than this makes a primary invalid
TIERS = ("easy", "medium", "hard")
METRICS = {
    "forward": {"callers": lambda r: len(r["found"]) / max(1, r["changed"]), "wrong": lambda r: float(len(r["extra"])),
                "all_found": lambda r: float(not r["missed"])},
    "blast": {"recall": lambda r: r["recall"], "recall_direct": lambda r: r["recall_direct"],
              "named": lambda r: float(r["named"])},
    "newfn": {"reuse": lambda r: r["reuse"], "invented": lambda r: float(r["invented"]),
              "parses": lambda r: float(r["parses"]), "fence_parses": lambda r: float(r["fence_parses"])}}
for _k in (5, 8, 10):  # amendment A1: blast recall within the first k names of the answer
    METRICS["blast"][f"recall_at_{_k}"] = (lambda k: lambda r: sum(r["hits_in_order"][:k]) / r["gold_n"] if r["gold_n"] else 0.0)(_k)
METRICS["blast"]["precision_traced"] = lambda r: sum(r["hits_in_order"]) / len(r["hits_in_order"]) if r["hits_in_order"] else 0.0


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


def cut_tasks(rs, versions_: tuple[str, ...], seeds) -> set[str]:
    """Tasks where any of the versions was cut at the answer cap at any of the seeds (amendment A1)."""
    return {r["task"] for r in rs if r["version"] in versions_ and r["seed"] in seeds and r.get("done_reason") == "length"}


def compare(rs, a: str, b: str, metric, seeds, langs=None, only=None, exclude=frozenset()) -> dict:
    """b vs a: Wilcoxon over tasks of per-task means, bootstrap interval of b - a. `only`/`exclude`: task ids."""
    xa, xb = per_task(rs, a, metric, seeds), per_task(rs, b, metric, seeds)
    lang = {r["task"]: ("python" if r["repo"] in PY else "javascript") for r in rs}
    tasks = sorted(t for t in set(xa) & set(xb) if (langs is None or lang[t] in langs)
                   and (only is None or t in only) and t not in exclude)
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


def matched_k(rs, a: str, b: str, seeds, exclude=frozenset()) -> dict:
    """Blast: recall of both versions within the first k names, k = the shorter of the two answers (per task and
    seed), then the mean over seeds (amendment A1)."""
    by = {(r["task"], r["version"], r["seed"]): r for r in rs}
    va, vb, tasks = [], [], []
    for t in sorted({r["task"] for r in rs} - set(exclude)):
        pa, pb = [], []
        for s_ in seeds:
            ra, rb = by.get((t, a, s_)), by.get((t, b, s_))
            if ra is None or rb is None:
                break
            k = min(len(ra["hits_in_order"]), len(rb["hits_in_order"]))
            pa.append(sum(ra["hits_in_order"][:k]) / ra["gold_n"] if ra["gold_n"] else 0.0)
            pb.append(sum(rb["hits_in_order"][:k]) / rb["gold_n"] if rb["gold_n"] else 0.0)
        else:
            va.append(statistics.mean(pa)); vb.append(statistics.mean(pb)); tasks.append(t)
    w = stats.wilcoxon(va, vb)
    return {"a": a, "b": b, "tasks": len(tasks), "a_mean": round(statistics.mean(va), 3) if va else None,
            "b_mean": round(statistics.mean(vb), 3) if vb else None, "p": w["p"] if w else 1.0,
            "bootstrap": stats.bootstrap_diff(va, vb, tasks)}


def hidden(kind: str) -> dict[str, set[str]]:
    """Amendment A2: per task, the answer items the padded version's prompt does not show. Computed from the frozen
    prompts and the answer key only. forward: changed callers whose first code line is not in the FILE_GREPPAD prompt.
    blast: traced callers that are not one of GREP_PAD's shown functions. newfn: the developer's helpers neither
    defined nor named in the FILE_BM25PAD prompt."""
    import re
    from harness.experiments.r1.run import pads_of, prompt
    out: dict[str, set[str]] = {}
    for repo in repos_of(kind):
        pads = pads_of(kind, repo)
        for t in tasks_of(kind, repo):
            if kind == "forward":
                p = prompt(kind, t, "FILE_GREPPAD", pads)
                first = lambda code: next((ln.strip() for ln in code.splitlines() if ln.strip()), "")
                out[t["id"]] = {c["qname"] for c in t["changed_callers"] if first(c["old"]) not in p}
            elif kind == "blast":
                shown = set(pads[t["id"]]["GREP_PAD"]["items"]) | {pads[t["id"]]["GREP_PAD"].get("partial")}
                out[t["id"]] = {g for g in t["gold"] if g not in shown}
            else:
                p = prompt(kind, t, "FILE_BM25PAD", pads)
                out[t["id"]] = {h for h in t["existing_helpers"]
                                if not re.search(rf"(def|function|class)\s+{re.escape(h)}\b|`[\w.]*\b{re.escape(h)}`", p)}
    return out


def hidden_metric(kind: str, hid: dict[str, set[str]]):
    if kind == "forward":
        return lambda r: len(hid[r["task"]] & set(r["found"])) / len(hid[r["task"]])
    if kind == "blast":
        return lambda r: len(hid[r["task"]] & set(r["found"])) / len(hid[r["task"]])
    return lambda r: len(hid[r["task"]] & set(r.get("helpers_used", []))) / len(hid[r["task"]])


def label(kind: str, e: dict, p_adj: float) -> str:
    """Amendment A3: the one outcome a type gets, decided by the pre-registered rule alone."""
    d, n_all = e["primary"]["bootstrap"], e["validity"]["tasks"]
    if n_all and e["validity"]["tasks_cut"] / n_all > MAX_CUT_SHARE:
        return "invalid"
    if p_adj < 0.05 and d["high"] < 0:
        return "padding_better"
    if p_adj >= 0.05 or d["low"] <= 0:
        return "not_shown"
    if d["mean"] < FLOOR[kind]:
        return "below_floor"
    if kind == "newfn" and e["secondary"]["invented_PRISM_vs_pad"]["bootstrap"]["mean"] > INVENTED_MARGIN:
        return "fails_invented_calls"
    return "pass"


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
        cut = cut_tasks(rs, (pad, "PRISM"), seeds)
        entry = {"seeds": seeds,
                 "means": {v: {name: mean_of(rs, v, f, seeds) for name, f in m.items()} for v in versions(kind)},
                 "primary": compare(rs, pad, "PRISM", m[key], seeds, exclude=cut),
                 "primary_all_tasks": compare(rs, pad, "PRISM", m[key], seeds),
                 "validity": {**validity(rs, pad, "PRISM", seeds), "tasks_cut": len(cut),
                              "tasks": sum(len(tasks_of(kind, r)) for r in repos_of(kind))},
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
            entry["secondary"]["wrong_PRISM_vs_pad"] = compare(rs, pad, "PRISM", m["wrong"], seeds, exclude=cut)
            tier_of = {r["task"]: r.get("tier") for r in rs}
            entry["by_tier"] = {tier: {v: compare(rs, v, "PRISM", m[key], seeds, exclude=cut,
                                                  only={t for t, x in tier_of.items() if x == tier})
                                       for v in versions(kind) if v != "PRISM"} for tier in TIERS}
        if kind == "blast":
            entry["recall_at_k"] = {f"k{k}": compare(rs, pad, "PRISM", m[f"recall_at_{k}"], seeds, exclude=cut)
                                    for k in (5, 8, 10)}
            entry["recall_matched_k"] = matched_k(rs, pad, "PRISM", seeds, exclude=cut)
            entry["precision_traced"] = {v: mean_of([r for r in rs if r["task"] not in cut], v, m["precision_traced"], seeds)
                                         for v in versions(kind)}
        hid = hidden(kind)
        with_hidden = {t for t, h in hid.items() if h}
        entry["hidden_from_pad"] = {"items": sum(len(h) for h in hid.values()), "tasks": len(with_hidden),
                                    "comparison": compare(rs, pad, "PRISM", hidden_metric(kind, hid), seeds,
                                                          only=with_hidden, exclude=cut)}
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
        e["decision"] = {**checks, "prism_context_helps": all(checks.values()), "p_holm": p_adj,
                         "label": label(kind, e, p_adj)}
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
