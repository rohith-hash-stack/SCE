"""Zero-cost validation of the LLM-to-score pipeline before spending
real money on calibration. Runs `benchmarks.runner.run_evaluation` (the
real pilot pipeline - real retrieval, real TSR prompt construction, real
scorer) against ONE synthetic calibration task, with `DeepSeekClient`
monkeypatched to a fake client that never touches the network. No
`DEEPSEEK_API_KEY` needed, no cost.

**Deviation from the literal request, flagged directly**: the request
asked for `--tasks-dir=<single-task-dir>` against the real Django
corpus. Measured first, with an instrumented script, before writing
this file: a single `run_evaluation()` call against real Django with
`--pragmatic-oracle` takes 250-400+ seconds, not "seconds" -
`PragmaticOracle.index()` alone took 102s in one measured run (real,
Django-scale cost, not a fixed per-call overhead - the same call
against the small local fixture repo below took 0.39s end to end).
Four of these per test run would take 15-20+ minutes, directly
contradicting "The tests should run in seconds. No cost." - "no cost"
held (no network/LLM calls either way), but "seconds" did not. Since
the actual purpose ("confirm the harness produces meaningful numbers")
is about the LLM-to-score wiring, not Django-scale retrieval
performance, these tests instead use `tests/fixtures/python_repo` (the
same small, real, local repo `test_runner_llm_gating.py`/
`test_runner_integration.py` already use for exactly this kind of fast
`run_evaluation()` test) with a synthetic in-memory `EvaluationTask`
whose `pipeline_symbols` is a real, verified 4-symbol causal chain in
that fixture (`CheckoutController.process_checkout` -> `verify_session`
/ `PaymentProcessor.charge` / `OrderRepository.mark_paid` - all real
Rule A/B resolved edges, confirmed in `test_symbol_resolution.py`).
`load_tasks_from_dir`/`resolve` are monkeypatched the same way those
two existing files already do it - not a new seam.

**Fragility note** (flagged per the task's own request): monkeypatching
`benchmarks.runner.DeepSeekClient`/`resolve`/`load_tasks_from_dir`
(module-level names) is the same seam `test_runner_llm_gating.py`/
`test_runner_integration.py` already depend on - not novel, not
separately fragile. Reading `record.engine_name`/`record.diagnostics`
off the real `TaskRunRecord` dataclass is likewise the same seam
those files use. The one thing that *would* make this file fragile -
and is deliberately avoided - is asserting an exact `fpr_oracle` value;
see Test 4's own docstring.

**Updated for fix-prompt-flat-contract/fix-parser-flat**: `PerfectClient`/
`WrongClient` now emit the flat `{"reasoning": ..., "symbols": [...]}`
object (via `_flat_response`), matching `DEBUG_TASK_RESPONSE_CONTRACT`
and parsed by `benchmarks.tsr.scorer_debug.extract_flat_symbols` - not
the earlier nested `{"pipeline": [{"symbol": ..., "evidence": ...}]}`
shape, retired after Qwen 2.5 7B Instruct Q8_0 (the Ollama SLM used for
local format-compliance testing) failed it at Q4 while passing the flat
shape 10/10. `ProseClient` is unchanged (plain prose, no JSON at all) -
it still exercises the "no valid JSON present" malformed-input path
under the new parser exactly as it did under the old one.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from benchmarks.engines.prism_engine_cache import PrismEngineCache
from benchmarks.ground_truth.schema import EvaluationTask, GroundTruthAnnotation
from benchmarks.openai_client import CallResult
from benchmarks.runner import run_evaluation
from benchmarks.tsr.client import DEFAULT_MAX_TOKENS

_SEEDS = (42, 43, 44, 45, 46)

#: A real, verified causal chain in tests/fixtures/python_repo:
#: CheckoutController.process_checkout calls verify_session (Rule B,
#: import), self.payment_processor.charge (Rule A, instance binding to
#: PaymentProcessor.charge), and self.order_repo.mark_paid (Rule A, to
#: OrderRepository.mark_paid) - all three are real, resolved CALLS
#: edges (test_symbol_resolution.py's own Rule A/B tests), so a
#: generous budget (4000, on a 5-file fixture repo) is expected to
#: retrieve all of them for every engine, not just Prism.
_PIPELINE_SYMBOLS = [
    "src.controllers.checkout.CheckoutController.process_checkout",
    "src.auth.jwt.verify_session",
    "src.services.billing.PaymentProcessor.charge",
    "src.repositories.orders.OrderRepository.mark_paid",
]


def _fake_task() -> EvaluationTask:
    annotation = GroundTruthAnnotation(
        annotator_id="a", pipeline_symbols=_PIPELINE_SYMBOLS, expected_solution="x"
    )
    return EvaluationTask(
        task_id="fake_t02_mock_calibration",
        repo="django",
        pinned_commit="deadbeef",
        seed_symbol=_PIPELINE_SYMBOLS[0],
        task_type="debug",
        prompt="Trace the causal pipeline starting from process_checkout.",
        annotation_a=annotation,
        annotation_b=annotation,
        adjudicated=annotation,
        cohen_kappa=1.0,
    )


def _flat_response(symbols: list[str]) -> str:
    """The flat fix-prompt-flat-contract shape: {"reasoning": ...,
    "symbols": [...]}, fenced - matching DEBUG_TASK_RESPONSE_CONTRACT's
    own instruction. Replaces the retired nested {"pipeline": [{"symbol":
    ..., "evidence": ...}]} shape."""
    obj = {
        "reasoning": "Each stage calls the next in the traced order.",
        "symbols": symbols,
    }
    return f"```json\n{json.dumps(obj)}\n```"


#: fix-client-env-vars-definitive's runner-side sanity check requires
#: base_url/model on whatever DeepSeekClient() resolves to, real or
#: faked - set as class attributes on every fake client below.
_FAKE_BASE_URL = "http://fake-client.test/v1"
_FAKE_MODEL = "fake-model"


class PerfectClient:
    """Returns the task's own real ground-truth pipeline as the flat
    JSON object, verbatim, for every call regardless of seed."""

    base_url = _FAKE_BASE_URL
    model = _FAKE_MODEL

    def complete(self, model, system, user, temperature=0.0, max_tokens=None, seed=None):
        content = _flat_response(_PIPELINE_SYMBOLS)
        return CallResult(
            model=model, content=content, prompt_tokens=1, completion_tokens=1,
            total_tokens=2, cost_usd=0.0, latency_seconds=0.0, seed=seed,
        )


class WrongClient:
    base_url = _FAKE_BASE_URL
    model = _FAKE_MODEL

    def complete(self, model, system, user, temperature=0.0, max_tokens=None, seed=None):
        content = _flat_response(["unrelated.symbol.that.does.not.match"])
        return CallResult(
            model=model, content=content, prompt_tokens=1, completion_tokens=1,
            total_tokens=2, cost_usd=0.0, latency_seconds=0.0, seed=seed,
        )


class ProseClient:
    base_url = _FAKE_BASE_URL
    model = _FAKE_MODEL

    def complete(self, model, system, user, temperature=0.0, max_tokens=None, seed=None):
        content = "The pipeline starts with foo and ends with bar."
        return CallResult(
            model=model, content=content, prompt_tokens=1, completion_tokens=1,
            total_tokens=2, cost_usd=0.0, latency_seconds=0.0, seed=seed,
        )


def _run_mocked_pilot(monkeypatch, python_repo_root: str, fake_client):
    import benchmarks.runner as runner_module

    PrismEngineCache._process_graph_cache.clear()
    monkeypatch.setattr(runner_module, "resolve", lambda repo, force=False: python_repo_root)
    monkeypatch.setattr(
        runner_module,
        "load_tasks_from_dir",
        lambda tasks_dir: SimpleNamespace(accepted=[_fake_task()], rejected=[]),
    )
    monkeypatch.setattr(runner_module, "DeepSeekClient", lambda: fake_client)
    return run_evaluation(
        repo="django",
        budgets=[4000],
        tasks_dir="unused",  # monkeypatched load_tasks_from_dir ignores this
        seeds=_SEEDS,
        dry_run=False,
        use_pragmatic_oracle=True,
    )


def test_1_perfect_answer_scores_1_0_for_every_engine(monkeypatch, python_repo_root):
    run = _run_mocked_pilot(monkeypatch, python_repo_root, PerfectClient())

    assert run.records, "expected at least one (task, engine, budget) record"
    assert len(run.records) == 5, [r.engine_name for r in run.records]  # PragmaticOracle + 4 baseline/prism
    for record in run.records:
        assert len(record.tsr_scores) == len(_SEEDS), record.engine_name
        assert record.tsr_scores == [1.0] * len(_SEEDS), (record.engine_name, record.tsr_scores)


def test_2_wrong_answer_scores_0_0_for_every_engine(monkeypatch, python_repo_root):
    run = _run_mocked_pilot(monkeypatch, python_repo_root, WrongClient())

    assert len(run.records) == 5
    for record in run.records:
        assert len(record.tsr_scores) == len(_SEEDS), record.engine_name
        assert record.tsr_scores == [0.0] * len(_SEEDS), (record.engine_name, record.tsr_scores)


def test_3_malformed_prose_output_fails_gracefully(monkeypatch, python_repo_root):
    run = _run_mocked_pilot(monkeypatch, python_repo_root, ProseClient())  # must not raise

    assert len(run.records) == 5
    for record in run.records:
        assert len(record.tsr_scores) == len(_SEEDS), record.engine_name
        assert record.tsr_scores == [0.0] * len(_SEEDS), (record.engine_name, record.tsr_scores)


def test_4_perfect_answer_yields_sensible_prism_diagnostics(monkeypatch, python_repo_root):
    """Sanity check on the Prism v1.1 record's diagnostics, not on TSR
    scoring. cpi_strict/cpi_fractional/fcc are checked as hard
    assertions - CPI measures whether Prism's own *retrieval* covers
    the adjudicated pipeline, which for this 4-symbol pipeline (all
    directly reachable from the seed, budget=4000 on a 5-file fixture
    repo) is expected to be perfect regardless of what the (mocked) LLM
    says.

    fpr_oracle is NOT asserted as a hard 0.0, despite the request's own
    premise. Measured directly before writing this assertion (both
    against real Django and against this fixture repo): fpr_oracle is
    divergence between Prism's own selected-symbol set and
    PragmaticOracle's - a real retrieval-strategy comparison, completely
    independent of what the fake LLM client returns. A PerfectClient
    controls only the scored *answer*, never what Prism or the Oracle
    actually retrieve. Measured value on this exact fixture: Oracle
    retrieves a narrow 4-node package while Prism retrieves a broader
    causally-connected package (real budget headroom pulling in more
    than the narrow annotated set, by design - see build.py's Gap 2
    Blocker 2 Option B docstring) - fpr_oracle ~= 0.73, not 0.0.
    "TSR=1.0" gives no reason to expect fpr_oracle==0.0; asserting it
    would make this test fail for the right harness and the wrong
    reason. Reported as a real diagnostic value instead.
    """
    run = _run_mocked_pilot(monkeypatch, python_repo_root, PerfectClient())

    prism_record = next(r for r in run.records if r.engine_name == "prism_v11")
    diag = prism_record.diagnostics

    assert diag["cpi_strict"] == 1.0, diag
    assert diag["cpi_fractional"] == 1.0, diag
    assert isinstance(diag["fcc"], float), diag
    assert diag["fpr_oracle"] is None or isinstance(diag["fpr_oracle"], float), diag


def test_default_max_tokens_is_the_chosen_safety_ceiling():
    """Reads DEFAULT_MAX_TOKENS from benchmarks.tsr.client, not a
    hardcoded literal duplicated here - a change to the constant fails
    this test rather than silently going unnoticed.

    2048, not the original 4096 - fix-maxtokens-2048's own rationale:
    a real Kaggle pilot run's successful completions ranged 482-1606
    tokens; 1024 would have truncated legitimate wins (t02_002/t02_005,
    both ~1600), but 4096 let 33 degenerate off-contract cells run to
    the cap at ~180-207s each instead of failing at ~90s. 2048 is an
    evidence-based cap from the observed response distribution, not a
    theoretical estimate of the model's true output limit."""
    assert DEFAULT_MAX_TOKENS == 2048
