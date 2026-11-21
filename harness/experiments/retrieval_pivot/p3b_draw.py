"""P3b focal draw: the blind, seeded draw of forward-task focal symbols, and the specs it implies.

harness/experiments/retrieval_pivot/P3.md, "Task authoring and freezing". Per corpus (django,
fastapi, express, trpc):

1. **Candidates.** The indexed library functions and methods - `layer4.context.Corpus.load`'s own
   `known` set (outside test files and the example zone), inside the library package
   (`LIBRARY_PREFIX`: `django.`, `fastapi.`, Express's `lib.`, tRPC's indexed server package). Dropped:
   - overload variants (`#2`);
   - functions nested in another function: the tracer folds closures into their enclosing function,
     so a nested focal never runs as itself;
   - every name of the 16 existing Layer 4 tasks (focal, gold, and every ancestor, descendant and
     oracle symbol of their trace manifests);
   - every query_to_evidence name (subjects, required and context symbols, relationship ends). P1's
     29 seeds are among these.
2. **Order.** `random.Random(f"p3b:{SEED}:{corpus}")` shuffles the sorted candidates. Nothing about
   the graph (callers, degree, reach) or any arm is used.
3. **Specs, in draw order.** The trace for each candidate is chosen mechanically, by plain text:
   test files under the corpus's test directory that contain the focal's name as a word
   (`git grep -w`). A candidate no test file mentions is skipped (`no_test_mention`); the trace
   could not run it; one mentioned only where no trace can run is skipped as `no_runnable_test`.
   Trace commands:
   - fastapi and express: the whole suite, as the existing tasks;
   - django: the test apps that mention it (at most 3, most mentions first), never an app the
     SQLite settings cannot run (`DJANGO_UNRUNNABLE_APPS`);
   - trpc: the test files that mention it (at most 8).

   The change is one fixed template per language (a new required `context` argument). The gold
   comes from the trace and does not depend on the wording, and the template names no caller.
   `max_depth` is 2.

`SPECS_PER_CORPUS` specs per corpus go to `benchmarks/layer4/specs_p3b.py` (generated - never edited
by hand); the full draw goes to `benchmarks/layer4_p3b/draw.json`. `author.py --specs` then keeps
the first 8 eligible per corpus, in this order. If 8 are not reached, more specs are generated from
the same draw (`--n` larger), never a new draw. Amendment 1 (P3.md) raised `--n` to 96 and dropped
the unrunnable Django apps; the pilot run's draw and logs are kept in `benchmarks/layer4_p3b/pilot1/`.

Usage: python -m harness.experiments.retrieval_pivot.p3b_draw [--n SPECS_PER_CORPUS]
"""
from __future__ import annotations

import argparse
import glob
import json
import random
import subprocess
import sys
from collections import Counter
from pathlib import Path

SCE = Path(__file__).resolve().parents[3]
SEED = 20261024
CORPORA = ("django", "fastapi", "express", "trpc")
#: amendment 1 (P3.md): 96, enough to draw Express (30), tRPC (63) and FastAPI (89) to exhaustion; the
#: pilot run had 24. The order is unchanged - only more of it is written out.
SPECS_PER_CORPUS = 96
OUT_DIR = SCE / "benchmarks" / "layer4_p3b"
SPECS_FILE = SCE / "benchmarks" / "layer4" / "specs_p3b.py"
#: corpus -> (test directory relative to the clone root, language)
TEST_DIRS = {"django": ("tests", "python"), "fastapi": ("tests", "python"), "express": ("test", "javascript"),
             "trpc": ("packages/tests", "typescript")}
#: corpus -> qualified-name prefix of its library package (tRPC: the whole index is packages/server/src)
LIBRARY_PREFIX = {"django": "django.", "fastapi": "fastapi.", "express": "lib.", "trpc": ""}
MAX_DJANGO_LABELS = 3
#: Django test apps the SQLite test settings cannot run (amendment 1): gis_tests aborts the whole
#: run without a GIS backend; postgres_tests needs PostgreSQL. Never chosen as trace labels.
DJANGO_UNRUNNABLE_APPS = frozenset({"gis_tests", "postgres_tests"})
MAX_TRPC_FILES = 8
TEMPLATES = {
    "python": "{name}() gains a required keyword-only argument `context` (a mapping of per-call settings); "
              "calls that do not pass it raise TypeError.",
    "javascript": "{name}() takes a new required last parameter `context` (an object of per-call settings) and "
                  "throws a TypeError when it is missing.",
}
TEMPLATES["typescript"] = TEMPLATES["javascript"]


def excluded_names() -> dict[str, set[str]]:
    """corpus -> every name the existing Layer 4 tasks and query_to_evidence already use."""
    out: dict[str, set[str]] = {c: set() for c in CORPORA}
    for path in glob.glob(str(SCE / "benchmarks/layer4/tasks/l4_*.json")):
        task = json.loads(Path(path).read_text())
        names = out.setdefault(task["corpus"], set())
        names.add(task["focal"]["qname"])
        names |= set(task["gold"].get("affected_symbols") or [])
        manifest = json.loads(Path(path.replace("/tasks/", "/manifests/")).read_text())
        for key in ("ancestors", "descendants", "oracle_callers", "oracle_callees", "focal"):
            value = manifest.get(key) or {}
            names |= set(value) if isinstance(value, (dict, list)) else set()
    q2e = SCE / "harness/experiments/query_to_evidence/dataset"
    questions = {q["id"]: q for q in json.loads((q2e / "questions.json").read_text())["questions"]}
    for qid, gold in json.loads((q2e / "gold.json").read_text())["gold"].items():
        names = out.setdefault(questions[qid]["corpus"], set())
        for req in gold.get("requirements", []):
            names |= {req.get("subject")} | set(req.get("required_symbols") or []) | set(req.get("context_symbols") or [])
            for e in req.get("relationship_paths") or []:
                names |= {e.get("from"), e.get("to")}
    return {c: {n for n in v if n} for c, v in out.items()}


def candidates(corpus, excluded: set[str]) -> tuple[list[str], Counter]:
    b = corpus.state.builder
    dropped: Counter = Counter()
    keep = []
    for q in sorted(corpus.known):
        info = b.symbol_table.get(q)
        parent = b.symbol_table.get(q.rsplit(".", 1)[0]) if "." in q else None
        if not q.startswith(LIBRARY_PREFIX[corpus.name]):
            dropped["outside_library_package"] += 1
        elif "#" in q:
            dropped["overload_variant"] += 1
        elif parent is not None and parent.kind in ("function", "method"):
            dropped["nested_function"] += 1
        elif q in excluded:
            dropped["used_by_existing_tasks"] += 1
        elif info is None:
            dropped["not_indexed"] += 1
        else:
            keep.append(q)
    return keep, dropped


def leaf(qname: str) -> str:
    parts = qname.split(".")
    return parts[-2] if parts[-1] in ("__init__", "constructor") and len(parts) > 1 else parts[-1]


def display(qname: str, kind: str) -> str:
    parts = qname.split(".")
    return ".".join(parts[-2:]) if kind == "method" and len(parts) > 1 else parts[-1]


def mentions(clone: str, test_dir: str, name: str) -> list[str]:
    r = subprocess.run(["git", "grep", "-l", "-w", "-F", "-e", name, "--", test_dir], cwd=clone,
                       capture_output=True, text=True)
    if r.returncode not in (0, 1):
        raise RuntimeError(f"git grep failed in {clone}: {r.stderr.strip()[:200]}")
    return sorted(r.stdout.split())


def trace_for(corpus: str, files: list[str]) -> dict:
    from benchmarks.layer4 import specs as base
    if corpus == "fastapi":
        return {"trace": base.PYTEST + ["tests", *base.FASTAPI_DESELECT, *base.FASTAPI_NO_WEBSOCKETS]}
    if corpus == "express":
        return {"trace": base.MOCHA + [base.EXPRESS_UNIT]}
    if corpus == "django":
        apps = Counter(f.split("/")[1] for f in files if f.count("/") >= 2 and f.split("/")[1] not in DJANGO_UNRUNNABLE_APPS)
        labels = [a for a, _ in sorted(apps.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_DJANGO_LABELS]]
        return {"trace_labels": labels} if labels else {}
    rel = [f.removeprefix("packages/") for f in files if f.endswith((".test.ts", ".test.tsx"))]
    return {"trace": base.VITEST + rel[:MAX_TRPC_FILES]} if rel else {}


def draw(n: int = SPECS_PER_CORPUS) -> dict:
    from harness.experiments.layer4.context import Corpus
    excluded = excluded_names()
    record = {"seed": SEED, "specs_per_corpus": n, "corpora": {}}
    specs = []
    for name in CORPORA:
        corpus = Corpus.load(name)
        b = corpus.state.builder
        keep, dropped = candidates(corpus, excluded[name])
        order = list(keep)
        random.Random(f"p3b:{SEED}:{name}").shuffle(order)
        test_dir, language = TEST_DIRS[name]
        skipped, chosen = [], []
        for rank, q in enumerate(order, 1):
            if len(chosen) >= n:
                break
            info = b.symbol_table.get(q)
            files = mentions(corpus.clone_root, test_dir, leaf(q))
            trace = trace_for(name, files) if files else {}
            if not trace:
                reason = "no_test_mention" if not files else "no_runnable_test"  # unrunnable apps / no .test.ts
                skipped.append({"rank": rank, "qname": q, "reason": reason})
                continue
            spec = {"task_id": f"p3b_fwd_{name}_{rank:04d}", "corpus": name, "family": "forward_blast", "qname": q,
                    "change": {"kind": "signature",
                               "description": TEMPLATES[language].format(name=display(q, info.kind))},
                    **trace, "max_depth": 2, "draw_rank": rank}
            chosen.append(spec)
        specs += chosen
        record["corpora"][name] = {"known": len(corpus.known), "dropped": dict(dropped), "candidates": len(order),
                                   "order": order, "skipped_before_specs": skipped,
                                   "specs": [s["task_id"] for s in chosen]}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "draw.json").write_text(json.dumps(record, indent=1) + "\n")
    write_specs(specs, n)
    return record


def write_specs(specs: list[dict], n: int) -> None:
    body = ",\n".join("    " + json.dumps(s) for s in specs)
    SPECS_FILE.write_text(f'''"""P3b forward-task specs - GENERATED by harness/experiments/retrieval_pivot/p3b_draw.py
(seed {SEED}, {n} per corpus, draw order). Do not edit: regenerate from the same draw.

`author.py --specs benchmarks/layer4/specs_p3b.py --eligibility p3b` traces them in order and keeps the
first 8 eligible per corpus (P3.md). Full draw and skips: benchmarks/layer4_p3b/draw.json.
"""
SEED = {SEED}
SPECS = [
{body},
]
''')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--n", type=int, default=SPECS_PER_CORPUS, help="specs per corpus, in draw order")
    a = ap.parse_args(argv)
    rec = draw(a.n)
    for c, r in rec["corpora"].items():
        print(f"{c}: known {r['known']}, dropped {r['dropped']}, candidates {r['candidates']}, "
              f"skipped (no test mention) {len(r['skipped_before_specs'])}, specs {len(r['specs'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
