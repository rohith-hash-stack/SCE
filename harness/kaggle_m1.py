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
import traceback
from pathlib import Path

from harness import config as C


def _scripted_llm():
    # prompt_tokens=0: "unknown", so the server-truncation guard does not apply
    from harness.llm import Completion

    def llm(system, user, max_tokens=0, seed=None, purpose="answer"):
        if purpose in ("turn1", "turn2b"):
            return Completion(json.dumps({"requested_symbols": []}), 0, 5, 0.0, model="scripted", purpose=purpose)
        if "fenced ```python" in user:
            return Completion("```python\ndef f(): return 1\n```", 0, 9, 0.0, model="scripted", purpose=purpose)
        if "few sentences of prose" in user:
            return Completion("It evaluates `fastapi.dependencies.utils.get_typed_annotation`.", 0, 9, 0.0, model="scripted")
        return Completion('```json\n{"reasoning": "s", "symbols": ["fastapi.dependencies.utils.get_typed_annotation"]}\n```',
                          0, 9, 0.0, model="scripted", purpose=purpose)
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
    meta = out.ctx.build_meta
    if meta.get("server_prompt_shortfall"):
        problems.append(f"server counted {meta.get('server_prompt_tokens')} prompt tokens, harness sent "
                        f"{out.prompt_tokens}: the server likely truncated the prompt (context window too small)")
    if meta.get("prompt_over_window"):
        problems.append(f"prompt {out.prompt_tokens} + generation cap exceeds the {C.CONTEXT_WINDOW}-token window")
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

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {"argv": ["python", "-m", "harness.kaggle_m1", *(argv if argv is not None else sys.argv[1:])],
                    "model": "SCRIPTED (dry run)" if args.dry_run else args.model, "seed": args.seed,
                    "fake_encoders": args.fake_encoders, "context_window": C.CONTEXT_WINDOW,
                    "generation_cap": C.GENERATION_RESERVE, "rows": []}
    exit_code = 2   # overwritten below; 2 = the run itself crashed
    try:
        exit_code = _run(args, out_dir, report)
    except Exception as exc:  # noqa: BLE001 - recorded, then reported via exit code 2
        report["fatal"] = failure_record(exc, step="harness", cmd=" ".join(report["argv"]))
        print(f"[m1] FATAL {report['fatal']['type']}: {report['fatal']['message']}", flush=True)
        for line in report["fatal"]["traceback_tail"]:
            print(f"[m1]   {line}", flush=True)
    finally:
        if not args.dry_run:
            report["gpu_mb_final"] = _gpu_reading()
        report["exit_code"] = exit_code
        (out_dir / "gate_report.json").write_text(json.dumps(report, indent=1, default=str))
    return exit_code


def failure_record(exc: BaseException | None, step: str, cmd: str, message: str | None = None,
                   type_: str | None = None) -> dict:
    """A failed cell (or run): {type, message, traceback_tail (last 5
    lines), step, cmd}."""
    if exc is not None:
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        lines = [ln for chunk in tb for ln in chunk.rstrip("\n").split("\n") if ln.strip()]
        return {"type": type(exc).__name__, "message": str(exc)[:1000], "traceback_tail": lines[-5:],
                "step": step, "cmd": cmd}
    return {"type": type_ or "Error", "message": (message or "")[:1000], "traceback_tail": [], "step": step, "cmd": cmd}


def _gpu_reading() -> dict:
    from harness.pipeline import gpu_memory_used_mb
    try:
        used = gpu_memory_used_mb()
    except Exception as exc:  # noqa: BLE001
        return {"max_used_mb": None, "error": f"{type(exc).__name__}: {exc}"}
    return {"max_used_mb": used, "limit_mb": C.GPU_MEMORY_ABORT_MB,
            "over_limit": bool(used is not None and used > C.GPU_MEMORY_ABORT_MB)}


def _run(args, out_dir: Path, report: dict) -> int:
    from benchmarks.corpora.resolver import resolve
    from harness.arms import build_arm
    from harness.llm import ChatLLM, OllamaChatLLM
    from harness.pipeline import Pipeline, class_ancestors
    from harness.reporting.output_schema import attach_latency_aggregates, to_frame, write_parquet
    from harness.scoring.bootstrap import bootstrap_ci
    from harness.scoring.hallucination import build_symbol_cache
    from harness.tasks.loaders import load_tasks
    from harness.tasks.synthetic import synthetic_tasks
    from harness.tokenizer import get_tokenizer

    root = str(resolve("fastapi"))
    tok = get_tokenizer()
    report["tokenizer"] = tok.name
    if args.dry_run:
        llm, report["llm_api"] = _scripted_llm(), "scripted"
    elif args.ollama_url:
        # native API: sets num_ctx / num_predict per request (Ollama's /v1 ignores them)
        llm, report["llm_api"] = OllamaChatLLM(base_url=args.ollama_url, model=args.model), f"ollama-native {args.ollama_url}/api/chat"
    else:
        llm, report["llm_api"] = ChatLLM(base_url=args.llm_url, model=args.model), f"openai-compatible {args.llm_url}"
    print(f"[m1] model={report['model']} api={report['llm_api']} tokenizer={tok.name} "
          f"window={C.CONTEXT_WINDOW} gen_cap={C.GENERATION_RESERVE} fake_encoders={args.fake_encoders}", flush=True)

    report["tokenizer_parity"] = (tokenizer_parity(tok, args.ollama_url or None, args.model, root)
                                  if not args.dry_run else {"status": "skipped (dry run)"})
    print(f"[m1] tokenizer parity: {report['tokenizer_parity'].get('status')} "
          f"max_div={report['tokenizer_parity'].get('max_divergence')}", flush=True)

    # ---- warm-up, then the first GPU reading (the model is resident now) ----
    if not args.dry_run:
        try:
            w = llm("You are a test.", "Reply with OK.", max_tokens=8, seed=args.seed, purpose="warmup")
            report["warmup"] = {"ok": True, "latency_seconds": round(w.latency_seconds, 1), "text": w.text[:40]}
        except Exception as exc:  # noqa: BLE001
            report["warmup"] = {"ok": False, **failure_record(exc, "warmup", "llm(warmup)")}
        report["gpu_mb_after_warmup"] = _gpu_reading()
        print(f"[m1] warm-up {report['warmup'].get('ok')}; GPU after warm-up: {report['gpu_mb_after_warmup']}", flush=True)
        if report["gpu_mb_after_warmup"].get("over_limit"):
            report["aborted"] = "GPU watchdog: memory over limit after warm-up"
            print(f"[m1] {report['aborted']}", flush=True)
            return 1

    # ---- index (CPU), each arm on its own: one arm's failure is that arm's ----
    index_failures: dict[str, dict] = {}
    index_ms: dict[tuple[str, str], float] = {}
    arms: dict = {"arm0": build_arm("arm0")}

    def _index(arm_id, factory, config):
        t0 = time.perf_counter()
        try:
            arm = factory()
            arm.index(root, config)
            arms[arm_id] = arm
            lat = getattr(arm, "index_latency", {}).get("L_index")
            if lat:
                index_ms[(arm_id, "fastapi")] = lat[0]
            print(f"[m1] indexed {arm_id} in {time.perf_counter() - t0:.0f}s", flush=True)
        except Exception as exc:  # noqa: BLE001 - recorded; that arm's cells FAIL with this cause
            index_failures[arm_id] = failure_record(exc, "index", f"{arm_id}.index({root!r})")
            arms[arm_id] = None
            print(f"[m1] INDEX FAIL {arm_id}: {type(exc).__name__}: {exc}", flush=True)

    _index("arm5", lambda: build_arm("arm5", llm=llm, tokenizer=tok), {})
    builder = arms["arm5"].engine.builder if arms.get("arm5") else None
    _index("oracle", lambda: build_arm("oracle", tokenizer=tok, builder=builder), {})
    emb, rr = _fake_encoders() if args.fake_encoders else (None, None)
    _index("arm1", lambda: build_arm("arm1", tokenizer=tok, embedder=emb, reranker=rr), {"repo_id": "fastapi"})
    report["index_failures"] = index_failures
    arm_order = ["arm0", "arm1", "arm5", "oracle"]

    builder = builder or (arms["oracle"].builder if arms.get("oracle") else None)
    symbol_cache = build_symbol_cache(builder.symbol_table._symbols) if builder is not None else None
    ancestors = class_ancestors(builder) if builder is not None else None
    pipe = Pipeline({a: arms[a] for a in arm_order if arms.get(a)}, llm, tok, out_dir=out_dir,
                    symbol_cache=symbol_cache, ancestors=ancestors)
    gates = {"A_type_coverage": synthetic_tasks(root), "B_real_fastapi_T2": load_tasks("fastapi", repo_root=root,
                                                                                         limit=args.real_tasks)}
    results = []
    for gate, tasks in gates.items():
        if not args.dry_run:
            reading = _gpu_reading()
            report.setdefault("gpu_mb_per_gate", {})[gate] = reading
            if reading.get("over_limit"):
                report["aborted"] = f"GPU watchdog: memory over limit before {gate}"
                print(f"[m1] {report['aborted']}", flush=True)
                break
        for task in tasks:
            for arm_id in arm_order:
                t1 = time.perf_counter()
                cmd = f"Pipeline.run_cell(arm={arm_id!r}, task={task.task_id!r}, seed={args.seed})"
                row = {"gate": gate, "arm": arm_id, "task_type": task.task_type, "task_id": task.task_id}
                if arm_id in index_failures:
                    fail = dict(index_failures[arm_id], cmd=cmd)
                    row.update(status="FAIL", problems=[f"{fail['type']}: {fail['message']}"], failure=fail)
                else:
                    try:
                        out = pipe.run_cell(arm_id, task, seed=args.seed)
                        problems = _validity(out, task)
                        results.append(out.result)
                        r = out.result
                        row.update(status="PASS" if not problems else "FAIL", problems=problems,
                                   tsr=r.tsr, extraction_success=out.ans.extraction_success,
                                   total_tokens=out.ctx.total_tokens, prompt_tokens=out.prompt_tokens,
                                   server_prompt_tokens=out.ctx.build_meta.get("server_prompt_tokens"),
                                   turn_count=r.turn_count, over_budget=r.over_budget,
                                   finish_reason=r.finish_reason, generation_capped=r.generation_capped,
                                   repetition_count=r.repetition_count)
                        if problems:
                            row["failure"] = failure_record(None, "validate", cmd, message="; ".join(problems),
                                                            type_="ValidityError")
                    except Exception as exc:  # noqa: BLE001 - a failing cell is a FAIL row, not a crash
                        fail = failure_record(exc, pipe.step, cmd)
                        row.update(status="FAIL", problems=[f"{fail['type']}: {fail['message']}"[:400]], failure=fail)
                row["seconds"] = round(time.perf_counter() - t1, 1)
                report["rows"].append(row)
                tsr = row.get("tsr")
                print(f"{row['status']:5s} {gate:20s} {arm_id:7s} {task.task_type:16s} {task.task_id:52s} "
                      f"tsr={'NaN' if tsr is None or tsr != tsr else f'{tsr:.2f}'} {row['seconds']}s "
                      f"{'capped ' if row.get('generation_capped') else ''}"
                      f"{'; '.join(row['problems']) if row['problems'] else ''}", flush=True)

    # bootstrap must run within each type on the synthetic cells
    boot_problems = []
    for arm_id in arm_order:
        for tt in sorted({t.task_type for t in gates["A_type_coverage"]}):
            cells = [r for r in results if r.arm == arm_id and r.task_type == tt and r.task_id.startswith("syn_")]
            try:
                bootstrap_ci(cells, "tsr", n_reps=args.bootstrap_reps)
            except Exception as exc:  # noqa: BLE001
                boot_problems.append(f"{arm_id}/{tt}: {exc}")
    report["bootstrap_problems"] = boot_problems

    if results:
        df = attach_latency_aggregates(to_frame(results), index_ms)
        write_parquet(df, out_dir / "cells.parquet")

    # ---- PASS/FAIL table per (gate, arm, task_type), also into the report ----
    print("\n" + "=" * 64)
    table: dict[tuple, str] = {}
    for r in report["rows"]:
        k = (r["gate"], r["arm"], r["task_type"])
        table[k] = "FAIL" if (table.get(k) == "FAIL" or r["status"] == "FAIL") else "PASS"
    report["table"] = [{"gate": g, "arm": a, "task_type": t, "status": st} for (g, a, t), st in sorted(table.items())]
    for row in report["table"]:
        print(f"{row['gate']:20s} {row['arm']:7s} {row['task_type']:18s} {row['status']}")
    n_pass = sum(v == "PASS" for v in table.values())
    n_fail = sum(v == "FAIL" for v in table.values()) + len(boot_problems) + (1 if report.get("aborted") else 0)
    report["totals"] = {"table_pass": n_pass, "table_fail": sum(v == "FAIL" for v in table.values()),
                        "rows_pass": sum(r["status"] == "PASS" for r in report["rows"]),
                        "rows_fail": sum(r["status"] == "FAIL" for r in report["rows"]),
                        "bootstrap_problems": len(boot_problems),
                        "generation_capped_rows": sum(bool(r.get("generation_capped")) for r in report["rows"])}
    print(f"bootstrap within-type on synthetic cells: {'PASS' if not boot_problems else 'FAIL ' + str(boot_problems)}")
    print(f"tokenizer parity: {report['tokenizer_parity'].get('status')}")
    print(f"TOTAL: {n_pass} PASS, {n_fail} FAIL   -> {out_dir}")
    if args.dry_run or args.fake_encoders:
        print("NOTE: dry-run / fake encoders: this verifies the runner, not M1.")
    return 1 if n_fail else 0

if __name__ == "__main__":
    sys.exit(main())
