"""Regression coverage for the pilot-run bug reported after Phase 2/3
smoke + calibration testing: `run_evaluation` used to catch *any*
exception from `OpenAICompatibleClient()` construction and silently fall back to
`dry_run = True`, producing a full report (exit 0, every diagnostic
computed normally) with every `tsr_scores` silently empty and no signal
anywhere in the output that no real LLM call was ever made.

Two things are verified here:

1. Without `--dry-run`, a client-construction failure (missing
   `DEEPSEEK_API_KEY`, in this test) is now fatal - `run_evaluation`
   raises instead of silently degrading. `main()`'s CLI wrapper turns
   this into a clean `error: ...` message and exit code 1 (not
   exercised directly here - `OpenAIClientError` is asserted instead,
   the same exception `main()`'s except clause now catches).
2. With `--dry-run` passed explicitly, behavior is unchanged: no client
   is constructed at all, `tsr_scores` stays empty, no error.

Also proves the seed-loop mechanism itself was never broken (the
"seed aggregation bug" reported alongside the LLM-gating bug): once a
working client is available, one `TaskRunRecord` is produced per
(task, engine, budget) and its `tsr_scores` list contains exactly
`len(seeds)` values - reusing a fake, no-network client in place of
`OpenAICompatibleClient`, run through the real `run_evaluation` end to end
against the real `tests/fixtures/python_repo` corpus (never the real
Django corpus - fast, no network, no LLM cost).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from benchmarks.ground_truth.schema import EvaluationTask, GroundTruthAnnotation
from benchmarks.openai_client import CallResult, OpenAIClientError
from benchmarks.runner import run_evaluation
from benchmarks.tsr.client import DEEPSEEK_API_KEY_ENV_VAR

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
    """Points `run_evaluation` at the small, real, local
    `tests/fixtures/python_repo` corpus instead of resolving (and
    network-cloning) the real, pinned Django corpus - everything else
    in `run_evaluation` (build_pipeline, the real engines, real
    diagnostics) runs for real against it."""
    import benchmarks.runner as runner_module

    monkeypatch.setattr(runner_module, "resolve", lambda repo, force=False: python_repo_root)
    monkeypatch.setattr(
        runner_module,
        "load_tasks_from_dir",
        lambda tasks_dir: SimpleNamespace(accepted=[_fake_task()], rejected=[]),
    )


def test_client_construction_failure_is_fatal_without_dry_run(monkeypatch, python_repo_root):
    monkeypatch.delenv(DEEPSEEK_API_KEY_ENV_VAR, raising=False)
    monkeypatch.setattr("benchmarks.tsr.client._load_dotenv_if_present", lambda: None)
    _patch_corpus(monkeypatch, python_repo_root)

    with pytest.raises(OpenAIClientError, match=DEEPSEEK_API_KEY_ENV_VAR):
        run_evaluation(
            repo="django",
            budgets=[2000],
            tasks_dir="unused",
            seeds=(42,),
            dry_run=False,
        )


def test_explicit_dry_run_is_unaffected_by_a_missing_api_key(monkeypatch, python_repo_root):
    monkeypatch.delenv(DEEPSEEK_API_KEY_ENV_VAR, raising=False)
    monkeypatch.setattr("benchmarks.tsr.client._load_dotenv_if_present", lambda: None)
    _patch_corpus(monkeypatch, python_repo_root)

    run = run_evaluation(
        repo="django",
        budgets=[2000],
        tasks_dir="unused",
        seeds=(42,),
        dry_run=True,
    )

    assert run.records
    assert all(r.tsr_scores == [] for r in run.records)


class _FakeOpenAICompatibleClient:
    """Stands in for `OpenAICompatibleClient` - no network, no `openai` SDK
    involvement - while exercising the exact `complete()` shape
    `run_tsr_prompt` calls. Also exposes `base_url`/`model` - the
    fix-client-env-vars-definitive sanity check in `run_evaluation`
    requires them on whatever `OpenAICompatibleClient()` resolves to, real or
    faked."""

    def __init__(self):
        self.base_url = "http://fake-client.test/v1"
        self.model = "fake-model"
        self.seeds_seen: list[int] = []
        self.models_seen: list[str | None] = []

    def complete(self, model, system, user, temperature=0.0, max_tokens=None, seed=None, task_id=None, engine=None):
        self.seeds_seen.append(seed)
        self.models_seen.append(model)
        return CallResult(
            model=model, content="response", prompt_tokens=1, completion_tokens=1,
            total_tokens=2, cost_usd=0.0, latency_seconds=0.0, seed=seed,
        )


def test_each_record_gets_exactly_len_seeds_tsr_scores_end_to_end(monkeypatch, python_repo_root):
    """The "seed aggregation bug" report's own claim (125 expected cells,
    20 produced) turned out to be explained entirely by Problem 1 (the
    client silently never got constructed, so the seed loop never ran at
    all) plus Oracle being excluded by design (no --pragmatic-oracle
    passed) - not a separate bug in seed iteration. This proves the
    seed loop itself, with a real working client, end to end."""
    import benchmarks.runner as runner_module

    _patch_corpus(monkeypatch, python_repo_root)
    fake_client = _FakeOpenAICompatibleClient()
    monkeypatch.setattr(runner_module, "OpenAICompatibleClient", lambda: fake_client)

    seeds = (42, 43, 44)
    run = run_evaluation(
        repo="django",
        budgets=[2000],
        tasks_dir="unused",
        seeds=seeds,
        dry_run=False,
    )

    assert run.records, "expected at least one (task, engine, budget) record"
    for record in run.records:
        assert len(record.tsr_scores) == len(seeds), (
            f"{record.engine_name}: expected {len(seeds)} tsr_scores (one per seed), got {len(record.tsr_scores)}"
        )
    # 4 default engines (no Oracle: neither --oracle-packages nor
    # --pragmatic-oracle was passed) x 1 task x 1 budget.
    assert len(run.records) == 4
    assert sorted(fake_client.seeds_seen) == sorted(list(seeds) * 4)


def test_run_evaluation_does_not_override_client_model_when_none_chosen(monkeypatch, python_repo_root):
    """fix-client-env-vars regression: the reported pilot bug -
    LLM_BASE_URL/LLM_MODEL/LLM_API_KEY_ENV set to point at a local
    Ollama instance, but the harness still requested
    'deepseek-v4-flash' and got a 404. Root cause was here, not in
    OpenAICompatibleClient: run_evaluation's own `model` parameter (and the
    --model CLI flag) defaulted to the hardcoded DEFAULT_MODEL, which
    flowed through run_tsr_prompt into client.complete(model=...) as an
    explicit, non-None value - permanently overriding
    OpenAICompatibleClient.complete()'s `resolved_model = model if model is not
    None else self.model` fallback to the client's own env-var-resolved
    self.model, no matter what LLM_MODEL was set to.

    Proven directly against the real client.complete() call: with no
    `model=` argument passed to run_evaluation (the pilot's own CLI
    invocation, which never passes --model), the fake client must
    receive `model=None` for every call - never a hardcoded string -
    so a real OpenAICompatibleClient's self.model (LLM_MODEL-aware) is the
    thing that actually decides which model gets requested."""
    import benchmarks.runner as runner_module

    _patch_corpus(monkeypatch, python_repo_root)
    fake_client = _FakeOpenAICompatibleClient()
    monkeypatch.setattr(runner_module, "OpenAICompatibleClient", lambda: fake_client)

    run_evaluation(
        repo="django",
        budgets=[2000],
        tasks_dir="unused",
        seeds=(42,),
        dry_run=False,
    )

    assert fake_client.models_seen, "expected at least one complete() call"
    assert all(m is None for m in fake_client.models_seen), fake_client.models_seen
