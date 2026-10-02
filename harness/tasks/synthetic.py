"""Five hand-made tasks, one per type, for the type-coverage gate.

They check that every arm's adapter and every scorer branch handle every
task type. They do NOT measure quality. T1/T2/T5 are grounded in real
symbols of the pinned FastAPI checkout (fastapi/dependencies/utils.py);
T3/T4 are self-contained.
"""
from __future__ import annotations

from harness.tasks.schema import (BlastRadiusTask, CodeGenTask, ConceptualTask, EditTask, GoldSpan,
                                  GroundTruth, LocalizationTask)

UTILS = "fastapi.dependencies.utils"
TARGET = f"{UTILS}.get_typed_annotation"
CALLERS = [f"{UTILS}.get_typed_signature", f"{UTILS}.get_typed_return_annotation"]


def synthetic_tasks(repo_root: str, repo_id: str = "fastapi") -> list:
    common = {"repo_id": repo_id, "repo_root": repo_root, "source": "synthetic"}
    return [
        ConceptualTask(
            task_id="syn_t1_conceptual", query=f"What does `{TARGET}` do?", seed_symbol=TARGET,
            ground_truth=GroundTruth(pipeline_symbols=[TARGET]),
            reference_answer="It turns a string annotation into a ForwardRef and evaluates it against the "
                             "function's globals, returning the resolved annotation (non-strings pass through).",
            **common),
        LocalizationTask(
            task_id="syn_t2_localization", query=f"Where is `{TARGET}` defined?", seed_symbol=TARGET,
            ground_truth=GroundTruth(pipeline_symbols=[TARGET]),
            gold_spans=[GoldSpan(path="fastapi/dependencies/utils.py", start=247, end=251)],
            **common),
        CodeGenTask(
            task_id="syn_t3_codegen", query="Write a function f that returns 1.",
            ground_truth=GroundTruth(), gold_code="def f(): return 1", tests=["assert f() == 1"],
            **common),
        EditTask(
            task_id="syn_t4_edit", query="Change f to return 2:\n\ndef f(): return 1",
            ground_truth=GroundTruth(), gold_patch="-def f(): return 1\n+def f(): return 2",
            fail_to_pass=["assert f() == 2"], **common),
        BlastRadiusTask(
            task_id="syn_t5_blast_radius", query=f"What calls `{TARGET}`?", seed_symbol=TARGET,
            ground_truth=GroundTruth(pipeline_symbols=list(CALLERS)),
            **common),
    ]
