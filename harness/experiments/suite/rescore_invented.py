"""Invented calls under scorer v2 for every saved newfn answer (suite seed 42 and R1's seeds), beside v1.

    python -m harness.experiments.suite.rescore_invented     # -> reports/suite/newfn_invented_v2.json

Only the invented-call count is recomputed; reuse and every pre-registered result are untouched. The v1 counts are
the ones in the existing score files.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

from harness.experiments.suite import stats
from harness.experiments.suite.fences import strip_outer_fence
from harness.experiments.suite.invented import invented

SUITE, R1 = Path("reports/suite"), Path("reports/r1")
TASKS = Path("harness/experiments/suite/repos")


def _code(raw: str) -> str:
    try:
        got = json.loads(raw)
    except (ValueError, TypeError):
        return ""
    code = got.get("code", "") if isinstance(got, dict) else ""
    return code if isinstance(code, str) else ""


def rows() -> list[dict]:
    out = []
    for f in sorted(TASKS.glob("*/newfn.json")):
        repo = f.parent.name
        tasks = {t["id"]: t for t in json.loads(f.read_text())["tasks"]}
        v1 = {}
        for p, suite in ((SUITE / repo / "newfn/scores.json", True), (R1 / repo / "newfn/scores.json", False)):
            if p.exists():
                for k, v in json.loads(p.read_text()).items():
                    v1[k + "@42" if suite else k] = v["invented"]
        for p, suite in ((SUITE / repo / "newfn/answers.json", True), (R1 / repo / "newfn/answers.json", False)):
            if not p.exists():
                continue
            for k, a in json.loads(p.read_text()).items():
                key = k + "@42" if suite else k
                task_id, rest = key.rsplit("/", 1)
                version, seed = rest.split("@", 1)
                if seed == "42replay" or (not suite and seed == "42" and version in ("FILE", "PRISM")):
                    continue
                code = _code(a.get("raw", ""))  # strict, as the pre-registered scorer reads it
                v2 = invented(tasks[task_id], code) if code.strip() else {"invented": [], "unresolved": [], "read_as": "-"}
                out.append({"task": task_id, "repo": repo, "version": version, "seed": seed, "v1": v1.get(key),
                            "v2": len(v2["invented"]), "v2_names": v2["invented"], "unresolved": len(v2["unresolved"]),
                            "read_as": v2["read_as"]})
    return out


def compare(rs, a: str, b: str, key: str, seeds) -> dict:
    by: dict = {}
    for r in rs:
        if r["seed"] in seeds and r[key] is not None:
            by.setdefault((r["task"], r["version"]), []).append(r[key])
    tasks = sorted({t for t, v in by if v == a} & {t for t, v in by if v == b})
    xa = [statistics.mean(by[(t, a)]) for t in tasks]
    xb = [statistics.mean(by[(t, b)]) for t in tasks]
    w = stats.wilcoxon(xa, xb)
    return {"a": a, "b": b, "tasks": len(tasks), "a_mean": round(statistics.mean(xa), 3), "b_mean": round(statistics.mean(xb), 3),
            "p": w["p"] if w else 1.0, "bootstrap": stats.bootstrap_diff(xa, xb, tasks)}


def main() -> int:
    rs = rows()
    seeds = ("42", "1001", "2002")
    res = {"answers": len(rs),
           "flagged_by_v1": {v: sum(r["v1"] or 0 for r in rs if r["version"] == v) for v in ("FILE", "FILE_BM25PAD", "PRISM")},
           "flagged_by_v2": {v: sum(r["v2"] for r in rs if r["version"] == v) for v in ("FILE", "FILE_BM25PAD", "PRISM")},
           "unresolved_v2": {v: sum(r["unresolved"] for r in rs if r["version"] == v) for v in ("FILE", "FILE_BM25PAD", "PRISM")},
           "read_as": dict(Counter(r["read_as"] for r in rs)),
           "v2_names": dict(Counter(f"{r['repo']}:{n}" for r in rs for n in r["v2_names"]).most_common()),
           "suite_seed42": {k: compare(rs, "FILE", "PRISM", k, ("42",)) for k in ("v1", "v2")},
           "r1_all_seeds": {k: {"PRISM_vs_FILE_BM25PAD": compare(rs, "FILE_BM25PAD", "PRISM", k, seeds),
                                "PRISM_vs_FILE": compare(rs, "FILE", "PRISM", k, seeds)} for k in ("v1", "v2")}}
    (SUITE / "newfn_invented_v2.json").write_text(json.dumps({**res, "rows": rs}, indent=1) + "\n")
    print(json.dumps(res, indent=1)[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
