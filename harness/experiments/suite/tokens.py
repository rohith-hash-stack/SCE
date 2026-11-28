"""Real token counts against PRISM's estimate (`prism.slicer.tokenizer.count_tokens`), from the suite's frozen prompts.

    python -m harness.experiments.suite.tokens [TOKENIZER_JSON]      # -> reports/suite/TOKENS.json

1. Estimate vs real, for each PRISM context: the ratio `count_tokens / real`, by task type and language, with its
   5th and 95th percentiles. A budget margin for the product has to come from the low end (where the estimate
   undercounts), not from the mean.
2. Harness vs Ollama: the real count of each prompt as the harness builds it, against the `prompt_tokens` Ollama
   reported for that answer. The difference is the chat template's overhead; it should be a near-constant.

The tokenizer is the evaluating model's (Qwen2's `tokenizer.json`); it measures the suite, it is not for the product,
which serves other models.
"""
from __future__ import annotations

import importlib
import json
import statistics
import sys
from pathlib import Path

ROOT = Path("harness/experiments/suite/repos")
OUT = Path("reports/suite")
DEFAULT_TOKENIZER = "/home/user/models/qwen2-tokenizer/tokenizer.json"
PY = {"werkzeug", "marshmallow", "rich", "tornado", "aiohttp", "scrapy"}
# type -> (task file, module with prompt(task, arm), answers dir, the PRISM version)
TYPES = {"forward": ("forward.json", "harness.experiments.change_history.run", "forward", "PRISM"),
         "bugs": ("bugs.json", "harness.experiments.suite.bugs", "bugs", "PRISM"),
         "blast": ("blast.json", "harness.experiments.suite.blast", "blast", "PRISM"),
         "concepts": ("concepts.json", "harness.experiments.suite.concepts", "concepts", "PRISM"),
         "newfn": ("newfn.json", "harness.experiments.suite.newfn", "newfn", "PRISM"),
         "tests": ("tests.json", "harness.experiments.suite.testgen", "tests", "PRISM")}


def _summary(xs: list[float]) -> dict:
    if not xs:
        return {}
    xs = sorted(xs)
    pick = lambda q: round(xs[min(len(xs) - 1, int(q * len(xs)))], 3)
    return {"n": len(xs), "mean": round(statistics.mean(xs), 3), "p5": pick(0.05), "median": pick(0.5), "p95": pick(0.95)}


def _tasks(repo_dir: Path, name: str) -> list[dict]:
    path = repo_dir / name
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return data["tasks"] if isinstance(data, dict) else data


def measure(tokenizer_path: str = DEFAULT_TOKENIZER) -> dict:
    from tokenizers import Tokenizer
    from prism.slicer.tokenizer import count_tokens
    tok = Tokenizer.from_file(tokenizer_path)
    real = lambda text: len(tok.encode(text).ids)
    ratio: dict[str, list[float]] = {}
    overhead: dict[str, list[int]] = {}
    for kind, (fname, modname, adir, arm) in TYPES.items():
        mod = importlib.import_module(modname)
        for repo_dir in sorted(p for p in ROOT.iterdir() if p.is_dir()):
            lang = "python" if repo_dir.name in PY else "javascript"
            answers = {}
            ans_path = OUT / repo_dir.name / adir / "answers.json"
            if ans_path.exists():
                answers = json.loads(ans_path.read_text())
            for t in _tasks(repo_dir, fname):
                ctx = t.get("prism_context") or ""
                if ctx.strip():
                    r = real(ctx)
                    if r:
                        for key in (kind, f"{kind}/{lang}", "all"):
                            ratio.setdefault(key, []).append(count_tokens(ctx) / r)
                a = answers.get(f"{t['id']}/{arm}") or {}
                if a.get("prompt_tokens"):
                    overhead.setdefault(kind, []).append(int(a["prompt_tokens"]) - real(mod.prompt(t, arm)))
    return {"tokenizer": tokenizer_path,
            "estimate_over_real": {k: _summary(v) for k, v in sorted(ratio.items())},
            "ollama_minus_harness": {k: _summary([float(x) for x in v]) for k, v in sorted(overhead.items())}}


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    res = measure(args[0] if args else DEFAULT_TOKENIZER)
    (OUT / "TOKENS.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
