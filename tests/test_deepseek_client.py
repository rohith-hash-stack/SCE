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
    assert "[deepseek]" in stderr
    assert "prompt_tokens=100" in stderr
    assert "completion_tokens=50" in stderr


def test_complete_returns_none_cost_for_an_unpriced_model():
    client, _ = _client_with_fake_completions(fail_count=0)

    result = client.complete("some-future-deepseek-model", "system", "user", seed=42)

    assert result.cost_usd is None


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
