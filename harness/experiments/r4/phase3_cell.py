"""Render Phase 3's Kaggle cells (AMENDMENTS "Phase 3, hidden dependencies, two stages"), pinned to a code commit and
to the task lists.

    python -m harness.experiments.r4.phase3_cell COMMIT
    # -> kaggle/r4_phase3_poc_cell.py (45 runs, shards 1-3 = seeds 42-44)
    #    kaggle/r4_phase3_cell.py (225 runs, shards 1-5 = seeds 101-105; run only after a GO)
    #    kaggle/r4_phase3_poc2_cell.py (POC 2: 45 runs, shards 1-3 = seeds 201-203)
    python -m harness.experiments.r4.phase3_cell COMMIT r4_phase3_poc2_cell     # only the cells named
"""
from __future__ import annotations

import hashlib
import json
import sys

from harness.experiments.r3.cell import ROOT, git

PHASE3_DOC = (
    "#   BASELINE_LSP the shared tools and an IDE agent's tools: find_references (jedi 0.19.2), semantic_search (BM25),\n"
    "#               ast_search (ast-grep 0.39.6), run_shell (read-only); no PRISM (r3/lsp_tools.py, r3/baseline_tools.py)\n"
    "#   PRISM_LSP   the shared tools and all of PRISM's tools, prism_find_references among them (no language server)\n"
    "#   PRISM_ONLY  PRISM's tools with prism_find_references, without grep and read_file\n"
    "# Every arm: the Phase 2 graph (reference pass, type tracking), and the harness feedback C1-C3 (--feedback).\n")
POC2_DOC = (
    "#   BASELINE_LSP the shared tools and an IDE agent's tools: find_references (jedi 0.19.2), semantic_search (BM25),\n"
    "#               ast_search (ast-grep 0.39.6), run_shell (read-only); no PRISM (r3/lsp_tools.py, r3/baseline_tools.py)\n"
    "#   PRISM_LSP   the shared tools and all of PRISM's tools, prism_find_references among them (no language server)\n"
    "#   PRISM_GW    PRISM_LSP, and PRISM's gateway (src/prism/gateway) on every call: it can block an edit or done,\n"
    "#               and adds what PRISM's graph knows to answers; every intervention is logged (AMENDMENTS, POC 2)\n"
    "# Every arm: the Phase 2 graph (reference pass, type tracking), the harness feedback C1-C3 (--feedback), and POC 2's\n"
    "# guards (--guards): the loop breaker, reference lists kept whole by compaction, edit_file's `occurrence`.\n")
_PHASE3 = {"@@DESIGN@@": "phase3", "@@ARMS@@": '("BASELINE_LSP", "PRISM_LSP", "PRISM_ONLY")', "@@ARMS_DOC@@": PHASE3_DOC,
           "@@EXTRA_FLAGS@@": "", "@@PREFLIGHT_FLAGS@@": ""}
STAGES = {
    "r4_phase3_poc_cell": {"stage": "poc", "tasks": "harness/experiments/r4/phase3/poc.json",
                           "seeds": "(42, 43, 44)", "results": "reports/r4/phase3_poc", **_PHASE3},
    "r4_phase3_cell": {"stage": "confirmatory", "tasks": "harness/experiments/r4/phase3/tasks.json",
                       "seeds": "(101, 102, 103, 104, 105)", "results": "reports/r4/phase3", **_PHASE3},
    # AMENDMENTS "Phase 3, POC 2"
    "r4_phase3_poc2_cell": {"stage": "poc", "tasks": "harness/experiments/r4/phase3/poc2.json",
                            "seeds": "(201, 202, 203)", "results": "reports/r4/phase3_poc2",
                            "@@DESIGN@@": "poc2", "@@ARMS@@": '("BASELINE_LSP", "PRISM_LSP", "PRISM_GW")',
                            "@@ARMS_DOC@@": POC2_DOC, "@@EXTRA_FLAGS@@": ', "--guards"',
                            "@@PREFLIGHT_FLAGS@@": ', "--guards"'},
}


def render(commit: str, only: tuple = ()) -> dict:
    commit = git("rev-parse", commit)
    template = (ROOT / "kaggle" / "r4_phase3_cell.template.py").read_text()
    out = {}
    for name, st in STAGES.items():
        if only and name not in only:
            continue
        doc = json.loads(git("show", f"{commit}:{st['tasks']}"))
        sha = hashlib.sha256(json.dumps(doc["tasks"], sort_keys=True).encode()).hexdigest()
        if sha != doc.get("tasks_sha256") or doc.get("design") != st["@@DESIGN@@"] or doc.get("stage") != st["stage"]:
            raise SystemExit(f"{st['tasks']} at {commit[:7]} is not a sealed {st['@@DESIGN@@']} {st['stage']} task list")
        fills = {"@@CODE_COMMIT@@": commit, "@@SRC_TREE@@": git("rev-parse", f"{commit}:src"),
                 "@@HARNESS_TREE@@": git("rev-parse", f"{commit}:harness"), "@@TASKS_SHA@@": sha,
                 "@@RESULTS_PATH@@": st["results"], "@@TASKS_PATH@@": st["tasks"], "@@SEEDS@@": st["seeds"],
                 "@@STAGE_KIND@@": st["stage"], "@@N_SHARDS@@": str(st["seeds"].count(",") + 1),
                 **{k: v for k, v in st.items() if k.startswith("@@")}}
        text = template
        for k, v in fills.items():
            if k not in text:
                raise SystemExit(f"{k} missing from the template")
            text = text.replace(k, v)
        if "@@" in text:
            raise SystemExit(f"an unfilled placeholder is left in {name}")
        (ROOT / "kaggle" / f"{name}.py").write_text(text)
        out[name] = {k: v for k, v in fills.items() if k in ("@@TASKS_SHA@@", "@@SEEDS@@")}
    return out


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        raise SystemExit(__doc__)
    print(render(args[0], tuple(args[1:])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
