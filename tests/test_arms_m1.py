"""Arms 0, 5, Oracle and the Arm 2/3/4 stubs (Arm 1: test_arm1_rag.py)."""

import json
import os
import pytest
from harness.arms.arm0_parametric import Arm0Parametric
from harness.llm import Completion
from harness.scoring.adapters import finalize_context
from harness.scoring.fairness import verify_ranking


def test_arm0_delivers_nothing():
    arm = Arm0Parametric()
    arm.index("/anywhere", {})
    ctx = arm.retrieve("Where is f defined?", {"task_id": "t", "task_type": "T2_localization"})
    assert ctx.items == [] and ctx.total_tokens == 0 and ctx.budget_tokens == 0
    p = arm.build_prompt(ctx)
    assert p.startswith("## Task\n\nWhere is f defined?") and "Repository context" not in p
    assert ctx.build_meta["fidelity"] == "BASELINE"


# ---- Arm 5: PRISM wrapper ----


FASTAPI = "/home/user/SCE/.benchmarks/corpora/fastapi"


class Words:
    name = "words"
    def count(self, t): return len(t.split())


class ScriptedLLM:
    def __init__(self, *texts):
        self.texts, self.calls = list(texts), []
    def __call__(self, system, user, max_tokens=0, seed=None, purpose=""):
        self.calls.append(purpose)
        return Completion(self.texts.pop(0), 1, 20, 0.0, purpose=purpose)


@pytest.fixture(scope="module")
def fastapi_prism():
    if not os.path.isdir(FASTAPI):
        pytest.skip("FastAPI checkout missing")
    from harness.arms.arm5_prism import Arm5Prism
    arm = Arm5Prism(tokenizer=Words())
    arm.index(FASTAPI, {})
    return arm


def test_arm5_wraps_real_two_pass(fastapi_prism):
    from harness.tasks.loaders import load_tasks
    task = load_tasks("fastapi", repo_root=FASTAPI, limit=1)[0]
    pipeline = task.ground_truth.pipeline_symbols
    fastapi_prism.llm = ScriptedLLM(json.dumps({"thought_process": "x", "requested_symbols": pipeline}))
    ctx = fastapi_prism.retrieve(task.query, task.seed_dict())
    verify_ranking(ctx.items)
    assert fastapi_prism.llm.calls == ["turn1"]
    assert set(pipeline) <= ctx.delivered_symbols                    # Turn-2 hydrated the selection
    assert ctx.build_meta["turn1_parsed_ok"] and ctx.build_meta["turn_count"] == 2
    assert ctx.build_meta["turn2b_triggered"] is False and ctx.build_meta["fidelity"] == "HIGH"
    assert all(it.kind in ("code_chunk", "signature_stub") for it in ctx.items)
    # harness-boundary trimming: re-count and cut from the lowest rank
    tight = finalize_context(ctx, Words(), budget=ctx.total_tokens // 2)
    assert tight.total_tokens <= ctx.total_tokens // 2 and tight.build_meta["over_budget"] is True
    assert [i.source_id for i in tight.items] == [i.source_id for i in ctx.items][:len(tight.items)]


def test_arm5_turn2b_runs_for_root_import_tasks(fastapi_prism):
    from harness.tasks.loaders import load_tasks
    tasks = [t for t in load_tasks("fastapi", repo_root=FASTAPI) if t.hints.get("root_imports")]
    if not tasks:
        pytest.skip("no FastAPI task declares root_imports")
    t = tasks[0]
    fastapi_prism.llm = ScriptedLLM(json.dumps({"requested_symbols": t.ground_truth.pipeline_symbols}),
                                    json.dumps({"requested_symbols": []}))
    ctx = fastapi_prism.retrieve(t.query, t.seed_dict())
    assert ctx.build_meta["turn2b_triggered"] == (fastapi_prism.llm.calls == ["turn1", "turn2b"])


def test_arm5_without_seed_delivers_nothing(fastapi_prism):
    ctx = fastapi_prism.retrieve("Write f", {"task_id": "x", "task_type": "T3_codegen", "seed_symbol": None})
    assert ctx.items == [] and ctx.build_meta["no_seed"] is True


# ---- Oracle ----
def test_oracle_delivers_universe_pipeline_first(fastapi_prism):
    from harness.arms.oracle import Oracle
    from harness.scoring.scorer import score
    from harness.scoring.canonical import NormalizedAnswer
    from harness.tasks.loaders import load_tasks
    task = load_tasks("fastapi", repo_root=FASTAPI, limit=1)[0]
    oracle = Oracle(tokenizer=Words(), builder=fastapi_prism.engine.builder)
    oracle.index(FASTAPI, {})
    seed = {**task.seed_dict(), "oracle_pipeline": task.ground_truth.pipeline_symbols,
            "oracle_universe": sorted(task.ground_truth.universe_symbols())}
    ctx = oracle.retrieve(task.query, seed)
    verify_ranking(ctx.items)
    assert [i.symbols[0] for i in ctx.items[:len(task.ground_truth.pipeline_symbols)]] == task.ground_truth.pipeline_symbols
    assert all(i.kind == "oracle_truth" for i in ctx.items)
    assert ctx.delivered_symbols | set(ctx.build_meta["unresolved_universe"]) == task.ground_truth.universe_symbols()
    res = score(task, ctx, NormalizedAnswer("oracle", task.task_id, "", "", [], "plain_text", False, 0, 0.0))
    assert res.cleanliness == 1.0 and res.task_specific["acc_at_5_retrieval"] == 1.0          # ceiling by construction


# ---- Arm 2/3/4 stubs ----
# Arm 2 is implemented in M2 (tests/test_arm2_priompt.py); Arms 3 and 4 are still stubs
@pytest.mark.parametrize("arm_id,ms,grade", [("arm3", "M2", "MEDIUM"), ("arm4", "M3", "MEDIUM_HIGH")])
def test_stubs_declare_fidelity_and_raise(arm_id, ms, grade):
    from harness.arms import build_arm
    arm = build_arm(arm_id)
    assert arm.fidelity.value == grade
    for call in (lambda: arm.index("/r", {}), lambda: arm.retrieve("q", {}), lambda: arm.build_prompt(None, None)):
        with pytest.raises(NotImplementedError, match=ms):
            call()
