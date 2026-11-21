"""Run a suite task type: model answers (Kaggle) and scores.

    python -m harness.experiments.suite.run generate TYPE TASKS.json OUT_DIR [--base-url URL] [--model M] [--threads 1]
    python -m harness.experiments.suite.run score TYPE TASKS.json OUT_DIR

TYPE is `bugs` (type 3), `newfn` (type 2), `blast` (type 4) or `concepts` (type 5). Type 6 (`testgen`) uses this `generate`, but is scored by running
the tests (`testgen score`). Type 1 runs with `harness.experiments.change_history.run`.

The model settings are those of every earlier run: temperature 0.2, seed 42, a 20,480-token window and
answers of at most 4,096 tokens, JSON output. Answers are saved as they come (resumable), with Ollama's
prompt and answer token counts and its stop reason.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import importlib
import json
import sys
from pathlib import Path

from harness.experiments.change_history.run import MODEL, ollama

TYPES = {"bugs": "harness.experiments.suite.bugs", "newfn": "harness.experiments.suite.newfn",
         "testgen": "harness.experiments.suite.testgen", "blast": "harness.experiments.suite.blast",
         "concepts": "harness.experiments.suite.concepts"}


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def generate(kind: str, tasks: list[dict], out: Path, base_url: str, model: str, threads: int = 1) -> None:
    mod = importlib.import_module(TYPES[kind])
    path = out / "answers.json"
    answers = _load(path)
    todo = [(t, arm) for t in tasks for arm in mod.ARMS if f"{t['id']}/{arm}" not in answers]
    print(f"{len(todo)} model calls to make ({len(answers)} done)", flush=True)

    def one(item):
        t, arm = item
        try:
            stats: dict = {}
            text, secs = ollama(base_url, model, mod.prompt(t, arm), stats=stats)
            return f"{t['id']}/{arm}", {"raw": text, "seconds": round(secs, 1), "model": model, **stats}
        except Exception as exc:  # recorded; scored as no answer
            return f"{t['id']}/{arm}", {"raw": "", "error": f"{type(exc).__name__}: {exc}", "model": model}

    with concurrent.futures.ThreadPoolExecutor(threads) as pool:
        for key, value in pool.map(one, todo):
            answers[key] = value
            path.write_text(json.dumps(answers, indent=1, sort_keys=True) + "\n")
            print(f"  {key}: {value.get('seconds', '-')}s {value.get('done_reason', '')} {value.get('error', '')}",
                  flush=True)


def score(kind: str, tasks: list[dict], out: Path, outer_fence: bool = False) -> dict:
    """`outer_fence` (newfn only): the outer-fence sensitivity analysis, written to `scores_fence.json`;
    `scores.json`, the pre-registered strict score, is left as it is."""
    mod = importlib.import_module(TYPES[kind])
    answers = _load(out / "answers.json")
    extra = {"outer_fence": True} if outer_fence else {}
    rows = {}
    for t in tasks:
        for arm in mod.ARMS:
            a = answers.get(f"{t['id']}/{arm}")
            if a is None:
                continue
            rows[f"{t['id']}/{arm}"] = {"task": t["id"], "repo": t["repo"], "arm": arm,
                                        **mod.score_task(t, a.get("raw", ""), **extra),
                                        "seconds": a.get("seconds"), "prompt_tokens": a.get("prompt_tokens"),
                                        "answer_tokens": a.get("answer_tokens"), "done_reason": a.get("done_reason")}
    name = "scores_fence.json" if outer_fence else "scores.json"
    (out / name).write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n")
    print(f"{len(rows)} answers scored -> {out / name}")
    return rows


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("cmd", choices=("generate", "score"))
    p.add_argument("kind", choices=sorted(TYPES))
    p.add_argument("tasks")
    p.add_argument("out")
    p.add_argument("--base-url", default="http://127.0.0.1:11434")
    p.add_argument("--model", default=MODEL)
    p.add_argument("--threads", type=int, default=1)
    p.add_argument("--outer-fence", action="store_true", help="score: the outer-fence sensitivity analysis (newfn)")
    args = p.parse_args(argv)
    if args.outer_fence and args.kind != "newfn":
        raise SystemExit("--outer-fence applies to newfn only: the other types' answers are lists of names")
    data = json.loads(Path(args.tasks).read_text())
    if data.get("status") != "frozen":
        raise SystemExit("tasks are not frozen")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.cmd == "generate":
        generate(args.kind, data["tasks"], out, args.base_url, args.model, args.threads)
    else:
        score(args.kind, data["tasks"], out, outer_fence=args.outer_fence)
    return 0


if __name__ == "__main__":
    sys.exit(main())
