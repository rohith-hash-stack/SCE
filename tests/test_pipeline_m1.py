"""End-to-end cells through harness.pipeline with a scripted model."""
import json
import os

import pytest

from harness.llm import Completion
from harness.pipeline import Pipeline, gpu_watchdog

FASTAPI = "/home/user/SCE/.benchmarks/corpora/fastapi"


class Words:
    name = "words"
    def count(self, t): return len(t.split())


class EchoGold:
    """Answers the T2 contract by naming whatever the context headers name."""
    def __call__(self, system, user, max_tokens=0, seed=None, purpose=""):
        if purpose == "turn1":
            return Completion(json.dumps({"requested_symbols": []}), 1, 5, 0.0, purpose=purpose)
        names = [ln.split("(")[-1].rstrip(")") for ln in user.splitlines() if ln.startswith("# ") and "(" in ln]
        return Completion("```json\n" + json.dumps({"reasoning": "r", "symbols": names}) + "\n```", 1, 9, 0.01)


@pytest.mark.skipif(not os.path.isdir(FASTAPI), reason="FastAPI checkout missing")
def test_cells_for_arm0_arm5_oracle(tmp_path):
    from harness.arms import build_arm
    from harness.tasks.loaders import load_tasks
    tok, llm = Words(), EchoGold()
    arm5 = build_arm("arm5", llm=llm, tokenizer=tok)
    arm5.index(FASTAPI, {})
    oracle = build_arm("oracle", tokenizer=tok, builder=arm5.engine.builder)
    oracle.index(FASTAPI, {})
    arm0 = build_arm("arm0")
    pipe = Pipeline({"arm0": arm0, "arm5": arm5, "oracle": oracle}, llm, tok, out_dir=tmp_path)
    task = load_tasks("fastapi", repo_root=FASTAPI, limit=1)[0]
    outs = {a: pipe.run_cell(a, task, seed=42) for a in ("arm0", "arm5", "oracle")}
    assert outs["oracle"].result.tsr == 1.0                                   # gold delivered + named
    assert outs["arm0"].result.tsr == 0.0 and outs["arm0"].result.cleanliness != outs["arm0"].result.cleanliness
    assert outs["arm5"].ans.extraction_method == "prism_final"
    for o in outs.values():
        assert {"L_retrieve", "L_generate", "L_e2e"} <= set(o.result.latency_profile)
        assert o.ctx.total_tokens <= o.ctx.budget_tokens
    assert (tmp_path / "bundles" / f"arm5_{task.task_id}_s42.json").exists()
    assert (tmp_path / "completions" / f"oracle_{task.task_id}_s42.json").exists()


def test_gpu_watchdog_without_gpu_is_noop():
    assert gpu_watchdog() is None or isinstance(gpu_watchdog(), int)
