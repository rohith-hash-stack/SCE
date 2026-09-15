"""Test-practice: LLM-path integration tests for `benchmarks.runner.
run_evaluation`, filling two gaps the existing gating/checkpoint/raw-
response test files don't cover:

1. `--resume` actually SKIPS a checkpointed cell (no fresh client call
   for it) rather than just proving load/save round-trip in isolation
   (`test_runner_checkpoint.py` covers that at the file level only) or
   that a resumed cell's score survives a schema change (`test_raw_
   response_persistence.py`'s pre-fix-checkpoint test) - here, the
   checkpoint feeds a REAL `run_evaluation()` call and we assert the
   fake client was never asked about the checkpointed seed.
2. `score_tsr_response` is correctly wired end to end for a debug task
   - a fake client returning a genuinely correct vs. a genuinely wrong
   fenced-JSON pipeline produces `tsr_scores` of 1.0 vs. 0.0
   respectively, not just "some float, len(seeds) of them"
   (`test_runner_llm_gating.py`'s own scope).

Both run through the real `run_evaluation`, against the real, small,
local `tests/fixtures/python_repo` corpus - no network, no LLM cost.
"""
from __future__ import annotations

from types import SimpleNamespace

from benchmarks.ground_truth.schema import EvaluationTask, GroundTruthAnnotation
from benchmarks.openai_client import CallResult
from benchmarks.runner import _cell_key, run_evaluation, save_checkpoint

_SEED_SYMBOL = "src.controllers.checkout.checkout_endpoint"


def _fake_task() -> EvaluationTask:
    annotation = GroundTruthAnnotation(
        annotator_id="a", pipeline_symbols=[_SEED_SYMBOL], expected_solution="x"
    )
    return EvaluationTask(
        task_id="fake_t02_001",
        repo="django",
        pinned_commit="deadbeef",
        seed_symbol=_SEED_SYMBOL,
        task_type="debug",
        prompt="Trace the causal pipeline starting from checkout_endpoint.",
        annotation_a=annotation,
        annotation_b=annotation,
        adjudicated=annotation,
        cohen_kappa=1.0,
    )


def _patch_corpus(monkeypatch, python_repo_root: str) -> None:
    import benchmarks.runner as runner_module

    monkeypatch.setattr(runner_module, "resolve", lambda repo, force=False: python_repo_root)
    monkeypatch.setattr(
        runner_module,
        "load_tasks_from_dir",
        lambda tasks_dir: SimpleNamespace(accepted=[_fake_task()], rejected=[]),
    )


class _FakeDeepSeekClient:
    def __init__(self, response_text: str = "response"):
        self.seeds_seen: list[int] = []
        self._response_text = response_text

    def complete(self, model, system, user, temperature=0.0, max_tokens=None, seed=None):
        self.seeds_seen.append(seed)
        return CallResult(
            model=model, content=self._response_text, prompt_tokens=1, completion_tokens=1,
            total_tokens=2, cost_usd=0.0, latency_seconds=0.0, seed=seed,
        )


def test_resume_skips_a_checkpointed_cell_and_only_calls_the_client_for_the_rest(monkeypatch, python_repo_root, tmp_path):
    import benchmarks.runner as runner_module

    _patch_corpus(monkeypatch, python_repo_root)
    fake_client = _FakeDeepSeekClient()
    monkeypatch.setattr(runner_module, "DeepSeekClient", lambda: fake_client)

    checkpoint_path = str(tmp_path / "checkpoint.json")
    key = _cell_key("fake_t02_001", "prism_v11", 2000, 42)
    save_checkpoint(checkpoint_path, {"cells": {key: {"score": 0.5, "raw_response": "cached"}}})

    seeds = (42, 43, 44)
    run = run_evaluation(
        repo="django",
        budgets=[2000],
        tasks_dir="unused",
        seeds=seeds,
        dry_run=False,
        resume=True,
        checkpoint_path=checkpoint_path,
    )

    # Only the prism_v11 cell for seed 42 was checkpointed - the other 3
    # default engines still need a real seed-42 call of their own, so
    # seed 42 legitimately still reaches the client 3 times (once per
    # non-checkpointed engine), just never a 4th time for prism_v11.
    num_engines = len({r.engine_name for r in run.records})
    assert num_engines == 4
    assert fake_client.seeds_seen.count(42) == num_engines - 1
    assert fake_client.seeds_seen.count(43) == num_engines
    assert fake_client.seeds_seen.count(44) == num_engines

    prism_record = next(r for r in run.records if r.engine_name == "prism_v11")
    seed_to_score = dict(zip(seeds, prism_record.tsr_scores))
    assert seed_to_score[42] == 0.5  # reused from the checkpoint, not recomputed
    seed_to_response = dict(zip(seeds, prism_record.raw_responses))
    assert seed_to_response[42] == "cached"


def test_tsr_scoring_is_correctly_wired_for_a_genuinely_correct_debug_response(monkeypatch, python_repo_root):
    import benchmarks.runner as runner_module

    _patch_corpus(monkeypatch, python_repo_root)
    correct_response = (
        "```json\n"
        f'{{"reasoning": "single stage", "pipeline": [{{"symbol": "{_SEED_SYMBOL}", "evidence": "seed"}}]}}'
        "\n```\n"
    )
    fake_client = _FakeDeepSeekClient(response_text=correct_response)
    monkeypatch.setattr(runner_module, "DeepSeekClient", lambda: fake_client)

    run = run_evaluation(
        repo="django", budgets=[2000], tasks_dir="unused", seeds=(42,), dry_run=False,
    )
    for record in run.records:
        assert record.tsr_scores == [1.0], record.engine_name


def test_tsr_scoring_is_correctly_wired_for_a_genuinely_wrong_debug_response(monkeypatch, python_repo_root):
    import benchmarks.runner as runner_module

    _patch_corpus(monkeypatch, python_repo_root)
    wrong_response = (
        '```json\n{"reasoning": "single stage", "pipeline": '
        '[{"symbol": "some.totally.unrelated.symbol", "evidence": "n/a"}]}\n```\n'
    )
    fake_client = _FakeDeepSeekClient(response_text=wrong_response)
    monkeypatch.setattr(runner_module, "DeepSeekClient", lambda: fake_client)

    run = run_evaluation(
        repo="django", budgets=[2000], tasks_dir="unused", seeds=(42,), dry_run=False,
    )
    for record in run.records:
        assert record.tsr_scores == [0.0], record.engine_name


def test_multiple_budgets_produce_one_record_per_engine_per_budget(monkeypatch, python_repo_root):
    import benchmarks.runner as runner_module

    _patch_corpus(monkeypatch, python_repo_root)
    fake_client = _FakeDeepSeekClient()
    monkeypatch.setattr(runner_module, "DeepSeekClient", lambda: fake_client)

    budgets = [500, 2000]
    run = run_evaluation(
        repo="django", budgets=budgets, tasks_dir="unused", seeds=(42,), dry_run=False,
    )
    budgets_seen = sorted({r.budget_tokens for r in run.records})
    assert budgets_seen == sorted(budgets)
    for budget in budgets:
        assert sum(1 for r in run.records if r.budget_tokens == budget) == 4  # 4 default engines
