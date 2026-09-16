"""Regression coverage for fix-llm-response-persistence: the TSR
pipeline used to discard `r.call.content` immediately after scoring it
(`score = score_tsr_response(task, r.call.content, ...)`), so no raw LLM
response text survived anywhere - not in `TaskRunRecord`, not in the
checkpoint, not in any written report - making it impossible to audit
*why* a given cell scored 0 vs 1 after the fact (the exact blocker hit
investigating the near-zero calibration TSR).

Verifies, end to end against the real `tests/fixtures/python_repo`
corpus (no network, no LLM cost, fake client only):

1. Each `TaskRunRecord.raw_responses` entry is index-aligned with its
   own `tsr_scores` entry (same seed, same position).
2. The checkpoint persists `raw_response` per cell and a `--resume` run
   reads it back correctly instead of re-calling the LLM.
3. A checkpoint written before this fix (no `raw_response` key) still
   loads on resume - an empty string for those older cells, never a
   `KeyError`.
4. The Markdown report's raw-response section includes a truncated
   preview of every real response, and the JSON report keeps the full,
   untruncated text.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from benchmarks.ground_truth.schema import EvaluationTask, GroundTruthAnnotation
from benchmarks.openai_client import CallResult
from benchmarks.reporting.report_generator import (
    RAW_RESPONSE_MARKDOWN_TRUNCATE_CHARS,
    render_raw_responses_markdown,
    write_json_results,
    write_markdown_report,
)
from benchmarks.runner import _cell_key, run_evaluation

_SEED_SYMBOL = "src.controllers.checkout.checkout_endpoint"


def _fake_task() -> EvaluationTask:
    annotation = GroundTruthAnnotation(annotator_id="a", pipeline_symbols=[_SEED_SYMBOL], expected_solution="x")
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
        runner_module, "load_tasks_from_dir", lambda tasks_dir: SimpleNamespace(accepted=[_fake_task()], rejected=[])
    )


#: fix-client-env-vars-definitive's runner-side sanity check requires
#: base_url/model on whatever OpenAICompatibleClient() resolves to, real or
#: faked - set as class attributes on every fake client below.
_FAKE_BASE_URL = "http://fake-client.test/v1"
_FAKE_MODEL = "fake-model"


class _FakeOpenAICompatibleClient:
    """Returns a response whose text encodes its own seed, so a test can
    check response-to-seed alignment without depending on real model
    output."""

    base_url = _FAKE_BASE_URL
    model = _FAKE_MODEL

    def complete(self, model, system, user, temperature=0.0, max_tokens=None, seed=None):
        return CallResult(
            model=model, content=f"response for seed {seed}", prompt_tokens=1, completion_tokens=1,
            total_tokens=2, cost_usd=0.0, latency_seconds=0.0, seed=seed,
        )


def test_raw_responses_are_index_aligned_with_tsr_scores(monkeypatch, python_repo_root):
    import benchmarks.runner as runner_module

    _patch_corpus(monkeypatch, python_repo_root)
    monkeypatch.setattr(runner_module, "OpenAICompatibleClient", _FakeOpenAICompatibleClient)

    seeds = (42, 43, 44)
    run = run_evaluation(repo="django", budgets=[2000], tasks_dir="unused", seeds=seeds, dry_run=False)

    assert run.records
    for record in run.records:
        assert len(record.raw_responses) == len(record.tsr_scores) == len(seeds)
        for i, seed in enumerate(seeds):
            assert record.raw_responses[i] == f"response for seed {seed}"


def test_checkpoint_persists_and_resumes_raw_response(monkeypatch, python_repo_root, tmp_path):
    import benchmarks.runner as runner_module

    _patch_corpus(monkeypatch, python_repo_root)
    monkeypatch.setattr(runner_module, "OpenAICompatibleClient", _FakeOpenAICompatibleClient)
    checkpoint_path = str(tmp_path / "checkpoint.json")

    run_evaluation(
        repo="django", budgets=[2000], tasks_dir="unused", seeds=(42,), dry_run=False, checkpoint_path=checkpoint_path
    )

    saved = json.loads((tmp_path / "checkpoint.json").read_text())
    key = _cell_key("fake_t02_001", "prism_v11", 2000, 42)
    assert saved["cells"][key]["raw_response"] == "response for seed 42"

    # A second, --resume run must reuse that stored response (no fresh
    # call) - proven by using a client that would raise if actually called.
    class _ExplodingClient:
        base_url = _FAKE_BASE_URL
        model = _FAKE_MODEL

        def complete(self, *args, **kwargs):
            raise AssertionError("resume must not re-call the LLM for an already-checkpointed cell")

    monkeypatch.setattr(runner_module, "OpenAICompatibleClient", _ExplodingClient)
    resumed = run_evaluation(
        repo="django", budgets=[2000], tasks_dir="unused", seeds=(42,), dry_run=False,
        resume=True, checkpoint_path=checkpoint_path,
    )
    prism_record = next(r for r in resumed.records if r.engine_name == "prism_v11")
    assert prism_record.raw_responses == ["response for seed 42"]
    assert prism_record.tsr_scores == [0.0]


def test_checkpoint_persists_selected_symbols_and_cpi(monkeypatch, python_repo_root, tmp_path):
    """fix-checkpoint-schema-cpi regression: CPI_strict/CPI_fractional
    were previously undiagnosable from the checkpoint after the fact -
    the checkpoint schema stored score/raw_response/token counts, never
    the engine's retrieved symbol set - so a partial or crashed run
    left no way to compute the gate's second metric at all (the exact
    gap hit auditing the real Kaggle pilot's 900-cell checkpoint).

    _fake_task()'s own pipeline_symbols is just [_SEED_SYMBOL] - the
    task's own seed symbol, which every engine trivially retrieves (it
    is where retrieval starts) - so cpi_strict/cpi_fractional must both
    be exactly 1.0 for every engine, deterministically, without
    depending on any particular engine's real retrieval behavior."""
    import benchmarks.runner as runner_module

    _patch_corpus(monkeypatch, python_repo_root)
    monkeypatch.setattr(runner_module, "OpenAICompatibleClient", _FakeOpenAICompatibleClient)
    checkpoint_path = str(tmp_path / "checkpoint.json")

    run_evaluation(
        repo="django", budgets=[2000], tasks_dir="unused", seeds=(42,), dry_run=False, checkpoint_path=checkpoint_path
    )

    saved = json.loads((tmp_path / "checkpoint.json").read_text())
    key = _cell_key("fake_t02_001", "prism_v11", 2000, 42)
    cell = saved["cells"][key]

    assert "selected_symbols" in cell
    assert isinstance(cell["selected_symbols"], list) and cell["selected_symbols"]
    assert _SEED_SYMBOL in cell["selected_symbols"]
    assert cell["cpi_strict"] == 1.0
    assert cell["cpi_fractional"] == 1.0


def test_resume_from_a_pre_fix_checkpoint_with_no_raw_response_key_is_not_a_keyerror(monkeypatch, python_repo_root, tmp_path):
    """Only the prism_v11 cell is pre-checkpointed here - the other 3
    default engines have no checkpoint entry, so they still need a
    fresh call (that's correct, expected resume behavior); this test
    isolates its assertion to the one pre-checkpointed cell rather than
    asserting no call happens at all."""
    import benchmarks.runner as runner_module

    _patch_corpus(monkeypatch, python_repo_root)
    checkpoint_path = tmp_path / "checkpoint.json"
    key = _cell_key("fake_t02_001", "prism_v11", 2000, 42)
    # Simulates a checkpoint written by the harness before this fix -
    # score/prompt_tokens/completion_tokens/cost_usd only, no raw_response.
    checkpoint_path.write_text(json.dumps({"cells": {key: {"score": 1.0, "prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.0}}}))

    class _FreshCallMarkerClient:
        base_url = _FAKE_BASE_URL
        model = _FAKE_MODEL

        def complete(self, model, system, user, temperature=0.0, max_tokens=None, seed=None):
            return CallResult(
                model=model, content="a genuinely fresh call happened", prompt_tokens=1, completion_tokens=1,
                total_tokens=2, cost_usd=0.0, latency_seconds=0.0, seed=seed,
            )

    monkeypatch.setattr(runner_module, "OpenAICompatibleClient", _FreshCallMarkerClient)
    run = run_evaluation(
        repo="django", budgets=[2000], tasks_dir="unused", seeds=(42,), dry_run=False,
        resume=True, checkpoint_path=str(checkpoint_path),
    )
    prism_record = next(r for r in run.records if r.engine_name == "prism_v11")
    assert prism_record.tsr_scores == [1.0]  # the checkpointed score, not freshly recomputed
    assert prism_record.raw_responses == [""]  # missing key -> "" , never a KeyError

    other_record = next(r for r in run.records if r.engine_name != "prism_v11")
    assert other_record.raw_responses == ["a genuinely fresh call happened"]  # no checkpoint entry -> real call made


def test_markdown_report_shows_truncated_response_and_json_keeps_full_text(monkeypatch, python_repo_root, tmp_path):
    import benchmarks.runner as runner_module

    _patch_corpus(monkeypatch, python_repo_root)
    long_text = "x" * (RAW_RESPONSE_MARKDOWN_TRUNCATE_CHARS + 50)

    class _LongResponseClient:
        base_url = _FAKE_BASE_URL
        model = _FAKE_MODEL

        def complete(self, model, system, user, temperature=0.0, max_tokens=None, seed=None):
            return CallResult(
                model=model, content=long_text, prompt_tokens=1, completion_tokens=1,
                total_tokens=2, cost_usd=0.0, latency_seconds=0.0, seed=seed,
            )

    monkeypatch.setattr(runner_module, "OpenAICompatibleClient", _LongResponseClient)
    run = run_evaluation(repo="django", budgets=[2000], tasks_dir="unused", seeds=(42,), dry_run=False)

    markdown = render_raw_responses_markdown(run)
    assert long_text not in markdown  # never the full, untruncated text in the table
    assert "x" * RAW_RESPONSE_MARKDOWN_TRUNCATE_CHARS in markdown

    md_path = tmp_path / "report.md"
    json_path = tmp_path / "report.json"
    write_markdown_report(run, md_path)
    write_json_results(run, json_path)

    assert long_text not in md_path.read_text()
    payload = json.loads(json_path.read_text())
    prism_record = next(r for r in payload["records"] if r["engine_name"] == "prism_v11")
    assert prism_record["raw_responses"] == [long_text]  # full text, untruncated, in the JSON
