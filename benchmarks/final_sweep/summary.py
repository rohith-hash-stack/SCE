"""Pilot/sweep summary statistics and cost accounting over `cells.jsonl`
records. Pure functions over plain dicts - no retrieval, no LLM."""
from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict

from benchmarks.final_sweep import config as C

#: Planned full-sweep task counts (debug-type tasks that load cleanly
#: under `benchmarks/ground_truth/tasks/<repo>/`), used for the cost
#: projection. Verified against the loader when this module was written.
FULL_SWEEP_TASKS = {"fastapi": 25, "django": 20, "express": 20, "trpc": 25}

#: Contrasts reported as paired (same task, same seed) differences.
PAIRED_CONTRASTS = (
    ("scaffolded_oracle", "pragmatic_oracle", "scaffolding effect (cleanliness paradox)"),
    ("prism_full", "baseline_bfs_bidirectional", "PRISM vs floor"),
    ("prism_full", "ablation_lexical_anchors", "taxonomy vs lexical anchors"),
    ("prism_full", "ablation_signature_only", "4-axis annotations vs signatures only"),
    ("prism_full", "ablation_no_purity", "purity axis"),
    ("prism_full", "prism_plus_distractors", "no noise vs injected distractors"),
)


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def paired_bootstrap_ci(deltas: list[float], iters: int = 2000, seed: int = 0) -> tuple[float, float] | None:
    """Percentile bootstrap over task clusters is what the full sweep's
    gate should use; for the pilot, a plain paired bootstrap over cells
    is enough to see whether the machinery produces sane intervals."""
    if len(deltas) < 2:
        return None
    rng = random.Random(seed)
    n = len(deltas)
    means = sorted(sum(rng.choice(deltas) for _ in range(n)) / n for _ in range(iters))
    return (means[int(0.025 * iters)], means[int(0.975 * iters) - 1])


def summarize(rows: list[dict]) -> dict:
    from benchmarks.final_sweep.runner import validate_record

    invalid = {}
    for r in rows:
        problems = validate_record(r)
        if problems:
            invalid[f"{r.get('task_id')}|{r.get('engine_id')}|{r.get('seed')}"] = problems

    by_arm: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_arm[r["engine_id"]].append(r)
    arm_names = [a for a in C.ARM_ORDER if a in by_arm] + sorted(a for a in by_arm if a not in C.ARM_ORDER)

    arms = {}
    for arm in arm_names:
        cells = by_arm[arm]
        ok = [r for r in cells if r["status"] == "ok"]
        scored = [r for r in ok if r["tsr"] is not None]
        succ = sum(r["tsr"] for r in scored)
        two_pass = [r for r in ok if r.get("turn1_parsed_ok") is not None]
        # Seed variance: tasks whose binary TSR differs across seeds.
        per_task = defaultdict(set)
        for r in scored:
            per_task[r["task_id"]].add(r["tsr"])
        arms[arm] = {
            "n_cells": len(cells),
            "n_ok": len(ok),
            "n_error": sum(1 for r in cells if r["status"] == "error"),
            "n_dry_run": sum(1 for r in cells if r["status"] == "dry_run"),
            "tsr": succ / len(scored) if scored else None,
            "tsr_ci95": wilson_ci(succ, len(scored)),
            "tsr_partial": _mean(r["tsr_partial"] for r in scored),
            "cleanliness": _mean(r["cleanliness"] for r in cells if r["status"] != "error"),
            "cleanliness_scaffold_adjusted": _mean(r["cleanliness_scaffold_adjusted"] for r in cells if r["status"] != "error"),
            "cpi_context": _mean(r["cpi_context"] for r in cells if r["status"] != "error"),
            "cpi_answer": _mean(r["cpi_answer"] for r in ok),
            "cpi_e2e": _mean(r["cpi_e2e"] for r in ok),
            "cpi_turn1": _mean(r["cpi_turn1"] for r in ok),
            "sufficiency_ratio": _mean(r["sufficiency_ratio"] for r in cells if r["status"] != "error"),
            "is_sufficient_rate": _mean(float(r["is_sufficient"]) for r in cells if r["is_sufficient"] is not None),
            "is_truncated_rate": _mean(float(r["is_truncated"]) for r in cells if r["is_truncated"] is not None),
            "mean_context_tokens": _mean(r["context_tokens"] for r in cells if r["context_tokens"] is not None),
            "answer_parse_fail_rate": _mean(float(not r["answer_parsed_ok"]) for r in ok if r["answer_parsed_ok"] is not None),
            "turn1_parse_fail_rate": _mean(float(not r["turn1_parsed_ok"]) for r in two_pass) if two_pass else None,
            "turn1_reused": sum(1 for r in ok if r.get("turn1_reused_from")),
            "anchor_hit_rate": _mean(float(r["anchor_matches_task_seed"]) for r in cells if r.get("anchor_matches_task_seed") is not None),
            "distractors_in_context": _mean(r["distractors_in_context"] for r in cells if r["distractor_k"]),
            "distractors_named": _mean(r["distractors_named"] for r in ok if r["distractor_k"]),
            "tasks_with_seed_variance": sum(1 for v in per_task.values() if len(v) > 1),
            "tasks_scored": len(per_task),
            "llm_calls": sum(r["n_llm_calls"] for r in cells),
            "prompt_tokens": sum(r["prompt_tokens"] for r in cells),
            "completion_tokens": sum(r["completion_tokens"] for r in cells),
            "mean_latency_s": _mean(r["latency_s"] for r in ok),
            "cost_usd": round(sum(r["cost_usd"] for r in cells), 6),
            "cost_per_cell_usd": (sum(r["cost_usd"] for r in ok) / len(ok)) if ok else None,
        }

    index = {(r["task_id"], r["seed"], r["engine_id"]): r for r in rows if r["status"] == "ok" and r["tsr"] is not None}
    contrasts = []
    for a, b, label in PAIRED_CONTRASTS:
        pairs = [(index[k], index[(k[0], k[1], b)]) for k in index if k[2] == a and (k[0], k[1], b) in index]
        if not pairs:
            continue
        d_tsr = [x["tsr"] - y["tsr"] for x, y in pairs]
        d_clean = [x["cleanliness"] - y["cleanliness"] for x, y in pairs]
        contrasts.append({
            "a": a, "b": b, "label": label, "n_pairs": len(pairs),
            "delta_tsr": statistics.fmean(d_tsr), "delta_tsr_ci95": paired_bootstrap_ci(d_tsr),
            "delta_cleanliness": statistics.fmean(d_clean),
        })

    ok_rows = [r for r in rows if r["status"] == "ok"]
    fingerprints: dict[str, int] = defaultdict(int)
    models: dict[str, int] = defaultdict(int)
    for r in ok_rows:
        for c in r["calls"]:
            fingerprints[c["system_fingerprint"] or "<none>"] += 1
            models[c["response_model"] or "<none>"] += 1
    manifest_hashes: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in rows:
        if r.get("manifest_hash"):
            manifest_hashes[(r["task_id"], r["engine_id"])].add(r["manifest_hash"])

    total_cost = sum(r["cost_usd"] for r in rows)
    per_arm_cell_cost = {a: s["cost_per_cell_usd"] for a, s in arms.items() if s["cost_per_cell_usd"] is not None}
    full_cells_per_arm = sum(FULL_SWEEP_TASKS.values()) * len(C.DEFAULT_SEEDS)
    projection = None
    if per_arm_cell_cost:
        projected = sum(cost * full_cells_per_arm for cost in per_arm_cell_cost.values())
        projection = {
            "basis": "this run's mean cost per ok cell, per arm (repo mix differs - Django/FastAPI contexts are larger)",
            "cells_per_arm": full_cells_per_arm,
            "arms": len(per_arm_cell_cost),
            "total_cells": full_cells_per_arm * len(per_arm_cell_cost),
            "projected_cost_usd": round(projected, 2),
        }

    return {
        "harness_version": C.HARNESS_VERSION,
        "n_cells": len(rows),
        "status_counts": {s: sum(1 for r in rows if r["status"] == s) for s in ("ok", "error", "dry_run")},
        "schema": {"n_invalid": len(invalid), "invalid": dict(list(invalid.items())[:20])},
        "arms": arms,
        "contrasts": contrasts,
        "determinism": {
            "system_fingerprints": dict(fingerprints),
            "response_models": dict(models),
            "manifest_hash_unstable": sorted(f"{t}|{e}" for (t, e), h in manifest_hashes.items() if len(h) > 1),
        },
        "cost": {
            "total_usd": round(total_cost, 6),
            "llm_calls": sum(r["n_llm_calls"] for r in rows),
            "prompt_tokens": sum(r["prompt_tokens"] for r in rows),
            "completion_tokens": sum(r["completion_tokens"] for r in rows),
            "full_sweep_projection": projection,
        },
        "errors": [
            {"task_id": r["task_id"], "engine_id": r["engine_id"], "seed": r["seed"], "error": (r["error"] or "").splitlines()[0]}
            for r in rows if r["status"] == "error"
        ][:50],
    }


def _f(value, digits=3):
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_markdown(s: dict) -> str:
    lines = [
        f"# Final sweep summary ({s['harness_version']})",
        "",
        f"Cells: {s['n_cells']} ({', '.join(f'{k}={v}' for k, v in s['status_counts'].items())}); "
        f"schema-invalid records: {s['schema']['n_invalid']}",
        "",
        "| arm | ok/n | TSR (95% CI) | TSR partial | cleanliness | CPI ctx | CPI answer | CPI e2e | suff. ratio | sufficient | truncated | ctx tokens | $/cell |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for arm, a in s["arms"].items():
        ci = a["tsr_ci95"]
        tsr = f"{_f(a['tsr'])} [{ci[0]:.2f}, {ci[1]:.2f}]" if ci else _f(a["tsr"])
        lines.append(
            f"| `{arm}` | {a['n_ok']}/{a['n_cells']} | {tsr} | {_f(a['tsr_partial'])} | {_f(a['cleanliness'])} | "
            f"{_f(a['cpi_context'])} | {_f(a['cpi_answer'])} | {_f(a['cpi_e2e'])} | {_f(a['sufficiency_ratio'])} | "
            f"{_f(a['is_sufficient_rate'])} | {_f(a['is_truncated_rate'])} | {_f(a['mean_context_tokens'], 0)} | "
            f"{_f(a['cost_per_cell_usd'], 5)} |"
        )
    lines += ["", "| arm | Turn-1 parse fail | answer parse fail | anchor = task seed | seed-varying tasks | Turn-1 reused | distractors in ctx / named | mean latency (s) | calls | cost ($) |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for arm, a in s["arms"].items():
        dist = f"{_f(a['distractors_in_context'], 1)} / {_f(a['distractors_named'], 2)}" if a["distractors_in_context"] is not None else "-"
        lines.append(
            f"| `{arm}` | {_f(a['turn1_parse_fail_rate'])} | {_f(a['answer_parse_fail_rate'])} | {_f(a['anchor_hit_rate'])} | "
            f"{a['tasks_with_seed_variance']}/{a['tasks_scored']} | {a['turn1_reused']} | {dist} | "
            f"{_f(a['mean_latency_s'], 2)} | {a['llm_calls']} | {_f(a['cost_usd'], 4)} |"
        )
    if s["contrasts"]:
        lines += ["", "Paired contrasts (same task and seed; A - B):", "",
                  "| A | B | what it isolates | pairs | ΔTSR (95% CI) | Δcleanliness |", "|---|---|---|---|---|---|"]
        for c in s["contrasts"]:
            ci = c["delta_tsr_ci95"]
            ci_s = f" [{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else ""
            lines.append(f"| `{c['a']}` | `{c['b']}` | {c['label']} | {c['n_pairs']} | {c['delta_tsr']:+.3f}{ci_s} | {c['delta_cleanliness']:+.3f} |")
    d = s["determinism"]
    cost = s["cost"]
    lines += [
        "",
        f"Served models: {d['response_models']}",
        f"System fingerprints: {d['system_fingerprints']}",
        f"Tasks whose Turn-1 manifest hash varied across seeds (should be none): {d['manifest_hash_unstable'] or 'none'}",
        "",
        f"Cost: ${cost['total_usd']:.4f} over {cost['llm_calls']} LLM calls "
        f"({cost['prompt_tokens']:,} prompt + {cost['completion_tokens']:,} completion tokens).",
    ]
    p = cost["full_sweep_projection"]
    if p:
        lines.append(
            f"Full-sweep projection: {p['total_cells']:,} cells ({p['arms']} arms x {p['cells_per_arm']:,}) "
            f"≈ ${p['projected_cost_usd']:.2f} - {p['basis']}."
        )
    if s["errors"]:
        lines += ["", "Errors:"] + [f"- {e['task_id']} {e['engine_id']} seed={e['seed']}: {e['error']}" for e in s["errors"]]
    return "\n".join(lines) + "\n"
