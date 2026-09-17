"""Phase 1.2 (DeepSeek pilot setup) regression coverage for
`benchmarks.tsr.client.DeepSeekClient`: API-key loading, the 2s/4s/8s/16s
429 backoff schedule (then fail), and per-call token-usage logging /
cost estimation via `DEEPSEEK_PRICING_PER_MILLION_TOKENS`.

Never makes a real network call - `DeepSeekClient._client` is replaced
with a fake object after construction (bypassing `openai.OpenAI`'s own
constructor, which requires a real or dummy API key but never actually
connects at construction time).
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx2
import openai
import pytest

from benchmarks.openai_client import LLMCallError
from benchmarks.tsr.client import (
    DEEPSEEK_API_KEY_ENV_VAR,
    DEFAULT_LLM_TIMEOUT_S,
    LLM_TIMEOUT_S_ENV_VAR,
    RATE_LIMIT_BACKOFF_SECONDS,
    DeepSeekClient,
    MissingDeepSeekAPIKeyError,
    load_deepseek_api_key,
)


def test_load_deepseek_api_key_raises_when_unset(monkeypatch):
    monkeypatch.delenv(DEEPSEEK_API_KEY_ENV_VAR, raising=False)
    monkeypatch.setattr("benchmarks.tsr.client._load_dotenv_if_present", lambda: None)

    with pytest.raises(MissingDeepSeekAPIKeyError):
        load_deepseek_api_key()


def test_load_deepseek_api_key_reads_env_var(monkeypatch):
    monkeypatch.setenv(DEEPSEEK_API_KEY_ENV_VAR, "sk-test-123")

    assert load_deepseek_api_key() == "sk-test-123"


def _rate_limit_error() -> openai.RateLimitError:
    response = httpx2.Response(
        status_code=429, request=httpx2.Request("POST", "https://api.deepseek.com/v1/chat/completions")
    )
    return openai.RateLimitError("rate limited", response=response, body=None)


class _FakeCompletions:
    """Raises `openai.RateLimitError` for the first `fail_count` calls,
    then returns a canned successful response - the exact shape
    `DeepSeekClient.complete()`'s retry loop needs to exercise. Records
    the kwargs of the last (successful or not) `.create(...)` call so a
    test can assert on the request payload actually sent."""

    def __init__(self, fail_count: int):
        self.fail_count = fail_count
        self.calls = 0
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        if self.calls <= self.fail_count:
            raise _rate_limit_error()
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )


def _client_with_fake_completions(fail_count: int) -> tuple[DeepSeekClient, _FakeCompletions]:
    client = DeepSeekClient.__new__(DeepSeekClient)
    fake_completions = _FakeCompletions(fail_count)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=fake_completions))
    return client, fake_completions


def test_complete_retries_on_429_and_succeeds_within_backoff_schedule(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(seconds))

    client, fake_completions = _client_with_fake_completions(fail_count=2)
    result = client.complete("deepseek-v4-flash", "system", "user", seed=42)

    assert result.content == "ok"
    assert fake_completions.calls == 3
    assert sleeps == list(RATE_LIMIT_BACKOFF_SECONDS[:2])


def test_complete_fails_after_exhausting_all_four_retries(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(seconds))

    client, fake_completions = _client_with_fake_completions(fail_count=len(RATE_LIMIT_BACKOFF_SECONDS) + 1)

    with pytest.raises(LLMCallError, match="rate-limited"):
        client.complete("deepseek-v4-flash", "system", "user", seed=42)

    assert fake_completions.calls == len(RATE_LIMIT_BACKOFF_SECONDS) + 1
    assert sleeps == list(RATE_LIMIT_BACKOFF_SECONDS)


def test_complete_logs_token_usage_and_estimates_cost_from_deepseek_pricing(monkeypatch, capsys):
    client, _ = _client_with_fake_completions(fail_count=0)

    result = client.complete("deepseek-v4-flash", "system", "user", seed=42)

    assert result.prompt_tokens == 100
    assert result.completion_tokens == 50
    assert result.total_tokens == 150
    # (100 * 0.14 + 50 * 0.28) / 1e6
    assert result.cost_usd == pytest.approx((100 * 0.14 + 50 * 0.28) / 1_000_000)

    stderr = capsys.readouterr().err
    assert "[llm]" in stderr
    assert "prompt_tokens=100" in stderr
    assert "completion_tokens=50" in stderr


def test_complete_returns_zero_cost_for_an_unpriced_model():
    """fix-logging-task-and-cost: an unpriced model (no entry in either
    pricing table, no override) is a real $0.0 cost, not an unknown-cost
    sentinel - true for a local Ollama model tag in particular, which is
    genuinely free to run. estimate_cost_usd's own None (`the model
    isn't in the pricing table and no override was given`) is
    normalized to 0.0 here rather than surfaced as None."""
    client, _ = _client_with_fake_completions(fail_count=0)

    result = client.complete("some-future-deepseek-model", "system", "user", seed=42)

    assert result.cost_usd == 0.0


def test_client_sends_response_format():
    """The flat contract (fix-prompt-flat-contract) is a soft ask - the
    request itself must also constrain the response, via the standard
    OpenAI-compatible `response_format={"type": "json_object"}`
    parameter, honored by both DeepSeek's endpoint and Ollama's
    OpenAI-compatible one (the local Qwen 2.5 7B Instruct Q8_0 SLM setup,
    docs/pilot/stop_condition.md Section 5)."""
    client, fake_completions = _client_with_fake_completions(fail_count=0)

    client.complete("deepseek-v4-flash", "system", "user", seed=42)

    assert fake_completions.last_kwargs is not None
    assert fake_completions.last_kwargs.get("response_format") == {"type": "json_object"}


def test_client_sends_stop_sequences():
    """fix-stop-sequences: every real chat-completions call sends the
    module's STOP_SEQUENCES, so a response that reaches one of its own
    natural boundaries (a triple newline, right after a closing
    `]}`, or a closing code fence) stops generating there instead of
    running to the max_tokens cap - the 31-cell 2048-cap-hit pattern
    from the previous full pilot run."""
    from benchmarks.tsr.client import STOP_SEQUENCES

    client, fake_completions = _client_with_fake_completions(fail_count=0)

    client.complete("deepseek-v4-flash", "system", "user", seed=42)

    assert fake_completions.last_kwargs is not None
    assert fake_completions.last_kwargs.get("stop") == list(STOP_SEQUENCES)


def test_client_respects_timeout_env_var(monkeypatch):
    """LLM_TIMEOUT_S is read inside __init__ (never at import time), so
    a value set before construction is honored - self.timeout must
    reflect it exactly, not just the OpenAI() constructor call
    underneath it (which this test can't introspect without reaching
    into SDK internals)."""
    monkeypatch.setenv(LLM_TIMEOUT_S_ENV_VAR, "42")

    client = DeepSeekClient(api_key="sk-test-dummy")

    assert client.timeout == 42.0


def test_client_defaults_timeout_to_180s_when_unset(monkeypatch):
    monkeypatch.delenv(LLM_TIMEOUT_S_ENV_VAR, raising=False)

    client = DeepSeekClient(api_key="sk-test-dummy")

    assert client.timeout == DEFAULT_LLM_TIMEOUT_S == 180.0


def test_client_reads_env_vars(monkeypatch):
    """fix-client-env-vars regression: DeepSeekClient() constructed with
    no args must reflect LLM_BASE_URL/LLM_MODEL (via LLM_API_KEY_ENV's
    own indirection for the key) as real instance attributes - not just
    pass them silently into the underlying openai.OpenAI client where
    nothing outside this module could ever observe or assert on them."""
    monkeypatch.setenv("LLM_BASE_URL", "http://example.test/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model-x")
    monkeypatch.setenv("LLM_API_KEY_ENV", "TEST_API_KEY")
    monkeypatch.setenv("TEST_API_KEY", "test-key-123")

    c = DeepSeekClient()

    assert c.base_url == "http://example.test/v1"
    assert c.model == "test-model-x"


def test_client_sets_max_retries_to_zero_on_the_underlying_openai_client(monkeypatch):
    """max_retries=0 is deliberate (see client.py's own comment): the
    SDK's own default (2) would let a single slow/hung cell retry the
    full timeout twice more on top of the first attempt, compounding
    the hang case rather than helping it - complete()'s own
    RATE_LIMIT_BACKOFF_SECONDS loop already handles 429s explicitly.
    Asserted directly against the constructed openai.OpenAI instance
    (which exposes max_retries as a real attribute), not just inferred
    from behavior."""
    client = DeepSeekClient(api_key="sk-test-dummy")

    assert client._client.max_retries == 0


def test_client_passes_timeout_to_the_underlying_openai_client(monkeypatch):
    monkeypatch.setenv(LLM_TIMEOUT_S_ENV_VAR, "42")

    client = DeepSeekClient(api_key="sk-test-dummy")

    assert client._client.timeout == 42.0


def test_client_falls_back_to_placeholder_key_for_localhost_ollama(monkeypatch):
    """A local Ollama endpoint needs no real key - the operator setting
    LLM_API_KEY_ENV to point at an unset (or never-created) env var
    must not turn into a MissingDeepSeekAPIKeyError for a localhost
    base_url. Never applies to a real hosted endpoint - see the
    sibling test below."""
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_API_KEY_ENV", "OLLAMA_API_KEY")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)

    client = DeepSeekClient()

    assert client._client.api_key == "ollama"


def test_client_still_raises_for_missing_key_on_a_non_localhost_endpoint(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("LLM_API_KEY_ENV", "SOME_OTHER_KEY")
    monkeypatch.delenv("SOME_OTHER_KEY", raising=False)

    with pytest.raises(MissingDeepSeekAPIKeyError, match="SOME_OTHER_KEY"):
        DeepSeekClient()
