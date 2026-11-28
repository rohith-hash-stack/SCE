"""R2 analysis (A6; R2-1 seeds; R3-3 pruning rule; amendment R2-5 fixes the implementation details below).

    python -m harness.experiments.r2.analyze            # -> reports/r2/RESULTS.json

Measure (A6, forward): the share of the changed callers the answer names and updates (`found / changed`), per task
the mean over seeds 42 and 1001. A task counts only when both seeds answered. Secondary: wrong functions per task,
all callers found.

Truncation (A1): for each comparison, a task is excluded when either version's answer was cut at the answer cap at
any seed. More than 20% excluded makes that comparison `invalid`. The all-tasks result is reported beside it.

Removal effect of version V: drop = FULL - V per task (positive: the section helps).

Development hypotheses (A6), one Holm family, alpha 0.05:
- R2-H4: drop(NO4), Wilcoxon two-sided; label as A3 with floor 0.05 (`removal_better` is A3's `padding_better`).
- R2-H6, R2-H7: TOST of drop(NO6), drop(NO7) within +-0.03: two one-sided paired t-tests, p = the larger.
  `equivalent` if the adjusted p < 0.05.
- R2-H47: NO47 below both NO4 and NO7: p = the larger of the two Wilcoxon p (NO47 vs NO4, NO47 vs NO7). `pass` if
  the adjusted p < 0.05 and both bootstrap intervals are above 0.

Sections 4, 6, 7 (R3-3; the pruning decision):
- directional: Wilcoxon of drop(N<s>), Holm across the removal arms (R3-4: four);
- equivalence: TOST of drop(N<s>), Holm across the removal arms (R3-4: four);
- `load_bearing` (A6): directional adjusted p < 0.05, interval above 0, drop >= 0.05 on development, and the held-out
  mean drop > 0;
- `dead_weight` (R3-3a): equivalence adjusted p < 0.05 on development, TOST p < 0.05 on held-out, and TOST
  p < 0.05 at each seed on both sets;
- `harmful` (R3-3b): directional adjusted p < 0.05 with the interval below 0 on development, the mean drop < 0 at
  each seed on development, and the held-out mean drop < 0;
- `prune` = dead_weight or harmful. Sections not removed in R2 (1, 2, 3, 5, 8) are never pruned.
- R3-4 (redundancy): the directional and equivalence Holm families hold four arms: sections 4, 6, 7 and the pair 4+7
  (NO47). Section 4 or 7 meets dead_weight (or harmful) only if the pair NO47 also meets dead_weight (or harmful),
  by the same criteria. The single-arm results are kept as `dead_weight_single` and `harmful_single`.

Per seed: every comparison is also reported at each seed; `unstable` when the mean drop has opposite signs at the
two seeds. Model-free measures (from the pads) come first and are never pooled with the model results.
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

from scipy.stats import ttest_1samp

from harness.experiments.r2.build import PADS, REMOVED, SPLITS, VERSIONS
from harness.experiments.suite import stats

OUT = Path("reports/r2")
SEEDS = ("42", "1001")
MARGIN = 0.03
FLOOR = 0.05
ALPHA = 0.05
MAX_CUT_SHARE = 0.20
SECTIONS = {4: "NO4", 6: "NO6", 7: "NO7"}
PAIR, PAIR_MEMBERS = "4+7", (4, 7)          # amendment R3-4: the redundant pair, tested with the sections
ARMS = {**SECTIONS, PAIR: "NO47"}
METRICS = {"callers": lambda r: len(r["found"]) / max(1, r["changed"]),
           "wrong": lambda r: float(len(r["extra"])),
           "all_found": lambda r: float(not r["missed"])}


def rows(split: str) -> list[dict]:
    out = []
    for f in sorted((OUT / split).glob("*/scores.json")):
        out += json.loads(f.read_text()).values()
    return out


def per_task(rs, version, metric, seeds) -> dict[str, float]:
    vals: dict[str, list[float]] = {}
    for r in rs:
        if r["version"] == version and r["seed"] in seeds:
            vals.setdefault(r["task"], []).append(METRICS[metric](r))
    return {t: statistics.mean(v) for t, v in vals.items() if len(v) == len(seeds)}


def cut(rs, versions, seeds) -> set[str]:
    return {r["task"] for r in rs if r["version"] in versions and r["seed"] in seeds and r.get("done_reason") == "length"}


def tost(diffs: list[float], margin: float = MARGIN) -> dict:
    """Paired TOST: H0 |mean diff| >= margin. p = max of the two one-sided t-tests; with a 90% bootstrap interval."""
    if not diffs:
        return {"p": None, "mean": None, "low90": None, "high90": None, "n": 0}
    m = statistics.mean(diffs)
    if len(diffs) < 2 or statistics.pstdev(diffs) == 0:
        p = 0.0 if abs(m) < margin else 1.0
    else:
        p_low = float(ttest_1samp(diffs, -margin, alternative="greater").pvalue)
        p_high = float(ttest_1samp(diffs, margin, alternative="less").pvalue)
        p = max(p_low, p_high)
    rng = random.Random(stats.SEED)
    boots = sorted(sum(rng.choice(diffs) for _ in diffs) / len(diffs) for _ in range(stats.RESAMPLES))
    return {"p": p, "mean": m, "low90": boots[int(0.05 * len(boots))], "high90": boots[int(0.95 * len(boots)) - 1],
            "n": len(diffs)}


def compare(rs, version, metric="callers", seeds=SEEDS, exclude=frozenset(), langs=None, lang_key="language") -> dict:
    """drop = FULL - version over tasks: Wilcoxon, 95% bootstrap interval, TOST."""
    full, other = per_task(rs, "FULL", metric, seeds), per_task(rs, version, metric, seeds)
    lang = {r["task"]: r.get(lang_key) for r in rs}
    tasks = sorted(t for t in set(full) & set(other) if t not in exclude and (langs is None or lang[t] in langs))
    if not tasks:
        return {"version": version, "tasks": 0, "p": None, "bootstrap": None, "tost": tost([])}
    a, b = [other[t] for t in tasks], [full[t] for t in tasks]
    w = stats.wilcoxon(a, b)
    return {"version": version, "tasks": len(tasks), "full_mean": round(statistics.mean(b), 4),
            "removed_mean": round(statistics.mean(a), 4), "p": w["p"] if w else 1.0,
            "bootstrap": stats.bootstrap_diff(a, b, tasks), "tost": tost([y - x for x, y in zip(a, b)])}


def pair(rs, x: str, y: str, seeds=SEEDS, exclude=frozenset()) -> dict:
    """y - x over tasks (used for R2-H47: NO4 - NO47 and NO7 - NO47)."""
    px, py = per_task(rs, x, "callers", seeds), per_task(rs, y, "callers", seeds)
    tasks = sorted(t for t in set(px) & set(py) if t not in exclude)
    if not tasks:
        return {"tasks": 0, "p": None, "bootstrap": None}
    a, b = [px[t] for t in tasks], [py[t] for t in tasks]
    w = stats.wilcoxon(a, b)
    return {"tasks": len(tasks), "p": w["p"] if w else 1.0, "bootstrap": stats.bootstrap_diff(a, b, tasks)}


def validity(rs, versions, seeds=SEEDS) -> dict:
    tasks = {r["task"] for r in rs}
    c = cut(rs, versions, seeds)
    return {"tasks": len(tasks), "tasks_cut": len(c), "invalid": bool(tasks) and len(c) / len(tasks) > MAX_CUT_SHARE,
            "cut": sorted(c)}


def label_directional(e: dict, p_adj: float, invalid: bool) -> str:
    d = e["bootstrap"]
    if invalid:
        return "invalid"
    if d is None:
        return "not_shown"
    if p_adj < ALPHA and d["high"] < 0:
        return "removal_better"
    if p_adj >= ALPHA or d["low"] <= 0:
        return "not_shown"
    if d["mean"] < FLOOR:
        return "below_floor"
    return "pass"


def model_free(split: str) -> dict:
    recs = []
    for f in sorted((PADS / split).glob("*.json")):
        recs += [r["measures"] for r in json.loads(f.read_text())["tasks"].values()]
    tot = sum(m["changed_callers"] for m in recs) or 1
    out = {"tasks": len(recs), "changed_callers": tot,
           "share_listed_anywhere": round(sum(m["listed_anywhere"] for m in recs) / tot, 3),
           "share_listed_in_section": {s: round(sum(m["listed_in_section"].get(str(s), 0) for m in recs) / tot, 3)
                                       for s in range(1, 9)}}
    for v in REMOVED:
        out[v] = {"share_listed_in_context": round(sum(m[f"{v}_listed_in_context"] for m in recs) / tot, 3),
                  "share_readded_by_pad": round(sum(m[f"{v}_readded_by_pad"] for m in recs) / tot, 3)}
    return out


def analyze() -> dict:
    res: dict = {"model_free": {s: model_free(s) for s in SPLITS}, "splits": {}}
    by = {s: rows(s) for s in SPLITS}
    for split, rs in by.items():
        sp = {"versions": {}}
        for v in REMOVED:
            val = validity(rs, ("FULL", v))
            excl = frozenset(val["cut"])
            sp["versions"][v] = {
                "validity": val, "primary": compare(rs, v, exclude=excl), "all_tasks": compare(rs, v),
                "per_seed": {s: compare(rs, v, seeds=(s,), exclude=frozenset(cut(rs, ("FULL", v), (s,))))
                             for s in SEEDS},
                "per_language": {lg: compare(rs, v, exclude=excl, langs={lg}) for lg in ("python", "javascript", "typescript")},
                "per_file_language": {lg: compare(rs, v, exclude=excl, langs={lg}, lang_key="file_language")
                                      for lg in ("python", "javascript", "typescript")},
                "wrong": compare(rs, v, metric="wrong", exclude=excl),
                "all_found": compare(rs, v, metric="all_found", exclude=excl)}
            seeds = sp["versions"][v]["per_seed"]
            m = [seeds[s]["bootstrap"]["mean"] for s in SEEDS if seeds[s]["bootstrap"]]
            sp["versions"][v]["unstable"] = len(m) == 2 and m[0] * m[1] < 0
        res["splits"][split] = sp

    dev, held = res["splits"]["dev"]["versions"], res["splits"]["heldout"]["versions"]
    rs = by["dev"]
    excl47 = frozenset(validity(rs, ("NO4", "NO7", "NO47"))["cut"])
    h47 = [pair(rs, "NO47", "NO4", exclude=excl47), pair(rs, "NO47", "NO7", exclude=excl47)]
    family = {"R2-H4": dev["NO4"]["primary"]["p"], "R2-H6": dev["NO6"]["primary"]["tost"]["p"],
              "R2-H7": dev["NO7"]["primary"]["tost"]["p"],
              "R2-H47": max((x["p"] for x in h47 if x["p"] is not None), default=None)}
    adj = stats.holm({k: v for k, v in family.items() if v is not None})
    hyp = {"R2-H4": {"p": family["R2-H4"], "p_holm": adj.get("R2-H4"),
                     "label": label_directional(dev["NO4"]["primary"], adj.get("R2-H4", 1.0),
                                                dev["NO4"]["validity"]["invalid"])}}
    for h, v in (("R2-H6", "NO6"), ("R2-H7", "NO7")):
        hyp[h] = {"p": family[h], "p_holm": adj.get(h),
                  "label": "invalid" if dev[v]["validity"]["invalid"] else
                  ("equivalent" if adj.get(h, 1.0) < ALPHA else "equivalence_not_shown")}
    ok47 = all(x["bootstrap"] and x["bootstrap"]["low"] > 0 for x in h47)
    hyp["R2-H47"] = {"p": family["R2-H47"], "p_holm": adj.get("R2-H47"), "parts": h47,
                     "label": "pass" if adj.get("R2-H47", 1.0) < ALPHA and ok47 else "not_shown"}
    res["hypotheses"] = hyp

    # R3-3 criteria for each removal arm; Holm across the four arms (R3-4: the pair 4+7 is tested with the sections)
    direc = stats.holm({s: dev[v]["primary"]["p"] for s, v in ARMS.items() if dev[v]["primary"]["p"] is not None})
    equiv = stats.holm({s: dev[v]["primary"]["tost"]["p"] for s, v in ARMS.items()
                        if dev[v]["primary"]["tost"]["p"] is not None})
    arms = {}
    for s, v in ARMS.items():
        d, h = dev[v], held[v]
        db, hb = d["primary"]["bootstrap"], h["primary"]["bootstrap"]
        held_mean = hb["mean"] if hb else None
        seed_tost = [x[v]["per_seed"][sd]["tost"]["p"] for x in (dev, held) for sd in SEEDS]
        seed_dev_means = [d["per_seed"][sd]["bootstrap"]["mean"] if d["per_seed"][sd]["bootstrap"] else None for sd in SEEDS]
        load = bool(db) and direc.get(s, 1.0) < ALPHA and db["low"] > 0 and db["mean"] >= FLOOR \
            and held_mean is not None and held_mean > 0
        hp = h["primary"]["tost"]["p"]
        dead = equiv.get(s, 1.0) < ALPHA and hp is not None and hp < ALPHA \
            and all(p is not None and p < ALPHA for p in seed_tost)
        harm = bool(db) and direc.get(s, 1.0) < ALPHA and db["high"] < 0 \
            and all(m is not None and m < 0 for m in seed_dev_means) and held_mean is not None and held_mean < 0
        invalid = d["validity"]["invalid"] or h["validity"]["invalid"]
        arms[s] = {"version": v, "p_directional_holm": direc.get(s), "p_equivalence_holm": equiv.get(s),
                   "heldout_mean_drop": held_mean, "heldout_tost_p": h["primary"]["tost"]["p"],
                   "seed_tost_p": seed_tost, "dev_seed_mean_drop": seed_dev_means,
                   "load_bearing": load and not invalid, "dead_weight": dead and not invalid,
                   "harmful": harm and not invalid, "invalid": invalid,
                   "unstable": d["unstable"] or h["unstable"]}
    pair_arm = arms[PAIR]
    sections = {}
    for s in SECTIONS:
        x = dict(arms[s])
        if s in PAIR_MEMBERS:  # R3-4: a member of the redundant pair is pruned only if the pair meets the same criterion
            x["dead_weight_single"], x["harmful_single"] = x["dead_weight"], x["harmful"]
            x["dead_weight"] = x["dead_weight"] and pair_arm["dead_weight"]
            x["harmful"] = x["harmful"] and pair_arm["harmful"]
        x["prune"] = x["dead_weight"] or x["harmful"]
        sections[s] = x
    res["pair_4_7"] = pair_arm
    res["sections"] = sections
    res["prune"] = sorted(s for s, x in sections.items() if x["prune"])
    return res


def main() -> int:
    res = analyze()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "RESULTS.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({"hypotheses": {k: v["label"] for k, v in res["hypotheses"].items()},
                      "sections": {k: {x: v[x] for x in ("load_bearing", "dead_weight", "harmful", "prune")}
                                   for k, v in res["sections"].items()}, "prune": res["prune"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
