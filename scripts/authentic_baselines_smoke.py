"""Zero-cost retrieval inspection of the authentic external baselines on
FastAPI t02_001..t02_005, against PRISM's sweep cells for the same tasks.

    TIKTOKEN_CACHE_DIR=... AIDER_PYTHON=... python scripts/authentic_baselines_smoke.py

Per task and engine:
- **Context tokens:** exact `cl100k_base` count of the context text.
- **Pipeline coverage:** share of adjudicated `pipeline_symbols` present
  in the context.
- **Cleanliness:** 1 - FPR against the task's ground-truth universe.

Engines that need something this environment can't provide are reported
as not run, with the reason:
- Hybrid RAG needs encoder weights.
- Agentless needs an LLM.

Writes `reports/final_sweep/authentic_baselines_smoke.json`; the markdown
report is written from it.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from prism.slicer.tokenizer import active_backend  # noqa: E402

from benchmarks.baselines import agentless_fl as AG  # noqa: E402
from benchmarks.baselines.aider_official import build_aider_map  # noqa: E402
from benchmarks.corpora.resolver import resolve  # noqa: E402
from benchmarks.final_sweep.arms import CorpusState  # noqa: E402
from benchmarks.final_sweep.runner import load_debug_tasks, load_records  # noqa: E402
from benchmarks.metrics.fpr import fpr  # noqa: E402
from benchmarks.runner import _ground_truth_universe  # noqa: E402

OUT = ROOT / "reports/final_sweep/authentic_baselines_smoke.json"
BUDGET = 4000


def coverage(pipeline, present):
    return sum(1 for p in pipeline if p in present) / len(pipeline)


def main():
    backend = active_backend()
    if not backend.startswith("tiktoken"):
        raise SystemExit(f"refusing to measure with the fallback tokenizer ({backend}); set TIKTOKEN_CACHE_DIR")
    root = str(resolve("fastapi"))
    corpus = CorpusState("fastapi", root)
    tasks = load_debug_tasks("fastapi")[:5]
    sweep = load_records(ROOT / "reports/final_sweep/slm_qwen7b/fastapi/cells.jsonl")
    structure = AG.repo_structure(root)
    rows = []

    encoder, encoder_error = None, None
    try:
        from benchmarks.baselines.hybrid_rag import HybridIndex, SentenceTransformerEncoder, retrieve
        encoder = SentenceTransformerEncoder()
        index = HybridIndex(corpus.builder, encoder, cache_key="fastapi")
    except Exception as exc:  # weights unreachable, package missing, ...
        encoder_error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"

    for t in tasks:
        pipe = list(t.adjudicated.pipeline_symbols)
        gt = _ground_truth_universe(t)
        row = {"task_id": t.task_id, "pipeline": pipe}

        p = next(r for r in sweep.values() if r["task_id"] == t.task_id and r["engine_id"] == "prism_full" and r["seed"] == 1)
        row["prism_full"] = {"source": "final sweep cell (qwen-7B, seed 1, Kaggle, cl100k)", "tsr": p["tsr"],
                             "tokens": p["context_tokens"], "coverage": p["cpi_context"], "cleanliness": p["cleanliness"],
                             "n_symbols": len(p["selected_symbols"]), "presentation": "full bodies"}

        t0 = time.time()
        m = build_aider_map(corpus.builder, root, t.prompt, map_tokens=BUDGET)
        present = set(m.symbols)
        row["aider_official"] = {"aider_version": m.aider_version, "pythonhashseed": m.extra["pythonhashseed"], "tokens": _count(m.text), "aider_tokens": m.aider_tokens,
                                 "coverage": coverage(pipe, present), "cleanliness": 1 - fpr(present, gt),
                                 "n_symbols": len(m.symbols), "n_tags": len(m.tags), "unmatched_tags": len(m.unmatched_tags),
                                 "n_files_in_map": len({tg[0] for tg in m.tags}),
                                 "docs_src_share": (sum(1 for tg in m.tags if tg[0].startswith("docs_src/")) / len(m.tags)) if m.tags else None,
                                 "missing": [s for s in pipe if s not in present], "seconds": round(time.time() - t0, 1),
                                 "presentation": "signature lines (Aider tree, elided)"}

        if encoder_error is None:
            h = retrieve(index, t.prompt, root, BUDGET)
            present = set(h.symbols)
            row["hybrid_dense_bm25"] = {"encoder": encoder.name, "tokens": h.tokens, "coverage": coverage(pipe, present),
                                        "cleanliness": 1 - fpr(present, gt), "n_symbols": len(h.symbols),
                                        "missing": [s for s in pipe if s not in present], "presentation": "full chunks"}
        else:
            row["hybrid_dense_bm25"] = {"not_run": f"dense encoder unavailable here ({encoder_error})"}

        # Agentless: stage-1 prompt is deterministic; its selection needs an LLM.
        # returns (path, lines) pairs, as Agentless's own correct_file_paths expects
        files_all = {fc[0] for fc in AG.A.get_full_file_paths_and_classes_and_functions(structure)[0]}
        gt_files = sorted({str(Path(corpus.builder.symbol_table.get(s).file).relative_to(root))
                           for s in pipe if corpus.builder.symbol_table.get(s)})
        gt_files = [f for f in gt_files if f in files_all][:AG.DEFAULT_TOP_N]
        row["agentless_hierarchical"] = {
            "not_run": "stages 1-2 are LLM calls; no LLM reachable from this environment",
            "stage1_prompt_tokens": _count(AG.stage1_prompt(structure, t.prompt)),
            "ground_truth_files": gt_files,
            "stage2_prompt_tokens_if_stage1_picked_gt_files": _count(AG.stage2_prompt(structure, gt_files, t.prompt)) if gt_files else None,
        }
        rows.append(row)
        print(t.task_id, "done", file=sys.stderr, flush=True)

    OUT.write_text(json.dumps({"tokenizer": backend, "budget": BUDGET, "tasks": rows}, indent=2))
    print(f"wrote {OUT}")


def _count(text):
    from prism.slicer.tokenizer import count_tokens
    return count_tokens(text)


if __name__ == "__main__":
    main()
