"""Layer 4 benchmark runner: the 16 tasks x 7 Stage 2 arms x seeds.

    PYTHONPATH=.:src python -m harness.experiments.layer4.runner run \\
        --arms all --seeds 42,43,44,45,46,47,48,49,50,51 --out reports/layer4/run \\
        --python fastapi=<venv>/bin/python --python django=<venv>/bin/python --node <node20> \\
        --model qwen2.5-coder:14b-instruct-q8_0 --base-url http://127.0.0.1:11434/v1

    ... contamination --seeds ... --out reports/layer4/contamination   # arm0 first: the memorisation floor
    ... summarize --out reports/layer4/run [--contamination reports/layer4/contamination]
    ... run --dry-run ...                                              # contexts and prompts, no model

Per cell (task, arm, seed): the arm's context from the Stage 2 builders,
filtered and masked (`context.build_context`), the leak checks (a leaking
cell is recorded with status "leak" and never sent), the prompt
(`context.build_prompt`: the family's system prompt, the context, the task
prompt and its answer contract), one model call (two for prism_full: its
Turn 1 selection is part of building its context), and the score:

- forward  `tasks.score_forward` - P/R/F1 of the named affected symbols
  against the gold callers within the task's max_depth, names resolved to
  the corpus index; Django: tables and side-effect tags, exact match and
  P/R/F1;
- backward `tasks.score_backward` over `splice.evaluate` - the reply
  spliced into the checkout and the gating tests run: pass/fail, failure
  lines, a syntax check of the spliced file.

Cells are appended to `<out>/cells.jsonl` (`cell.schema.json`), one corpus at
a time and sequentially (splices edit the checkout in place); a rerun skips
cells already done. `summary.json` aggregates with `tasks.summarize`
(over all tasks and over tasks the arm0 run did not flag).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from harness.experiments.layer4 import context as X
from harness.experiments.layer4 import splice as S
from harness.experiments.layer4 import tasks as T

SCE = Path(__file__).resolve().parents[3]
TASKS_DIR = SCE / "benchmarks" / "layer4" / "tasks"
#: version of the backward answer protocol stamped on backward cells
#: (`detail.backward_answer`); an ok backward cell of an older one is run
#: again. v1 sent Stage 2's stop sequences, whose "\n```" is the answer's
#: own closing fence: every reply came back unterminated, the opening fence
#: was spliced in with the code and no candidate parsed. v2 sends none.
BACKWARD_ANSWER_VERSION = 2
#: version of the backward splice (`detail.backward_splice`). v1 kept the
#: `export` a full-definition reply repeats (the span starts after it), so
#: tRPC candidates spliced to `export export function ...` and never parsed.
#: A v2-answer cell whose reply v2 could splice differently is re-evaluated
#: from its stored reply - the same answer, no model call.
BACKWARD_SPLICE_VERSION = 2
#: version of the static index + manifest routing behind the forward
#: contexts of FORWARD_CONTEXT_ARMS (`detail.forward_context`); a forward
#: cell of those arms from an older version is run again. v2: blast tasks
#: get the blast-radius manifest (`direction="both"`) and the index gains
#: JS method aliases, closures, constructor callers, class values and
#: undefined-hook dispatch (Layer 4 forward gold callers in the static graph
#: 35 -> 48 of 63, in prism_full's candidate manifest 17 -> 49).
FORWARD_CONTEXT_VERSION = 2
FORWARD_CONTEXT_ARMS = ("prism_full",)
DONE = {"ok", "leak"}  # a rerun skips these (a leak only if the current check said so); errors are retried


# ---------------------------------------------------------------- tasks / cells
def load_tasks(directory: Path = TASKS_DIR) -> dict[str, dict]:
    tasks = {t["task_id"]: t for t in T.load_suite(directory)}
    errors = T.validate_suite(list(tasks.values()))
    if errors:
        raise SystemExit(f"task suite invalid: {errors}")
    return tasks


def cell_key(task_id: str, arm: str, seed: int) -> str:
    return f"{task_id}|{arm}|{seed}"


def load_cells(path: Path) -> dict[str, dict]:
    """Latest record per cell key (a crash mid-append leaves at most one bad line)."""
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            out[cell_key(r["task_id"], r["arm"], r["seed"])] = r
    return out


# ---------------------------------------------------------------- backward evaluation
#: cap on one backward evaluation (the task's own timeout_s is the gates' 1800 s):
#: a wrong candidate can hang the corpus's tests, and a timeout scores as a fail
EVAL_TIMEOUT_S = 300


def splice_task(task: dict, corpus, pythons: dict, node: str, timeout: int | None = None) -> S.SpliceTask:
    from harness.experiments.layer4 import author as A
    A.NODE["bin"] = node
    g = task["gold"]
    cmd = A.expand(g["test_command"], corpus.clone_root, pythons.get(task["corpus"]))
    if any("{" in part for part in cmd):
        raise RuntimeError(f"{task['task_id']}: unresolved placeholder in {cmd} (pass --python / --node)")
    return S.SpliceTask(task["task_id"], corpus.clone_root, task["focal"]["file"], task["focal"]["line"], cmd,
                        cwd=g.get("cwd", "."), timeout=min(g.get("timeout_s", 900), timeout or 10**9),
                        name=task["focal"]["qname"].rsplit(".", 1)[-1],
                        env={k: str(v) for k, v in (g.get("env") or {}).items()})


# ---------------------------------------------------------------- one cell
def run_cell(corpus, task: dict, arm: str, seed: int, cfg, client, pythons: dict, node: str,
             evaluate=S.evaluate, build=X.build_context) -> dict:
    from benchmarks.final_sweep.runner import CellRecord, _CallLog, sampler_kwargs
    started = time.monotonic()
    rec = {"schema_version": "layer4.cell.v1", "task_id": task["task_id"], "family": task["family"],
           "corpus": task["corpus"], "arm": arm, "engine_id": arm, "seed": seed, "model": cfg.model, "status": "error",
           "error": None, "tokens": {"context": 0, "prompt": 0, "completion": 0, "llm_calls": 0},
           "leak": {"focal_body_lines": 0, "test_files": [], "trace_markers": []}, "selected_symbols": [],
           "reply": "", "score": None, "detail": {}, "calls": []}
    log = _CallLog(local=cfg.local, num_ctx=cfg.num_ctx)
    try:
        stage2 = CellRecord(repo=task["corpus"], task_id=task["task_id"], engine_id=arm, seed=seed, budget=cfg.budget,
                            token_ceiling=cfg.token_ceiling, model_requested=cfg.model, temperature=cfg.temperature)
        ctx = build(corpus, arm, task, seed, cfg, client, log, stage2)
        system, user = X.build_prompt(task, ctx["rendered"])
        prompt_leaks = X.prompt_leaks(task["prompt"], task)
        rec.update(engine_id=ctx["engine_id"], selected_symbols=ctx["selected_symbols"], leak=ctx["leak"],
                   context_hash=T_hash(ctx["rendered"]))
        rec["tokens"]["context"] = ctx["context_tokens"]
        rec["detail"] = {k: ctx[k] for k in ("context_tokens_pre_ceiling", "n_ceiling_dropped", "dropped_test_nodes",
                                             "masked_nodes", "dropped_nested_nodes", "focal_in_context", "manifest_direction")
                         if k in ctx and ctx[k] is not None}
        rec["detail"]["prompt_leaks"] = prompt_leaks
        rec["detail"]["leak_check"] = ctx.get("leak_check", X.LEAK_CHECK_VERSION)
        if task["family"] == "forward_blast" and arm in FORWARD_CONTEXT_ARMS:
            rec["detail"]["forward_context"] = FORWARD_CONTEXT_VERSION
        if task["family"] == "backward_adaptation":
            rec["detail"]["backward_answer"] = BACKWARD_ANSWER_VERSION
            rec["detail"]["backward_splice"] = BACKWARD_SPLICE_VERSION
        if ctx["leaked"] or prompt_leaks:
            rec["status"] = "leak"
        elif client is None:
            rec["status"] = "dry_run"
            rec["detail"]["prompt_chars"] = len(system) + len(user)
        else:
            forward = task["family"] == "forward_blast"
            call = client.complete(cfg.model, system, user, temperature=cfg.temperature, max_tokens=cfg.max_tokens,
                                   seed=seed, task_id=task["task_id"], engine=f"{ctx['engine_id']}/answer",
                                   response_format={"type": "json_object"} if forward else None,
                                   **({} if forward else {"stop": None}),  # forward: Stage 2's stop sequences
                                   **sampler_kwargs(cfg, "answer"))
            log.add("answer", call)
            rec["reply"] = call.content or ""
            if forward:
                rec["score"] = T.score_forward(rec["reply"], task["gold"], corpus.known)
            else:
                result = evaluate(splice_task(task, corpus, pythons, node, timeout=EVAL_TIMEOUT_S), rec["reply"])
                rec["score"] = T.score_backward(result)
            rec["status"] = "ok"
    except Exception as exc:  # one broken cell never stops the run
        rec["status"] = "error"
        rec["error"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}"[:4000]
    rec["calls"] = log.calls
    rec["tokens"].update(prompt=sum(c["prompt_tokens"] for c in log.calls),
                         completion=sum(c["completion_tokens"] for c in log.calls), llm_calls=len(log.calls))
    rec["wall_time_s"] = round(time.monotonic() - started, 3)
    rec["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    errors = T.schema_errors(rec, T.CELL_SCHEMA)
    if errors:  # a runner bug, never a model's fault: keep the cell, mark it
        rec["status"], rec["error"] = "error", f"cell record invalid: {errors}"
    return rec


def T_hash(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------- the run
def needs_resplice(rec: dict) -> bool:
    """An ok backward cell of the current answer protocol scored by an older
    splice that its reply could splice differently under (conservative: any
    block opening with `export` or a decorator)."""
    return (rec["status"] == "ok" and rec.get("family") == "backward_adaptation"
            and (rec.get("detail") or {}).get("backward_answer") == BACKWARD_ANSWER_VERSION
            and (rec.get("detail") or {}).get("backward_splice") != BACKWARD_SPLICE_VERSION
            and S.PREFIX_SENSITIVE.search(rec.get("reply") or "") is not None)


def reevaluate_cell(corpus, task: dict, rec: dict, pythons: dict, node: str, evaluate=S.evaluate) -> dict:
    """`rec` scored again from its stored reply by the current splice: same
    context, prompt, answer and tokens; new score."""
    out = json.loads(json.dumps(rec, default=str))
    started = time.monotonic()
    try:
        out["score"] = T.score_backward(evaluate(splice_task(task, corpus, pythons, node, timeout=EVAL_TIMEOUT_S), out["reply"]))
        out["status"], out["error"] = "ok", None
    except Exception as exc:
        out["status"] = "error"
        out["error"] = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}"[:4000]
    out["detail"]["backward_splice"] = BACKWARD_SPLICE_VERSION
    out["detail"]["rescored_from_stored_reply"] = True
    out["wall_time_s"] = round(rec.get("wall_time_s", 0) + time.monotonic() - started, 3)
    out["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    errors = T.schema_errors(out, T.CELL_SCHEMA)
    if errors:
        out["status"], out["error"] = "error", f"cell record invalid: {errors}"
    return out


def is_done(rec: dict, dry_run: bool = False) -> bool:
    """A finished cell a rerun skips: ok, or a leak the current leak check
    found (one from an older check is re-evaluated: the check only gates
    sending, so an ok cell stays valid across versions). An ok backward
    cell also needs the current backward answer protocol."""
    detail = rec.get("detail") or {}
    if rec.get("family") == "forward_blast" and rec.get("arm") in FORWARD_CONTEXT_ARMS \
            and rec["status"] in DONE and detail.get("forward_context") != FORWARD_CONTEXT_VERSION:
        return False
    if rec["status"] == "leak":
        return detail.get("leak_check") == X.LEAK_CHECK_VERSION
    if rec["status"] == "ok" and rec.get("family") == "backward_adaptation":
        return detail.get("backward_answer") == BACKWARD_ANSWER_VERSION and not needs_resplice(rec)
    return rec["status"] in ({"dry_run"} | DONE if dry_run else DONE)


def run(tasks: dict[str, dict], arms: list[str], seeds: list[int], out: Path, cfg, client, pythons: dict, node: str,
        load_corpus=X.Corpus.load) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    cells_path = out / "cells.jsonl"
    existing = load_cells(cells_path)
    done = {k for k, r in existing.items() if is_done(r, dry_run=client is None)}
    by_corpus: dict[str, list[dict]] = {}
    for t in tasks.values():
        by_corpus.setdefault(t["corpus"], []).append(t)
    written = []
    if cells_path.exists() and cells_path.stat().st_size and not cells_path.read_bytes().endswith(b"\n"):
        with cells_path.open("a") as fh:  # a kill mid-append left a partial line: never glue the next record to it
            fh.write("\n")
    with cells_path.open("a") as fh:
        for corpus_name in sorted(by_corpus):
            todo = [(t, a, s) for t in sorted(by_corpus[corpus_name], key=lambda t: t["task_id"]) for a in arms
                    for s in seeds if cell_key(t["task_id"], a, s) not in done]
            if not todo:
                continue
            print(f"[corpus] {corpus_name}: {len(todo)} cells", flush=True)
            corpus = load_corpus(corpus_name)
            for task, arm, seed in todo:
                old = existing.get(cell_key(task["task_id"], arm, seed))
                if old is not None and client is not None and needs_resplice(old):
                    rec = reevaluate_cell(corpus, task, old, pythons, node)
                else:
                    rec = run_cell(corpus, task, arm, seed, cfg, client, pythons, node)
                fh.write(json.dumps(rec, default=str) + "\n")
                fh.flush()
                written.append(rec)
                score = rec["score"] or {}
                shown = score.get("f1", score.get("passed", ""))
                print(f"  [{rec['status']}] {task['task_id']} {arm} seed {seed}"
                      f"{' score ' + str(shown) if shown != '' else ''} ctx {rec['tokens']['context']}"
                      f"{' ' + rec['error'].splitlines()[0] if rec['error'] else ''}", flush=True)
            del corpus
    return written


def summarize(out: Path, tasks: dict[str, dict], contamination_dir: Path | None = None) -> dict:
    """`tasks.summarize` over the run's cells. With `contamination_dir`, that
    arm0 run's cells join the pool (deduplicated by cell key), so the
    contamination flags - and the unflagged view - come from it."""
    pool = load_cells(out / "cells.jsonl")
    if contamination_dir:
        for key, rec in load_cells(contamination_dir / "cells.jsonl").items():
            if rec["arm"] == "arm0_no_retrieval":
                pool.setdefault(key, rec)
    cells = list(pool.values())
    summary = T.summarize(cells, tasks)
    summary["status_counts"] = {}
    for c in cells:
        summary["status_counts"][c["status"]] = summary["status_counts"].get(c["status"], 0) + 1
    (out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    return summary


def contamination_report(out: Path, tasks: dict[str, dict]) -> dict:
    cells = [c for c in load_cells(out / "cells.jsonl").values() if c["arm"] == "arm0_no_retrieval"]
    flags = T.contamination(cells, tasks)
    report = {"threshold": {"backward_pass_rate": T.CONTAMINATION_THRESHOLD, "forward_f1": T.FORWARD_CONTAMINATION_F1},
              "tasks": {tid: flags.get(tid, {"arm0_score": None, "flagged": None, "seeds": 0}) for tid in sorted(tasks)},
              "flagged": sorted(t for t, f in flags.items() if f["flagged"])}
    (out / "contamination.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    return report


# ---------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    from benchmarks.final_sweep import config as C
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("run", "contamination", "summarize"):
        s = sub.add_parser(name)
        s.add_argument("--out", required=True, type=Path)
        s.add_argument("--tasks-dir", type=Path, default=TASKS_DIR)
        if name == "summarize":
            s.add_argument("--contamination", type=Path, help="the arm0 run whose flags to use")
            continue
        if name == "run":
            s.add_argument("--arms", default="all", help=f"comma-separated, or all: {','.join(X.ARMS)}")
        s.add_argument("--tasks", default="all", help="comma-separated task ids, or all")
        s.add_argument("--corpus", help="only this corpus's tasks")
        s.add_argument("--seeds", default="42,43,44,45,46,47,48,49,50,51")
        s.add_argument("--python", action="append", default=[], help="corpus=interpreter (fastapi, django)")
        s.add_argument("--node", default="node", help="Node binary (tRPC needs Node 20)")
        s.add_argument("--model", default=C.DEFAULT_MODEL)
        s.add_argument("--base-url", default=C.DEFAULT_BASE_URL)
        s.add_argument("--api-key-env", default=C.DEFAULT_API_KEY_ENV)
        s.add_argument("--temperature", type=float, default=C.DEFAULT_TEMPERATURE)
        s.add_argument("--max-tokens", type=int, default=C.DEFAULT_MAX_TOKENS)
        s.add_argument("--budget", type=int, default=C.DEFAULT_BUDGET)
        s.add_argument("--token-ceiling", type=int, default=C.DEFAULT_TOKEN_CEILING)
        s.add_argument("--num-ctx", type=int, default=24_576)
        s.add_argument("--dry-run", action="store_true", help="build contexts and prompts, no model calls")
        s.add_argument("--eval-timeout", type=int, default=EVAL_TIMEOUT_S,
                       help="seconds one backward evaluation may take (a timeout is a fail)")
    return p


def install_sigterm_exit() -> None:
    """A terminated runner (Kaggle's time limit, the cell's lane stop) must
    still restore a spliced file: SIGTERM becomes SystemExit, so `edited`'s
    finally runs. Python's default SIGTERM ends the process without it."""
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))


def main(argv=None) -> int:
    global EVAL_TIMEOUT_S
    install_sigterm_exit()
    args = build_parser().parse_args(argv)
    EVAL_TIMEOUT_S = getattr(args, "eval_timeout", EVAL_TIMEOUT_S)
    tasks = load_tasks(args.tasks_dir)
    if args.command == "summarize":
        s = summarize(args.out, tasks, args.contamination)
        print(json.dumps({"status_counts": s["status_counts"], "flagged_tasks": s["flagged_tasks"]}, indent=1))
        return 0
    from benchmarks.final_sweep.runner import SweepConfig, build_client
    if args.tasks != "all":
        wanted = set(args.tasks.split(","))
        tasks = {k: v for k, v in tasks.items() if k in wanted}
    if args.corpus:
        tasks = {k: v for k, v in tasks.items() if v["corpus"] == args.corpus}
    arms = ["arm0_no_retrieval"] if args.command == "contamination" else (
        list(X.ARMS) if args.arms == "all" else args.arms.split(","))
    unknown = set(arms) - set(X.ARMS)
    if unknown:
        raise SystemExit(f"unknown arms {sorted(unknown)}; known: {X.ARMS}")
    cfg = SweepConfig(model=args.model, temperature=args.temperature, budget=args.budget,
                      token_ceiling=args.token_ceiling, max_tokens=args.max_tokens, base_url=args.base_url,
                      api_key_env=args.api_key_env, dry_run=args.dry_run, num_ctx=args.num_ctx)
    cfg.tpm_limit = None if cfg.local else cfg.tpm_limit  # as Stage 2: no client throttle on a local server
    client = build_client(cfg)
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    pythons = dict(x.split("=", 1) for x in args.python)
    args.out.mkdir(parents=True, exist_ok=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=SCE, capture_output=True, text=True).stdout.strip()
    (args.out / "run_manifest.json").write_text(json.dumps({
        "command": args.command, "sce_head": head, "arms": arms, "seeds": seeds, "tasks": sorted(tasks),
        "task_hashes": {k: T_hash(json.dumps(v, sort_keys=True)) for k, v in sorted(tasks.items())},
        "config": dataclasses.asdict(cfg) | {"api_key_env": args.api_key_env}, "dry_run": args.dry_run,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=1, default=str) + "\n")
    run(tasks, arms, seeds, args.out, cfg, client, pythons, args.node)
    if args.command == "contamination":
        report = contamination_report(args.out, load_tasks(args.tasks_dir))
        print(json.dumps({"flagged": report["flagged"]}, indent=1))
    else:
        s = summarize(args.out, load_tasks(args.tasks_dir))
        print(json.dumps({"status_counts": s["status_counts"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
