"""Turn-1 micro-ablation: do atomic capability tags help Turn 1 pick the
forward gold callers?

Three arms on the 8 Layer 4 forward (blast-radius) tasks, same manifest
(`direction="both"`, the cell budget) and the same Turn 2 / answer turn:

- `prism_full`             control: the manifest with the default `axes=` field;
- `prism_atomic_caps`      the same rows plus `|caps=[name@hop,...]` from
                           `prism.semantics.capabilities`, and a Turn-1
                           instruction to use the call hierarchy and the caps;
- `prism_r0_rule_callers`  reference: Turn 1 requests every `caller` row by
                           rule (config R0), no model call.

Per cell it records the Turn-1 request, and per task the deterministic
candidate list, so it reports:

a) Turn-1 gold recall: gold candidates requested / gold candidates in the
   manifest, and gold candidates not requested per cell (the funnel's
   "lost: Turn 1", 14.0 per cell summed over the 8 tasks in 72b6efd);
b) Turn-1 precision: requested gold / requested (the seed excluded);
c) end-to-end forward F1 (`tasks.score_forward`, as the tournament);
d) Jaccard of the Turn-1 requests and of the packed symbols, control vs
   caps, per (task, seed).

    # model arms (an OpenAI-compatible endpoint, e.g. Ollama on Kaggle)
    PYTHONPATH=.:src python -m harness.experiments.layer4.atomic_caps_ablation --out OUT --seeds 42,43,44
    # deterministic parts only (R0 and caps coverage; no model)
    PYTHONPATH=.:src python -m harness.experiments.layer4.atomic_caps_ablation --out OUT --offline
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

from harness.experiments.layer4 import context as X
from harness.experiments.layer4 import runner as R
from harness.experiments.layer4 import tasks as T

CONTROL, CAPS, RULE = "prism_full", "prism_atomic_caps", "prism_r0_rule_callers"
ARMS = (CONTROL, CAPS, RULE)


def register_arms(out: Path) -> None:
    """The two ablation arms, for this process only: Stage 2's arm table and
    a copy of the cell schema whose `arm` enum also lists them (the
    tournament's own schema file is left alone)."""
    from benchmarks.final_sweep import config as C
    C.STAGE2_ARMS.setdefault(CAPS, CAPS)
    C.STAGE2_ARMS.setdefault(RULE, RULE)
    schema = json.loads(Path(T.CELL_SCHEMA).read_text())
    enum = schema["properties"]["arm"]["enum"]
    enum += [a for a in (CAPS, RULE) if a not in enum]
    path = out / "cell.schema.ablation.json"
    path.write_text(json.dumps(schema, indent=1) + "\n")
    T.CELL_SCHEMA = path


def manifest_rows(state, task: dict, cfg) -> dict[str, str]:
    """`{qualified_name: role}` of the task's Turn-1 manifest (deterministic)."""
    et = T.to_evaluation_task(task)
    with state.lock:
        text, _universe = state.prism.build_candidate_manifest(et.seed_symbol, direction="both", budget_tokens=cfg.budget)
    rows = {}
    for line in text.splitlines():
        if line and not line.startswith("<") and line.count("|") >= 1:
            rows[line.split("|")[0]] = line.split("|")[1]
    return rows


def task_static(corpus, task: dict, cfg) -> dict:
    from benchmarks.final_sweep.context_ops import caps_field
    state = corpus.state
    seed = T.to_evaluation_task(task).seed_symbol
    rows = manifest_rows(state, task, cfg)
    gold = set(task["gold"]["affected_symbols"]) - {seed}
    profiles = state.capability_profiles()
    caps = {q: caps_field(profiles.get(q)) for q in rows}
    return {"seed": seed, "rows": rows, "gold": sorted(gold), "gold_in_manifest": sorted(gold & set(rows)),
            "caps": caps}


def run_arm_cell(corpus, task, arm, seed, cfg, client, pythons, node) -> dict:
    captured = {}

    def build(corpus_, arm_, task_, seed_, cfg_, client_, log_, record_):
        ctx = X.build_context(corpus_, arm_, task_, seed_, cfg_, client_, log_, record_)
        captured.update(requested=list(record_.requested_symbols or []), turn1_parsed_ok=record_.turn1_parsed_ok,
                        turn1_degenerate=record_.turn1_degenerate, manifest_hash=record_.manifest_hash,
                        manifest_candidate_count=record_.manifest_candidate_count, turn1_response=record_.turn1_response)
        return ctx

    rec = R.run_cell(corpus, task, arm, seed, cfg, client, pythons, node, build=build)
    rec["detail"]["turn1"] = captured
    return rec


def cell_metrics(rec: dict, static: dict) -> dict:
    requested = set(rec["detail"]["turn1"].get("requested") or []) - {static["seed"]}
    gold, cand_gold = set(static["gold"]), set(static["gold_in_manifest"])
    picked_gold = requested & cand_gold
    score = rec.get("score") or {}
    return {"cand_gold": len(cand_gold), "requested": len(requested), "requested_gold": len(picked_gold),
            "lost_turn1": len(cand_gold - requested),
            "turn1_recall": len(picked_gold) / len(cand_gold) if cand_gold else None,
            "turn1_precision": len(requested & gold) / len(requested) if requested else None,
            "f1": score.get("f1"), "precision": score.get("precision"), "recall": score.get("recall")}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def mean(xs):
    xs = [x for x in xs if x is not None]
    return st.mean(xs) if xs else None


def summarize(cells: list[dict], statics: dict) -> dict:
    by = defaultdict(list)
    for c in cells:
        if c["status"] in ("ok", "dry_run"):
            by[c["arm"]].append(c)
    arms = {}
    for arm, cs in sorted(by.items()):
        m = [cell_metrics(c, statics[c["task_id"]]) for c in cs]
        tasks = sorted({c["task_id"] for c in cs})
        seeds = sorted({c["seed"] for c in cs})
        # funnel convention: per-task means over seeds, summed over tasks
        lost_per_task = {t: mean([x["lost_turn1"] for x, c in zip(m, cs) if c["task_id"] == t]) for t in tasks}
        arms[arm] = {"cells": len(cs), "tasks": len(tasks), "seeds": seeds, "status": sorted({c["status"] for c in cs}),
                     "turn1_gold_recall": mean([x["turn1_recall"] for x in m]),
                     "turn1_lost_gold_per_cell_summed_over_tasks": sum(v for v in lost_per_task.values() if v is not None),
                     "turn1_precision": mean([x["turn1_precision"] for x in m]),
                     "requested_per_cell": mean([x["requested"] for x in m]),
                     "f1": mean([x["f1"] for x in m]), "precision": mean([x["precision"] for x in m]),
                     "recall": mean([x["recall"] for x in m]),
                     "per_task": {t: {"turn1_recall": mean([x["turn1_recall"] for x, c in zip(m, cs) if c["task_id"] == t]),
                                      "f1": mean([x["f1"] for x, c in zip(m, cs) if c["task_id"] == t])} for t in tasks}}
    pairs = {}
    cell = {(c["arm"], c["task_id"], c["seed"]): c for c in cells if c["status"] in ("ok", "dry_run")}
    for (arm, tid, seed), c in cell.items():
        if arm != CONTROL or (CAPS, tid, seed) not in cell:
            continue
        o = cell[(CAPS, tid, seed)]
        pairs[(tid, seed)] = {
            "turn1": jaccard(set(c["detail"]["turn1"].get("requested") or []), set(o["detail"]["turn1"].get("requested") or [])),
            "packed": jaccard(set(c["selected_symbols"]), set(o["selected_symbols"]))}
    within = []  # noise floor: the control against itself across seeds
    ctl = defaultdict(dict)
    for (arm, tid, seed), c in cell.items():
        if arm == CONTROL:
            ctl[tid][seed] = set(c["detail"]["turn1"].get("requested") or [])
    for tid, ss in ctl.items():
        keys = sorted(ss)
        within += [jaccard(ss[a], ss[b]) for i, a in enumerate(keys) for b in keys[i + 1:]]
    coverage = {}
    for tid, s in statics.items():
        rows = [q for q in s["rows"] if q != s["seed"]]
        with_caps = [q for q in rows if s["caps"][q] != "caps=[]"]
        coverage[tid] = {"rows": len(rows), "rows_with_caps": len(with_caps),
                         "gold_rows": len(s["gold_in_manifest"]),
                         "gold_rows_with_caps": sum(s["caps"][q] != "caps=[]" for q in s["gold_in_manifest"]),
                         "distinct_caps": sorted({s["caps"][q] for q in with_caps})}
    return {"arms": arms,
            "jaccard_control_vs_caps": {"pairs": len(pairs), "turn1": mean([p["turn1"] for p in pairs.values()]),
                                        "packed": mean([p["packed"] for p in pairs.values()]),
                                        "turn1_control_across_seeds": mean(within)},
            "caps_coverage": coverage,
            "caps_coverage_total": {k: sum(v[k] for v in coverage.values())
                                    for k in ("rows", "rows_with_caps", "gold_rows", "gold_rows_with_caps")}}


def render(s: dict) -> str:
    f = lambda x: "-" if x is None else f"{x:.3f}"
    L = ["# Turn-1 atomic-capability micro-ablation (Layer 4 forward tasks)", "",
         "| arm | cells | Turn-1 gold recall | gold lost at Turn 1 (per cell, summed over tasks) | Turn-1 precision | "
         "requested / cell | F1 | P | R |", "|---|---|---|---|---|---|---|---|---|"]
    for arm, a in s["arms"].items():
        L.append(f"| `{arm}` | {a['cells']} | {f(a['turn1_gold_recall'])} | {f(a['turn1_lost_gold_per_cell_summed_over_tasks'])} | "
                 f"{f(a['turn1_precision'])} | {f(a['requested_per_cell'])} | {f(a['f1'])} | {f(a['precision'])} | {f(a['recall'])} |")
    j = s["jaccard_control_vs_caps"]
    L += ["", f"Jaccard, control vs caps (same task and seed, {j['pairs']} pairs): Turn-1 requests {f(j['turn1'])}, "
          f"packed symbols {f(j['packed'])}; control vs itself across seeds (noise floor): {f(j['turn1_control_across_seeds'])}.",
          "", "## caps coverage of the manifest rows", "", "| task | rows | rows with caps | gold rows | gold rows with caps | distinct caps |",
          "|---|---|---|---|---|---|"]
    for tid, c in sorted(s["caps_coverage"].items()):
        L.append(f"| {tid} | {c['rows']} | {c['rows_with_caps']} | {c['gold_rows']} | {c['gold_rows_with_caps']} | "
                 f"{'; '.join(c['distinct_caps']) or '-'} |")
    t = s["caps_coverage_total"]
    L.append(f"| **total** | {t['rows']} | {t['rows_with_caps']} | {t['gold_rows']} | {t['gold_rows_with_caps']} | |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    from benchmarks.final_sweep import config as C
    from benchmarks.final_sweep.runner import SweepConfig, build_client
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--seeds", default="42,43,44")
    p.add_argument("--arms", default=",".join(ARMS))
    p.add_argument("--tasks", default="all", help="comma-separated forward task ids, or all")
    p.add_argument("--offline", action="store_true", help="no model: R0 and caps coverage only")
    p.add_argument("--model", default=C.DEFAULT_MODEL)
    p.add_argument("--base-url", default=C.DEFAULT_BASE_URL)
    p.add_argument("--api-key-env", default=C.DEFAULT_API_KEY_ENV)
    p.add_argument("--temperature", type=float, default=C.DEFAULT_TEMPERATURE)
    p.add_argument("--max-tokens", type=int, default=C.DEFAULT_MAX_TOKENS)
    p.add_argument("--budget", type=int, default=C.DEFAULT_BUDGET)
    p.add_argument("--token-ceiling", type=int, default=C.DEFAULT_TOKEN_CEILING)
    p.add_argument("--num-ctx", type=int, default=24_576)
    args = p.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    register_arms(args.out)
    tasks = {k: v for k, v in R.load_tasks(R.TASKS_DIR).items() if v["family"] == "forward_blast"
             and (args.tasks == "all" or k in args.tasks.split(","))}
    arms = [a for a in args.arms.split(",") if a]
    if args.offline:
        arms = [a for a in arms if a == RULE]
    cfg = SweepConfig(model=args.model, temperature=args.temperature, budget=args.budget,
                      token_ceiling=args.token_ceiling, max_tokens=args.max_tokens, base_url=args.base_url,
                      api_key_env=args.api_key_env, dry_run=args.offline, num_ctx=args.num_ctx)
    cfg.tpm_limit = None if cfg.local else cfg.tpm_limit
    client = None if args.offline else build_client(cfg)
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    args.out.mkdir(parents=True, exist_ok=True)
    cells_path = args.out / "cells.jsonl"
    done = {}
    if cells_path.exists():
        for line in cells_path.read_text().splitlines():
            if line.strip():
                c = json.loads(line)
                done[(c["arm"], c["task_id"], c["seed"])] = c
    statics = {}
    for corpus_name in sorted({t["corpus"] for t in tasks.values()}):
        corpus = X.Corpus.load(corpus_name)
        for tid in sorted(k for k, t in tasks.items() if t["corpus"] == corpus_name):
            statics[tid] = task_static(corpus, tasks[tid], cfg)
            for arm in arms:
                for seed in seeds:
                    if (arm, tid, seed) in done and done[(arm, tid, seed)]["status"] in ("ok", "dry_run"):
                        continue
                    rec = run_arm_cell(corpus, tasks[tid], arm, seed, cfg, client, {}, "node")
                    done[(arm, tid, seed)] = rec
                    with cells_path.open("a") as fh:
                        fh.write(json.dumps(rec) + "\n")
                    m = cell_metrics(rec, statics[tid])
                    print(f"[cell] {tid} {arm} s{seed} {rec['status']}: turn1 recall {m['turn1_recall']} "
                          f"precision {m['turn1_precision']} f1 {m['f1']}", flush=True)
        del corpus
    (args.out / "statics.json").write_text(json.dumps(statics, indent=1) + "\n")
    s = summarize([c for c in done.values() if c["task_id"] in statics], statics)
    (args.out / "summary.json").write_text(json.dumps(s, indent=1) + "\n")
    (args.out / "SUMMARY.md").write_text(render(s))
    print(render(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
