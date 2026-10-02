"""M1 GPU smoke run (Kaggle): type-coverage gate + 5 real FastAPI T2 tasks
over Arms 0, 1, 5 and the Oracle, against the live model.

    python -m harness.kaggle_m1 --out /kaggle/working/m1_smoke

Gate A (type coverage): the five synthetic tasks (T1..T5) x 4 arms, once.
  Quality is NOT measured. PASS per (arm, task_type) when the cell ran, the
  adapter produced a valid DeliveredContext + NormalizedAnswer, the scorer
  took that type's branch, and every registry metric for the type has a
  value (NaN allowed where not applicable). bootstrap_ci must also run on
  each arm's synthetic cells, within each type.
Gate B (real tasks): 5 FastAPI T2 tasks x 4 arms, same validity criteria.

Also at startup: tokenizer parity (harness HF tokenizer vs the serving
model's own prompt counts) and the GPU memory watchdog before each batch.

`--dry-run` replaces the model with a scripted one and `--fake-encoders`
replaces jina/bge with deterministic stand-ins: for testing this runner
off-GPU only. Results from those flags are labelled and are not M1 evidence.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from harness import config as C


def _scripted_llm():
    from harness.llm import Completion

    def llm(system, user, max_tokens=0, seed=None, purpose="answer"):
        if purpose in ("turn1", "turn2b"):
            return Completion(json.dumps({"requested_symbols": []}), 1, 5, 0.0, model="scripted", purpose=purpose)
        if "fenced ```python" in user:
            return Completion("```python\ndef f(): return 1\n```", 1, 9, 0.0, model="scripted", purpose=purpose)
        if "few sentences of prose" in user:
            return Completion("It evaluates `fastapi.dependencies.utils.get_typed_annotation`.", 1, 9, 0.0, model="scripted")
        return Completion('```json\n{"reasoning": "s", "symbols": ["fastapi.dependencies.utils.get_typed_annotation"]}\n```',
                          1, 9, 0.0, model="scripted", purpose=purpose)
    return llm


def _fake_encoders():
    import zlib

    import numpy as np

    from harness.arms.arm1_rag import lexical_tokens

    class HashEmbedder:
        name = "FAKE-hash-embedder"

        def encode(self, texts):
            out = np.zeros((len(texts), 64), dtype=np.float32)
            for i, t in enumerate(texts):
                for w in lexical_tokens(t):
                    out[i, zlib.crc32(w.encode()) % 64] += 1
            return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)

    class OverlapReranker:
        name = "FAKE-overlap-reranker"

        def score(self, q, docs):
            qs = set(lexical_tokens(q))
            return np.array([len(qs & set(lexical_tokens(d))) for d in docs], dtype=np.float32)

    return HashEmbedder(), OverlapReranker()


def _validity(out, task) -> list[str]:
    from harness.scoring import registry as R
    from harness.scoring.fairness import verify_ranking
    problems = []
    try:
        verify_ranking(out.ctx.items)
    except AssertionError as e:
        problems.append(str(e))
    if out.ctx.total_tokens > max(out.ctx.budget_tokens, 0) and out.ctx.budget_tokens:
        problems.append("over budget after enforcement")
    if out.result.task_type != task.task_type:
        problems.append(f"scored as {out.result.task_type}")
    row = out.result.to_dict()
    for m in R.universal_metrics():
        if row.get(m) is None:
            problems.append(f"{m} missing")
    for m in R.task_specific_metrics(task.task_type):
        if m not in out.result.task_specific:
            problems.append(f"{m} missing")
    return problems


def tokenizer_parity(tok, ollama_url: str | None, model: str, root: str) -> dict:
    from harness.tokenizer import OllamaPromptCounter, verify_tokenizer_parity
    if not ollama_url:
        return {"status": "skipped (no server)"}
    files = ["fastapi/dependencies/utils.py", "fastapi/routing.py", "fastapi/applications.py", "fastapi/params.py"]
    stamp = str(time.time_ns())
    # distinct samples, so the server's prompt cache cannot shorten a count
    samples = [f"# parity sample {stamp}-{i}\n" + open(f"{root}/{p}").read()[:6000] for i, p in enumerate(files)]
    rep = verify_tokenizer_parity(tok, OllamaPromptCounter(model=model, base_url=ollama_url), samples)
    d = rep.to_dict()
    d["status"] = "ok" if rep.within_tolerance else "DIVERGED: count with the server-side tokenizer in later runs"
    return d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(C.KAGGLE_WORKING / "m1_smoke"))
    ap.add_argument("--seed", type=int, default=C.SEEDS[0])
    ap.add_argument("--real-tasks", type=int, default=5)
    ap.add_argument("--llm-url", default=C.LLM_BASE_URL)
    ap.add_argument("--ollama-url", default="http://127.0.0.1:11434", help="for tokenizer parity; '' to skip")
    ap.add_argument("--model", default=C.MODEL_NAME)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fake-encoders", action="store_true")
    ap.add_argument("--bootstrap-reps", type=int, default=1000)
    args = ap.parse_args(argv)

    from benchmarks.corpora.resolver import resolve
    from harness.arms import build_arm
    from harness.llm import ChatLLM
    from harness.pipeline import Pipeline, gpu_watchdog
    from harness.reporting.output_schema import attach_latency_aggregates, to_frame, write_parquet
    from harness.scoring.bootstrap import bootstrap_ci
    from harness.scoring.hallucination import build_symbol_cache
    from harness.tasks.loaders import load_tasks
    from harness.tasks.synthetic import synthetic_tasks
    from harness.tokenizer import get_tokenizer

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    root = str(resolve("fastapi"))
    tok = get_tokenizer()
    llm = _scripted_llm() if args.dry_run else ChatLLM(base_url=args.llm_url, model=args.model)
    report: dict = {"model": "SCRIPTED (dry run)" if args.dry_run else args.model, "tokenizer": tok.name,
                    "seed": args.seed, "fake_encoders": args.fake_encoders, "rows": []}
    print(f"[m1] model={report['model']} tokenizer={tok.name} fake_encoders={args.fake_encoders}", flush=True)

    report["tokenizer_parity"] = (tokenizer_parity(tok, args.ollama_url or None, args.model, root)
                                  if not args.dry_run else {"status": "skipped (dry run)"})
    print(f"[m1] tokenizer parity: {report['tokenizer_parity'].get('status')} "
          f"max_div={report['tokenizer_parity'].get('max_divergence')}", flush=True)

    # ---- index (CPU) ----
    t0 = time.perf_counter()
    arm5 = build_arm("arm5", llm=llm, tokenizer=tok)
    arm5.index(root, {})
    oracle = build_arm("oracle", tokenizer=tok, builder=arm5.engine.builder)
    oracle.index(root, {})
    emb, rr = _fake_encoders() if args.fake_encoders else (None, None)
    arm1 = build_arm("arm1", tokenizer=tok, embedder=emb, reranker=rr)
    arm1.index(root, {"repo_id": "fastapi"})
    arms = {"arm0": build_arm("arm0"), "arm1": arm1, "arm5": arm5, "oracle": oracle}
    index_ms = {("arm1", "fastapi"): arm1.index_latency["L_index"][0],
                ("arm5", "fastapi"): arm5.index_latency["L_index"][0]}
    print(f"[m1] indexed in {time.perf_counter() - t0:.0f}s ({len(arm1.chunks)} RAG chunks)", flush=True)

    pipe = Pipeline(arms, llm, tok, out_dir=out_dir, symbol_cache=build_symbol_cache(arm5.symbol_names()))
    gates = {"A_type_coverage": synthetic_tasks(root), "B_real_fastapi_T2": load_tasks("fastapi", repo_root=root,
                                                                                         limit=args.real_tasks)}
    results = []
    for gate, tasks in gates.items():
        if not args.dry_run:
            report.setdefault("gpu_mb", []).append(gpu_watchdog())
        for task in tasks:
            for arm_id in arms:
                t1 = time.perf_counter()
                row = {"gate": gate, "arm": arm_id, "task_type": task.task_type, "task_id": task.task_id}
                try:
                    out = pipe.run_cell(arm_id, task, seed=args.seed)
                    problems = _validity(out, task)
                    results.append(out.result)
                    row.update(status="PASS" if not problems else "FAIL", problems=problems,
                               tsr=out.result.tsr, extraction_success=out.ans.extraction_success,
                               total_tokens=out.ctx.total_tokens, prompt_tokens=out.prompt_tokens,
                               turn_count=out.result.turn_count, over_budget=out.result.over_budget)
                except Exception as exc:  # noqa: BLE001 - a failing cell is a FAIL row, not a crash
                    row.update(status="FAIL", problems=[f"{type(exc).__name__}: {exc}"[:400]])
                row["seconds"] = round(time.perf_counter() - t1, 1)
                report["rows"].append(row)
                tsr = row.get("tsr")
                print(f"{row['status']:5s} {gate:20s} {arm_id:7s} {task.task_type:16s} {task.task_id:52s} "
                      f"tsr={'NaN' if tsr is None or tsr != tsr else f'{tsr:.2f}'} {row['seconds']}s "
                      f"{'; '.join(row['problems']) if row['problems'] else ''}", flush=True)

    # bootstrap must run within each type on the synthetic cells
    boot_problems = []
    for arm_id in arms:
        for tt in {t.task_type for t in gates["A_type_coverage"]}:
            cells = [r for r in results if r.arm == arm_id and r.task_type == tt and r.task_id.startswith("syn_")]
            try:
                bootstrap_ci(cells, "tsr", n_reps=args.bootstrap_reps)
            except Exception as exc:  # noqa: BLE001
                boot_problems.append(f"{arm_id}/{tt}: {exc}")
    report["bootstrap_problems"] = boot_problems

    if results:
        df = attach_latency_aggregates(to_frame(results), index_ms)
        write_parquet(df, out_dir / "cells.parquet")
    (out_dir / "gate_report.json").write_text(json.dumps(report, indent=1, default=str))

    # ---- PASS/FAIL table per (gate, arm, task_type) ----
    print("\n" + "=" * 64)
    table: dict[tuple, str] = {}
    for r in report["rows"]:
        k = (r["gate"], r["arm"], r["task_type"])
        table[k] = "FAIL" if (table.get(k) == "FAIL" or r["status"] == "FAIL") else "PASS"
    for (gate, arm_id, tt), st in sorted(table.items()):
        print(f"{gate:20s} {arm_id:7s} {tt:18s} {st}")
    n_fail = sum(v == "FAIL" for v in table.values()) + len(boot_problems)
    print(f"bootstrap within-type on synthetic cells: {'PASS' if not boot_problems else 'FAIL ' + str(boot_problems)}")
    print(f"tokenizer parity: {report['tokenizer_parity'].get('status')}")
    print(f"TOTAL: {sum(v == 'PASS' for v in table.values())} PASS, {n_fail} FAIL   -> {out_dir}")
    if args.dry_run or args.fake_encoders:
        print("NOTE: dry-run / fake encoders: this verifies the runner, not M1.")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
