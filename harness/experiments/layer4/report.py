"""Layer 4 tournament report: merge per-corpus cell files, compute the
contamination flags and the summary, and write TOURNAMENT.md.

    PYTHONPATH=.:src python -m harness.experiments.layer4.report \\
        --out reports/layer4/tournament [--merge reports/layer4/tournament/corpora]

`--merge` concatenates `<dir>/<corpus>/cells.jsonl` into `<out>/cells.jsonl`
(latest record per cell key wins) before reporting. Writes `<out>/summary.json`
(`runner.summarize`), `<out>/contamination.json` (`runner.contamination_report`)
and `<out>/TOURNAMENT.md`:

- status and leak counts per arm;
- forward blast radius per arm: precision / recall / F1 of the affected
  symbols, Django table and side-effect matches (exact and F1), over all
  tasks and over the tasks arm0 did not flag;
- backward adaptation per arm: pass rate, syntax validity of the spliced
  file, context tokens and tokens per pass, all / unflagged;
- the contamination table (arm0 score per task) and a task x arm matrix.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from harness.experiments.layer4 import runner as R
from harness.experiments.layer4 import tasks as T
from harness.experiments.layer4.context import ARMS


def merge(cells_root: Path, out: Path) -> int:
    """`<cells_root>/<corpus>/cells.jsonl` -> `<out>/cells.jsonl` (one line per cell key)."""
    merged: dict[str, dict] = {}
    if (out / "cells.jsonl").exists():
        merged.update(R.load_cells(out / "cells.jsonl"))
    for path in sorted(cells_root.glob("*/cells.jsonl")):
        merged.update(R.load_cells(path))
    out.mkdir(parents=True, exist_ok=True)
    with (out / "cells.jsonl").open("w") as fh:
        for key in sorted(merged):
            fh.write(json.dumps(merged[key], default=str) + "\n")
    return len(merged)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def _fmt(x):
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.0f}" if abs(x) >= 100 else f"{x:.3f}"  # token counts whole, rates to 3 places
    return str(x)


def tables(cells: list[dict], tasks: dict[str, dict], flagged: set[str]) -> dict:
    """Per-arm aggregates computed from the cells (ok cells only)."""
    ok = [c for c in cells if c["status"] == "ok" and c["score"]]
    out = {"status": defaultdict(lambda: defaultdict(int)), "forward": {}, "backward": {}, "matrix": defaultdict(dict)}
    for c in cells:
        out["status"][c["arm"]][c["status"]] += 1
    for arm in ARMS:
        for view, keep in (("all", lambda c: True), ("unflagged", lambda c: c["task_id"] not in flagged)):
            fw = [c for c in ok if c["arm"] == arm and c["family"] == "forward_blast" and keep(c)]
            bw = [c for c in ok if c["arm"] == arm and c["family"] == "backward_adaptation" and keep(c)]
            if fw:
                dj = [c for c in fw if c["corpus"] == "django"]
                out["forward"].setdefault(arm, {})[view] = {
                    "n": len(fw), "precision": _mean(c["score"]["precision"] for c in fw),
                    "recall": _mean(c["score"]["recall"] for c in fw), "f1": _mean(c["score"]["f1"] for c in fw),
                    "parsed": _mean(float(c["score"]["parsed"]) for c in fw),
                    "django_n": len(dj),
                    "tables_exact": _mean(float(c["score"]["tables_exact"]) for c in dj if c["score"].get("tables_exact") is not None),
                    "tables_f1": _mean((c["score"].get("tables_prf") or {}).get("f1") for c in dj),
                    "effects_exact": _mean(float(c["score"]["side_effects_exact"]) for c in dj
                                           if c["score"].get("side_effects_exact") is not None),
                    "effects_f1": _mean((c["score"].get("side_effects_prf") or {}).get("f1") for c in dj),
                    "context_tokens": _mean(float(c["tokens"]["context"]) for c in fw)}
            if bw:
                passes = sum(c["score"]["passed"] for c in bw)
                spent = sum(c["tokens"]["prompt"] + c["tokens"]["completion"] for c in bw)
                out["backward"].setdefault(arm, {})[view] = {
                    "n": len(bw), "pass_rate": round(passes / len(bw), 3),
                    "syntax_ok": _mean(float(c["score"]["syntax_ok"]) for c in bw if c["score"].get("syntax_ok") is not None),
                    "context_tokens": _mean(float(c["tokens"]["context"]) for c in bw),
                    "tokens_per_pass": round(spent / passes, 1) if passes else None,
                    "passes_per_1k_tokens": round(1000 * passes / spent, 4) if spent else None}
    by = defaultdict(list)
    for c in ok:
        by[(c["task_id"], c["arm"])].append(float(c["score"]["passed"]) if c["family"] == "backward_adaptation"
                                            else c["score"]["f1"])
    for (tid, arm), xs in by.items():
        out["matrix"][tid][arm] = _mean(xs)
    return out


def render(cells: list[dict], tasks: dict[str, dict], summary: dict, contamination: dict, header: str = "") -> str:
    flagged = set(contamination.get("flagged", []))
    t = tables(cells, tasks, flagged)
    seeds = sorted({c["seed"] for c in cells})
    lines = ["# Layer 4 tournament", "", header or "", "",
             f"{len(tasks)} tasks, {len(ARMS)} arms, seeds {seeds}; {len(cells)} cells. Contamination-flagged tasks "
             f"(arm0 >= threshold): {sorted(flagged) or 'none'}.", ""]
    lines += ["## Cell status", "", "| arm | " + " | ".join(("ok", "leak", "error", "timeout", "dry_run")) + " |",
              "|---|" + "---|" * 5]
    for arm in ARMS:
        st = t["status"].get(arm, {})
        lines.append(f"| {arm} | " + " | ".join(str(st.get(s, 0)) for s in ("ok", "leak", "error", "timeout", "dry_run")) + " |")
    leaks = defaultdict(list)
    for c in cells:
        if c["status"] == "leak":
            leaks[(c["task_id"], c["arm"])].append(c)
    if leaks:
        lines += ["", "## Leak cells (held back, never sent to the model)", "",
                  "| task | arm | cells | leak check | body lines | test files | trace keys | prompt | first body line |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for (tid, arm), cs in sorted(leaks.items()):
            c = cs[0]
            hits = c["leak"].get("focal_body_hits") or []
            lines.append(f"| {tid} | {arm} | {len(cs)} | v{(c.get('detail') or {}).get('leak_check', 1)} | "
                         f"{c['leak']['focal_body_lines']} | {', '.join(c['leak']['test_files']) or '-'} | "
                         f"{', '.join(c['leak'].get('trace_markers') or []) or '-'} | "
                         f"{', '.join((c.get('detail') or {}).get('prompt_leaks') or []) or '-'} | "
                         f"{('`' + hits[0][:60].replace('|', '/') + '`') if hits else '-'} |")
    lines += ["", "## Forward blast radius (callers within each task's max_depth)", "",
              "| arm | view | n | precision | recall | F1 | parsed | Django tables exact | tables F1 | effects exact | effects F1 | context tok |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for arm in ARMS:
        for view in ("all", "unflagged"):
            r = t["forward"].get(arm, {}).get(view)
            if r:
                lines.append(f"| {arm} | {view} | {r['n']} | {_fmt(r['precision'])} | {_fmt(r['recall'])} | "
                             f"{_fmt(r['f1'])} | {_fmt(r['parsed'])} | {_fmt(r['tables_exact'])} | {_fmt(r['tables_f1'])} | "
                             f"{_fmt(r['effects_exact'])} | {_fmt(r['effects_f1'])} | {_fmt(r['context_tokens'])} |")
    lines += ["", "## Backward contract adaptation (gating tests green)", "",
              "| arm | view | n | pass rate | syntax ok | context tok | tokens / pass | passes / 1k tok |",
              "|---|---|---|---|---|---|---|---|"]
    for arm in ARMS:
        for view in ("all", "unflagged"):
            r = t["backward"].get(arm, {}).get(view)
            if r:
                lines.append(f"| {arm} | {view} | {r['n']} | {_fmt(r['pass_rate'])} | {_fmt(r['syntax_ok'])} | "
                             f"{_fmt(r['context_tokens'])} | {_fmt(r['tokens_per_pass'])} | {_fmt(r['passes_per_1k_tokens'])} |")
    lines += ["", "## Contamination (arm0_no_retrieval)", "",
              f"Flagged at backward pass rate >= {T.CONTAMINATION_THRESHOLD} or forward mean F1 >= "
              f"{T.FORWARD_CONTAMINATION_F1}.", "", "| task | focal | arm0 score | seeds | flagged |", "|---|---|---|---|---|"]
    for tid in sorted(tasks):
        f = contamination.get("tasks", {}).get(tid, {})
        lines.append(f"| {tid} | `{tasks[tid]['focal']['qname']}` | {_fmt(f.get('arm0_score'))} | {f.get('seeds', 0)} | "
                     f"{'**yes**' if f.get('flagged') else 'no' if f.get('flagged') is False else '-'} |")
    lines += ["", "## Task x arm (mean F1 forward, pass rate backward)", "", "| task | " + " | ".join(ARMS) + " |",
              "|---|" + "---|" * len(ARMS)]
    for tid in sorted(tasks):
        lines.append(f"| {tid} | " + " | ".join(_fmt(t["matrix"].get(tid, {}).get(a)) for a in ARMS) + " |")
    return "\n".join(lines) + "\n"


def write(out: Path, tasks: dict[str, dict], header: str = "") -> str:
    contamination = R.contamination_report(out, tasks)
    summary = R.summarize(out, tasks)
    cells = list(R.load_cells(out / "cells.jsonl").values())
    md = render(cells, tasks, summary, contamination, header)
    (out / "TOURNAMENT.md").write_text(md)
    return md


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--merge", type=Path, help="directory of <corpus>/cells.jsonl to merge into <out>/cells.jsonl")
    p.add_argument("--tasks-dir", type=Path, default=R.TASKS_DIR)
    p.add_argument("--header", default="")
    args = p.parse_args(argv)
    tasks = R.load_tasks(args.tasks_dir)
    if args.merge:
        print(f"[merge] {merge(args.merge, args.out)} cells -> {args.out / 'cells.jsonl'}", flush=True)
    print(write(args.out, tasks, args.header))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
