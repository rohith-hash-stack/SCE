"""Pilot-4 prep, Fix 3: `runner.py`'s checkpoint cells now record the
resolved model tag that actually answered each cell.

Reuses `tests/test_pilot_pipeline_mocked.py`'s own proven zero-cost
seam (`OpenAICompatibleClient` monkeypatched to a fake client, real
`run_evaluation`, real local fixture repo) rather than duplicating its
task/fixture-repo setup - see that module's own docstring for why this
is the right seam for a fast, real `run_evaluation()` test.

**Not `PerfectClient`/`WrongClient`/`ProseClient` from that module,
deliberately**: those fake clients return `CallResult(model=model,
...)` using the raw `model` argument `complete()` receives verbatim -
correct for what they test (score/diagnostics correctness), but
`_run_mocked_pilot` never passes an explicit `model=` through to
`run_evaluation`, so that argument is always `None` there, and
`cell["model"]` would end up `None` too - proving nothing about
whether a *resolved* model tag round-trips. `_ResolvingFakeClient`
below mimics `OpenAICompatibleClient.complete()`'s own real resolution
rule (`resolved_model = model if model is not None else self.model`)
so this test exercises the actual case Fix 3 is for: a real, non-None
resolved model string ending up in the checkpoint.
"""
from __future__ import annotations

from benchmarks.openai_client import CallResult
from benchmarks.runner import load_checkpoint
from tests.test_pilot_pipeline_mocked import _flat_response, _PIPELINE_SYMBOLS, _run_mocked_pilot

_FAKE_MODEL = "fake-resolved-model"


class _ResolvingFakeClient:
    """Same shape as `PerfectClient`, but resolves `model` the way the
    real `OpenAICompatibleClient.complete()` does - `resolved_model =
    model if model is not None else self.model` - instead of echoing
    the raw (here, always-`None`) argument back verbatim."""

    base_url = "http://fake-client.test/v1"
    model = _FAKE_MODEL

    def complete(self, model, system, user, temperature=0.0, max_tokens=None, seed=None, task_id=None, engine=None):
        resolved_model = model if model is not None else self.model
        content = _flat_response(_PIPELINE_SYMBOLS)
        return CallResult(
            model=resolved_model, content=content, prompt_tokens=1, completion_tokens=1,
            total_tokens=2, cost_usd=0.0, latency_seconds=0.0, seed=seed,
        )


def test_checkpoint_stores_model_per_cell(monkeypatch, python_repo_root, tmp_path):
    client = _ResolvingFakeClient()
    _run_mocked_pilot(monkeypatch, python_repo_root, client, tmp_path)

    checkpoint = load_checkpoint(str(tmp_path / "checkpoint.json"))
    cells = checkpoint["cells"]
    assert cells, "expected at least one checkpoint cell to have been written"
    for cell in cells.values():
        assert cell["model"] == _FAKE_MODEL
