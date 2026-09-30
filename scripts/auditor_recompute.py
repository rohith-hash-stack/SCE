"""Auditor-response recomputation: every number in
`reports/final_sweep/auditor_revision_plan.md` and
`reports/final_sweep/supplementary_triple_scoring.md` comes from here.

    python scripts/auditor_recompute.py            # writes reports/final_sweep/auditor_numbers.json

Inputs:
- Final sweep cells: `reports/final_sweep/{slm_qwen7b,full}/<repo>/cells.jsonl`.
- Historical standalone-run checkpoints, read straight from git history at
  their own commits, with the ground-truth task files as they stood at those
  commits.

Nothing here calls an LLM. It re-scores saved responses with the harness's
own scorers:
- `score_debug`: exact-match.
- `score_debug_causal`: partial credit; binary = score == 1.0.

Uncertainty is a stratified (by corpus) cluster bootstrap over tasks, the
same as `benchmarks.final_sweep.analysis`, extended with bootstrap p-values
and Holm-Bonferroni adjustment.
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from benchmarks.final_sweep import config as C  # noqa: E402
from benchmarks.final_sweep.analysis import external_task_ids  # noqa: E402
from benchmarks.final_sweep.runner import load_debug_tasks, load_records  # noqa: E402
from benchmarks.ground_truth.loader import load_tasks_from_dir  # noqa: E402
from benchmarks.tsr.scorer_debug import ParseError, _normalize, extract_flat_symbols, score_debug, score_debug_causal  # noqa: E402

ITERS = 10_000
REPOS = ("trpc", "express", "fastapi", "django")
OUT = ROOT / "reports/final_sweep/auditor_numbers.json"


# --------------------------------------------------------------------- #
# Loading + triple scoring of sweep cells
# --------------------------------------------------------------------- #

def sweep_rows(root: str) -> list[dict]:
    rows = []
    for repo in REPOS:
        path = ROOT / f"reports/final_sweep/{root}/{repo}/cells.jsonl"
        if not path.exists():
            continue
        tasks = {t.task_id: t for t in load_debug_tasks(repo)}
        for r in load_records(path).values():
            if r["status"] != "ok":
                continue
            pipe = tasks[r["task_id"]].adjudicated.pipeline_symbols
            r = dict(r)
            r["exact"] = score_debug(r["answer_response"], pipe)
            r["binary"] = r["tsr"]
            r["partial"] = r["tsr_partial"]
            r["_pipeline"] = pipe
            rows.append(r)
    return rows


# --------------------------------------------------------------------- #
# Clustered bootstrap with p-values
# --------------------------------------------------------------------- #

def task_means(rows, field):
    acc = defaultdict(lambda: defaultdict(list))
    for r in rows:
        acc[(r["repo"], r["task_id"])][r["engine_id"]].append(float(r[field]))
    return {k: {a: sum(v) / len(v) for a, v in arms.items()} for k, arms in acc.items()}


def boot_delta(rows, a, b, field="binary", seed=0, iters=ITERS):
    """Paired within-task difference A-B, task-weighted; stratified cluster
    bootstrap over tasks. Returns point, 95% CI, bootstrap distribution."""
    tm = task_means(rows, field)
    keys = [k for k, v in tm.items() if a in v and b in v]
    by_repo = defaultdict(list)
    for k in keys:
        by_repo[k[0]].append(k)
    d = {k: tm[k][a] - tm[k][b] for k in keys}
    point = sum(d.values()) / len(d)
    rng = random.Random(seed)
    dist = []
    for _ in range(iters):
        s = [d[rng.choice(ks)] for ks in by_repo.values() for _ in ks]
        dist.append(sum(s) / len(s))
    dist.sort()
    return point, (dist[int(0.025 * iters)], dist[int(0.975 * iters) - 1]), dist, len(keys)


def p_two_sided(point, dist):
    """Bootstrap p-value for H0: delta = 0 (percentile / sign-flip form)."""
    below = sum(1 for x in dist if x <= 0) / len(dist)
    above = sum(1 for x in dist if x >= 0) / len(dist)
    return min(1.0, 2 * min(below, above))


def holm(pvals: dict[str, float]) -> dict[str, float]:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    adj, running = {}, 0.0
    for i, (name, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        adj[name] = running
    return adj


# --------------------------------------------------------------------- #
# Failure taxonomy (binary failures)
# --------------------------------------------------------------------- #

def classify_failure(r, reach) -> str:
    try:
        answer = extract_flat_symbols(r["answer_response"])
    except ParseError:
        return "unparseable answer"
    pipe = r["_pipeline"]
    ctx = set(r["selected_symbols"])
    ex = [_normalize(s) for s in answer]
    pn = [_normalize(s) for s in pipe]
    ctx_n = {_normalize(s) for s in ctx}
    if [s for s in ex if s not in set(pn) and s not in ctx_n]:
        return "hallucination gate (named a symbol absent from context)"
    missing = [p for p, pb in zip(pipe, pn) if pb not in ex]
    if missing:
        if any(m not in ctx for m in missing):
            return "Turn-1 retrieval omission (stage never reached context)"
        return "Turn-2 generation omission (stage in context, not named)"
    # all stages named, order differs: is the ground-truth order causally forced?
    pos = {s: ex.index(_normalize(s)) for s in pipe}
    for i in range(len(pipe)):
        for j in range(i + 1, len(pipe)):
            a, b = pipe[i], pipe[j]
            if pos[a] > pos[b] and reach(a, b):
                return "Turn-2 sequencing violation (order causally forced)"
    return "annotation artifact (misordered stages are causally independent siblings)"


def reach_fn(repo):
    import networkx as nx

    from prism.cli import build_pipeline
    from benchmarks.corpora.resolver import resolve

    builder, _ = build_pipeline(str(resolve(repo)))
    g = builder.calls_graph

    def reach(a, b):
        return a in g and b in g and nx.has_path(g, a, b)
    return reach


# --------------------------------------------------------------------- #
# Historical runs, re-scored under uniform binary TSR
# --------------------------------------------------------------------- #

def git_json(spec):
    out = subprocess.run(["git", "show", spec], cwd=ROOT, capture_output=True, text=True)
    return json.loads(out.stdout) if out.returncode == 0 and out.stdout else None


def tasks_at(commit, repo):
    tmp = Path(tempfile.mkdtemp())
    listing = subprocess.run(["git", "ls-tree", "--name-only", f"{commit}:benchmarks/ground_truth/tasks/{repo}"],
                             cwd=ROOT, capture_output=True, text=True).stdout.split()
    for name in listing:
        text = subprocess.run(["git", "show", f"{commit}:benchmarks/ground_truth/tasks/{repo}/{name}"],
                              cwd=ROOT, capture_output=True, text=True).stdout
        (tmp / name).write_text(text)
    return {t.task_id: t for t in load_tasks_from_dir(tmp).accepted}


HISTORICAL = [
    # label, repo, task-commit, single-pass checkpoint spec, two-pass checkpoint spec.
    # Every Qwen-7B run outside the final sweep (found by scanning all
    # remote branches; there are none for tRPC or Express), then the
    # Django 14B/DeepSeek pilots the draft manuscript also cites.
    ("Django qwen-7B smoke", "django", "origin/smoke-qwen7b-progress",
     "origin/smoke-qwen7b-progress:reports/pilot-qwen7b/checkpoint_single_pass.json",
     "origin/smoke-qwen7b-progress:reports/pilot-qwen7b/checkpoint_two_pass.json"),
    ("Django qwen-7B pilot (seeds 43-46)", "django", "origin/pilot-qwen7b-progress",
     "origin/pilot-qwen7b-progress:reports/pilot-qwen7b/checkpoint_single_pass.json",
     "origin/pilot-qwen7b-progress:reports/pilot-qwen7b/checkpoint_two_pass.json"),
    ("Django qwen-7B holdout (seeds 101-102)", "django", "origin/pilot-qwen7b-holdout",
     "origin/pilot-qwen7b-holdout:reports/pilot-qwen7b-holdout/checkpoint_single_pass.json",
     "origin/pilot-qwen7b-holdout:reports/pilot-qwen7b-holdout/checkpoint_two_pass.json"),
    ("FastAPI qwen-7B smoke", "fastapi", "origin/smoke-fastapi-qwen7b-progress",
     "origin/smoke-fastapi-qwen7b-progress:reports/pilot-fastapi-qwen7b/checkpoint_single_pass.json",
     "origin/smoke-fastapi-qwen7b-progress:reports/pilot-fastapi-qwen7b/checkpoint_two_pass.json"),
    ("FastAPI qwen-7B rerun v2", "fastapi", "origin/pilot-fastapi-qwen7b-full-v2-progress",
     "origin/pilot-fastapi-qwen7b-full-v2-progress:reports/pilot-fastapi-qwen7b-full-v2/checkpoint_single_pass.json",
     "origin/pilot-fastapi-qwen7b-full-v2-progress:reports/pilot-fastapi-qwen7b-full-v2/checkpoint_two_pass.json"),
    ("FastAPI qwen-7B rerun v3", "fastapi", "origin/pilot-fastapi-qwen7b-full-v3-progress",
     "origin/pilot-fastapi-qwen7b-full-v3-progress:reports/pilot-fastapi-qwen7b-full-v3/checkpoint_single_pass.json",
     "origin/pilot-fastapi-qwen7b-full-v3-progress:reports/pilot-fastapi-qwen7b-full-v3/checkpoint_two_pass.json"),
    ("FastAPI qwen-7B full (seed 42)", "fastapi", "9d4333f",
     "36e52b5:reports/pilot-fastapi-qwen7b-full/checkpoint_single_pass.json",
     "899a2ae:reports/pilot-fastapi-qwen7b-full/checkpoint_two_pass.json"),
    ("FastAPI qwen-7B holdout (seeds 101-103)", "fastapi", "0588972",
     "d1b80c8:reports/pilot-fastapi-qwen7b-holdout/checkpoint_single_pass.json",
     "23b50ea:reports/pilot-fastapi-qwen7b-holdout/checkpoint_two_pass.json"),
    ("Django qwen-14B pilot-4-patched", "django", "594eec9",
     "1af868a:reports/pilot-4/checkpoint_single_pass.json",
     "be8acdd:reports/pilot-4-patched/checkpoint_two_pass.json"),
    ("Django deepseek-6.7B pilot", "django", "3e54725",
     "f34f8bb:reports/pilot-deepseek/checkpoint_single_pass.json",
     "dc96106:reports/pilot-deepseek/checkpoint_two_pass.json"),
]


def historical():
    results = []
    for label, repo, tcommit, sp_spec, tp_spec in HISTORICAL:
        tasks = tasks_at(tcommit, repo)
        sp, tp = git_json(sp_spec), git_json(tp_spec)
        if sp is None or tp is None:
            results.append({"label": label, "error": "checkpoint not found"})
            continue
        # one row per (task, engine, budget, seed) with three scores where computable
        rows = []
        stored_scores = []
        for key, c in sp["cells"].items():
            task_id, engine, budget, seed = key.split("|")
            if task_id not in tasks:
                continue
            pipe = tasks[task_id].adjudicated.pipeline_symbols
            causal = score_debug_causal(c["raw_response"], pipe, set(c.get("selected_symbols") or []))
            stored_scores.append(c["score"])
            rows.append({"repo": repo, "task_id": task_id, "engine_id": engine, "budget": int(budget), "seed": seed,
                         "stored": c["score"], "binary": 1 if causal == 1.0 else 0, "partial": causal,
                         "exact": score_debug(c["raw_response"], pipe)})
        for c in tp["cells"].values():
            if c["task_id"] not in tasks or c.get("tsr") is None:
                continue
            pipe = tasks[c["task_id"]].adjudicated.pipeline_symbols
            rows.append({"repo": repo, "task_id": c["task_id"], "engine_id": "prism_two_pass", "budget": c["budget"],
                         "seed": str(c["seed"]), "stored": c["tsr"], "binary": 1 if c["tsr"] == 1.0 else 0,
                         "partial": c["tsr"], "exact": score_debug(c["turn2_response"], pipe)})
        single_scorer = "strict (exact-match)" if all(s in (0, 0.0, 1, 1.0) for s in stored_scores) and any(
            r["stored"] != r["partial"] for r in rows if r["engine_id"] != "prism_two_pass") else "causal (partial credit)"
        per = {}
        for eng in sorted({r["engine_id"] for r in rows}):
            v = [r for r in rows if r["engine_id"] == eng]
            per[eng] = {k: sum(r[k] for r in v) / len(v) for k in ("stored", "exact", "binary", "partial")}
            per[eng]["n"] = len(v)
        entry = {"label": label, "single_pass_stored_scorer": single_scorer, "per_engine": per}
        # mixed (as historically reported) vs uniform deltas, prism_two_pass vs baseline_bfs_bidirectional
        base, prism = "baseline_bfs_bidirectional", "prism_two_pass"
        if base in per and prism in per:
            entry["delta_as_reported_mixed"] = per[prism]["stored"] - per[base]["stored"]
            # pair on (task, budget, seed) for uniform binary with clustered CI
            for r in rows:
                r["_pair"] = f"{r['budget']}|{r['seed']}"
            paired = [dict(r, engine_id=r["engine_id"], task_id=r["task_id"]) for r in rows if r["engine_id"] in (base, prism)]
            pt, ci, _, n = boot_delta(paired, prism, base, "binary")
            entry["delta_uniform_binary"] = {"point": pt, "ci95": ci, "n_tasks": n}
            pt2, ci2, _, _ = boot_delta(paired, prism, base, "partial")
            entry["delta_uniform_partial"] = {"point": pt2, "ci95": ci2}
        results.append(entry)
    return results


# --------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------- #

def main():
    out = {}
    external = external_task_ids()

    # Task 1: triple scoring
    triple = {}
    rows_by_model = {"qwen2.5-coder:7b": sweep_rows("slm_qwen7b"), "gpt-4o-mini": sweep_rows("full")}
    for model, rows in rows_by_model.items():
        t = {}
        for repo in REPOS:
            for arm in C.ARM_ORDER:
                v = [r for r in rows if r["repo"] == repo and r["engine_id"] == arm]
                if v:
                    t[f"{repo}|{arm}"] = {k: sum(r[k] for r in v) / len(v) for k in ("exact", "binary", "partial")} | {"n": len(v)}
        for arm in C.ARM_ORDER:
            v = [r for r in rows if r["engine_id"] == arm]
            if v:
                t[f"ALL|{arm}"] = {k: sum(r[k] for r in v) / len(v) for k in ("exact", "binary", "partial")} | {"n": len(v)}
        triple[model] = t
    out["triple"] = triple

    # Task 1b: why exact != binary (gpt tRPC)
    g = [r for r in rows_by_model["gpt-4o-mini"] if r["repo"] == "trpc"]
    expl = {}
    for arm in ("prism_full", "pragmatic_oracle", "baseline_bfs_bidirectional", "ablation_signature_only"):
        v = [r for r in g if r["engine_id"] == arm]
        gap = [r for r in v if r["binary"] == 1 and r["exact"] == 0]
        kinds = Counter()
        extras_in_ctx = 0
        for r in gap:
            ans = extract_flat_symbols(r["answer_response"])
            pn = {_normalize(s) for s in r["_pipeline"]}
            extra = [s for s in ans if _normalize(s) not in pn]
            dup = len(ans) - len(set(_normalize(s) for s in ans))
            kinds["extra real symbols from context" if extra else ("duplicate stage names" if dup else "other")] += 1
            extras_in_ctx += len(extra)
        expl[arm] = {"n": len(v), "binary_pass_exact_fail": len(gap), "kinds": dict(kinds),
                     "mean_extra_symbols": (extras_in_ctx / len(gap)) if gap else 0.0,
                     "mean_context_size": sum(len(r["selected_symbols"]) for r in v) / len(v),
                     "mean_pipeline_len": sum(len(r["_pipeline"]) for r in v) / len(v)}
    out["exact_vs_binary_trpc_gpt"] = expl

    # Task 2.1: tRPC failure taxonomy
    reach = reach_fn("trpc")
    fail = {}
    for model, rows in rows_by_model.items():
        v = [r for r in rows if r["repo"] == "trpc" and r["engine_id"] == "prism_full"]
        f = [r for r in v if r["binary"] == 0]
        cats = Counter(classify_failure(r, reach) for r in f)
        fail[model] = {"cells": len(v), "failures": len(f), "failure_rate": len(f) / len(v), "categories": dict(cats.most_common())}
    out["trpc_failure_taxonomy"] = fail

    # Task 2.2 + 2.3 + Task 3: contrasts (binary), clustered, Holm
    contrasts = [
        ("prism_full", "baseline_bfs_bidirectional"), ("prism_full", "ablation_lexical_anchors"),
        ("scaffolded_oracle", "pragmatic_oracle"), ("prism_full", "ablation_signature_only"),
        ("prism_full", "ablation_no_purity"), ("prism_full", "prism_plus_distractors"),
        ("pragmatic_oracle", "prism_full"),
    ]
    cres = {}
    for model, rows in rows_by_model.items():
        for scope, rs in (("all", rows), ("clean80", [r for r in rows if r["task_id"] not in external])):
            for repo_scope in ("pooled",) + REPOS:
                sub = rs if repo_scope == "pooled" else [r for r in rs if r["repo"] == repo_scope]
                if not sub:
                    continue
                block = {}
                for a, b in contrasts:
                    if not any(r["engine_id"] == a for r in sub) or not any(r["engine_id"] == b for r in sub):
                        continue
                    pt, ci, dist, n = boot_delta(sub, a, b, "binary")
                    block[f"{a} - {b}"] = {"point": pt, "ci95": ci, "p": p_two_sided(pt, dist), "n_tasks": n}
                if block:
                    adj = holm({k: v["p"] for k, v in block.items()})
                    for k in block:
                        block[k]["p_holm"] = adj[k]
                    cres[f"{model}|{scope}|{repo_scope}"] = block
    out["contrasts_binary"] = cres

    # Task 3.1: non-inferiority on the 80 clean tasks (qwen, pooled), margin -0.05
    q80 = [r for r in rows_by_model["qwen2.5-coder:7b"] if r["task_id"] not in external]
    pt, ci, dist, n = boot_delta(q80, "prism_full", "baseline_bfs_bidirectional", "binary")
    ni_p = sum(1 for x in dist if x <= -0.05) / len(dist)
    tm = task_means(q80, "context_tokens")
    keys = [k for k, v in tm.items() if "prism_full" in v and "baseline_bfs_bidirectional" in v]
    by_repo = defaultdict(list)
    for k in keys:
        by_repo[k[0]].append(k)

    def saving(ks):
        p = sum(tm[k]["prism_full"] for k in ks) / len(ks)
        b = sum(tm[k]["baseline_bfs_bidirectional"] for k in ks) / len(ks)
        return 1 - p / b
    rng = random.Random(1)
    sdist = sorted(saving([rng.choice(ks) for ks in by_repo.values() for _ in ks]) for _ in range(ITERS))
    per_repo_saving = {repo: saving(by_repo[repo]) for repo in by_repo}
    out["non_inferiority_qwen_clean80"] = {
        "delta_binary": pt, "ci95_two_sided": ci, "margin": -0.05, "n_tasks": n,
        "one_sided_p_delta_le_margin": ni_p, "lower_bound_excludes_margin": ci[0] > -0.05,
        "token_saving": saving(keys), "token_saving_ci95": (sdist[int(0.025 * ITERS)], sdist[int(0.975 * ITERS) - 1]),
        "token_saving_per_repo": per_repo_saving,
    }

    # Task 2: historical runs
    out["historical"] = historical()

    OUT.write_text(json.dumps(out, indent=2, default=list))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
