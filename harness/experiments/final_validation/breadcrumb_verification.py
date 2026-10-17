"""Verification of the breadcrumb patch (f55de74) on the Stage 2 debug
tasks whose breadcrumb payload it changed, against the frozen Stage 2
cells (`reports/two_stage/stage2/`).

Selection (`select`, from the committed static audit
`reports/two_stage/breadcrumb_audit/breadcrumb_audit.json`):
- `prism_oracle`: every task whose gold slice gets a non-empty breadcrumb
  under the new rule, plus `django_t02_016_db_cursor_connection` (now
  none - its prompt equals `normal_oracle`'s byte for byte). Baseline: the
  frozen `pragmatic_oracle` (= `normal_oracle`) cells.
- `prism_ask`: every task whose `prism_ask` breadcrumbs changed (added,
  removed or rewritten). Baseline: the frozen `prism_ask` cells.

Pre-registered criteria (`analyse`):
1. no task's success count drops by 3 or more (of 10) against its baseline;
2. the mean per-task change in TSR is >= -0.01;
3. `django_t02_016` scores 10/10 under `prism_oracle`.
Identical prompts varied by up to 1/10 between Stage 2 arms (3 of 78
tasks), so criterion 3 is reported together with that noise floor.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

REGRESSION_TASK = ("django", "django_t02_016_db_cursor_connection")
BASELINE = {"prism_oracle": "pragmatic_oracle", "prism_ask": "prism_ask"}
MAX_DROP = 3
MIN_MEAN_DELTA = -0.01


def select(audit_path: Path) -> dict[str, dict[str, list[str]]]:
    """`{arm: {repo: [task ids]}}` from the static audit."""
    audit = json.loads(Path(audit_path).read_text())
    out: dict[str, dict[str, list[str]]] = {"prism_oracle": defaultdict(list), "prism_ask": defaultdict(list)}
    for corpus in audit["corpora"]:
        for row in corpus["per_task"]:
            if row["arm"] == "pragmatic_oracle" and row["new_fires"]:
                out["prism_oracle"][corpus["repo"]].append(row["task_id"])
            elif row["arm"] == "prism_ask" and (row["old_fires"] or row["new_fires"]):
                out["prism_ask"][corpus["repo"]].append(row["task_id"])
    repo, task = REGRESSION_TASK
    if task not in out["prism_oracle"][repo]:
        out["prism_oracle"][repo].append(task)
    return {arm: {r: sorted(ts) for r, ts in sorted(per.items())} for arm, per in out.items()}


def _cells(root: Path, repo: str) -> list[dict]:
    path = Path(root) / repo / "cells.jsonl"
    if not path.exists():
        return []
    last = {}
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            last[(r["task_id"], r["seed"], r["engine_id"], r.get("budget"))] = r
    return list(last.values())


def _score(cells: list[dict], engine: str, task: str) -> dict:
    ok = [c for c in cells if c["engine_id"] == engine and c["task_id"] == task and c["status"] == "ok"
          and c.get("tsr") is not None]
    errors = sum(1 for c in cells if c["engine_id"] == engine and c["task_id"] == task and c["status"] != "ok")
    return {"n": len(ok), "successes": sum(int(c["tsr"]) for c in ok), "errors": errors,
            "mean": sum(c["tsr"] for c in ok) / len(ok) if ok else None,
            "breadcrumb_tokens": sum(c.get("breadcrumb_tokens", 0) for c in ok) / len(ok) if ok else None}


def analyse(new_root: Path, old_root: Path, selection: dict) -> dict:
    """`new_root/<arm>/<repo>/cells.jsonl` (this run) against
    `old_root/<repo>/cells.jsonl` (the frozen Stage 2 cells)."""
    rows = []
    for arm, per_repo in selection.items():
        for repo, tasks in per_repo.items():
            new_cells, old_cells = _cells(Path(new_root) / arm, repo), _cells(old_root, repo)
            for task in tasks:
                new = _score(new_cells, arm, task)
                base = _score(old_cells, BASELINE[arm], task)
                old_arm = _score(old_cells, arm, task) if arm != BASELINE[arm] else base
                delta = new["mean"] - base["mean"] if new["mean"] is not None and base["mean"] is not None else None
                drop = base["successes"] - new["successes"] if new["n"] and base["n"] else None
                rows.append({"arm": arm, "repo": repo, "task_id": task, "new": new, "baseline": base,
                             "old_arm": old_arm, "delta": delta, "drop": drop})
    complete = [r for r in rows if r["delta"] is not None]
    mean_delta = sum(r["delta"] for r in complete) / len(complete) if complete else None
    worst = max((r["drop"] for r in complete if r["drop"] is not None), default=None)
    reg = next((r for r in rows if (r["repo"], r["task_id"]) == REGRESSION_TASK and r["arm"] == "prism_oracle"), None)
    criteria = {
        "no_task_drops_3_or_more": worst is not None and worst < MAX_DROP,
        "mean_delta_at_least_-0.01": mean_delta is not None and mean_delta >= MIN_MEAN_DELTA,
        "django_t02_016_recovers_to_10_of_10": bool(reg and reg["new"]["n"] == 10 and reg["new"]["successes"] == 10),
    }
    expected = sum(len(ts) for per in selection.values() for ts in per.values())
    return {"rows": rows, "mean_delta": mean_delta, "worst_drop": worst, "criteria": criteria,
            "complete_tasks": len(complete), "expected_tasks": expected,
            "all_complete": len(complete) == expected and all(r["new"]["n"] == 10 for r in rows),
            "passed": all(criteria.values())}


def render(result: dict, model: str, code: str) -> str:
    def frac(s):
        return f"{s['successes']}/{s['n']}" if s["n"] else "-"

    c = result["criteria"]
    mark = {True: "PASS", False: "FAIL"}
    lines = ["# Breadcrumb verification", "",
             f"Model `{model}`, code `{code}`. New cells: this run; baselines: the frozen Stage 2 cells "
             "(`prism_oracle` against `normal_oracle` = `pragmatic_oracle`; `prism_ask` against the old `prism_ask`). "
             "TSR is the causal-trace success rate (`score_debug_causal`).", "",
             "## Pre-registered criteria", "",
             f"- {mark[c['no_task_drops_3_or_more']]} - no task drops by >= 3/10 (worst drop: {result['worst_drop']})",
             f"- {mark[c['mean_delta_at_least_-0.01']]} - mean per-task delta TSR >= -0.01 "
             f"(observed {result['mean_delta']:+.3f})" if result["mean_delta"] is not None else
             "- FAIL - mean per-task delta TSR: no complete task",
             f"- {mark[c['django_t02_016_recovers_to_10_of_10']]} - django_t02_016 recovers to 10/10 under prism_oracle "
             "(its prompt is now byte-identical to normal_oracle's; identical prompts varied by up to 1/10 in Stage 2)",
             "", f"**Overall: {'PASS' if result['passed'] else 'FAIL'}**"
             + ("" if result["all_complete"] else f" (incomplete: {result['complete_tasks']}/{result['expected_tasks']} tasks scored, "
                "or a task has fewer than 10 scored seeds)"), ""]
    for arm in ("prism_oracle", "prism_ask"):
        sub = [r for r in result["rows"] if r["arm"] == arm]
        if not sub:
            continue
        oracle = arm == "prism_oracle"
        head = "| task | normal_oracle | old prism_oracle |" if oracle else "| task | old prism_ask |"
        lines += [f"## {arm} ({len(sub)} tasks)", "",
                  f"{head} new {arm} | delta TSR | new breadcrumb tokens | errors |",
                  "|---|---|---|---|---|---|---|" if oracle else "|---|---|---|---|---|---|"]
        for r in sub:
            d = f"{r['delta']:+.2f}" if r["delta"] is not None else "-"
            tok = f"{r['new']['breadcrumb_tokens']:.0f}" if r["new"]["breadcrumb_tokens"] is not None else "-"
            before = f"{frac(r['baseline'])} | {frac(r['old_arm'])}" if oracle else frac(r["baseline"])
            lines.append(f"| {r['task_id']} | {before} | {frac(r['new'])} | {d} | {tok} | {r['new']['errors']} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("--audit", required=True)
    a = sub.add_parser("analyse")
    a.add_argument("--audit", required=True)
    a.add_argument("--new", required=True)
    a.add_argument("--old", required=True)
    a.add_argument("--model", required=True)
    a.add_argument("--code", default="")
    a.add_argument("--out", required=True)
    args = p.parse_args(argv)
    selection = select(Path(args.audit))
    if args.cmd == "select":
        print(json.dumps(selection))
        return 0
    result = analyse(Path(args.new), Path(args.old), selection)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "verification.json").write_text(json.dumps({"selection": selection, **result}, indent=1))
    md = render(result, args.model, args.code)
    (out / "VERIFICATION.md").write_text(md)
    print(md)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
