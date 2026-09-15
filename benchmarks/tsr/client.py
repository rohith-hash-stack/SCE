"""v1.1+ Empirical Benchmarking Harness: the TSR pipeline's LLM client -
a thin, TSR-specific wrapper around the existing, already-tested
`benchmarks.openai_client.LLMClient` (extended with a `seed` parameter
for this module's own 5-run protocol) rather than a second OpenAI SDK
wrapper.

**Pilot model (Phase 1, DeepSeek pilot setup)**: `DeepSeekClient` below
talks to DeepSeek's OpenAI-compatible chat completions endpoint (same
`openai` SDK, different `base_url`) - `deepseek-v4-flash`, the current
V-series (non-reasoning) identifier confirmed against
https://api-docs.deepseek.com/quick_start/pricing (the legacy
`deepseek-chat`/`deepseek-reasoner` names were retired 2026-07-24).

**Stated honestly**: real, working code - but no `DEEPSEEK_API_KEY` is
configured in this environment, and this module never calls the API on
its own initiative. Running a real TSR sweep is the operator's own
action, with their own credentials and their own cost. Separately,
`api.deepseek.com` is unreachable from this development environment at
all (organization egress policy blocks it, independent of any API
key) - see `docs/pilot/run_checklist.md`.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

from benchmarks.openai_client import (
    CallResult,
    LLMCallError,
    LLMClient,
    OpenAIClientError,
    estimate_cost_usd,
)

#: The spec's own pinned defaults - DeepSeek pilot (Phase 1).
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_SEEDS: tuple[int, ...] = (42, 43, 44, 45, 46)
DEFAULT_TEMPERATURE = 0.0
#: 13-20x expected response (contract bounds to ~200-300 tokens).
#: Model supports 384K. Chosen as cost ceiling, not design target.
DEFAULT_MAX_TOKENS = 4096

DEEPSEEK_API_KEY_ENV_VAR = "DEEPSEEK_API_KEY"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

#: USD per 1,000,000 tokens, peak rate (cache-miss input / output) - from
#: https://api-docs.deepseek.com/quick_start/pricing. Off-peak (outside
#: weekday 01:00-04:00 UTC and 06:00-10:00 UTC, per docs/pilot/
#: stop_condition.md Section 7) is 50% of these rates; `estimate_cost_usd`
#: is given the peak table below and the caller is responsible for halving
#: the result when a call is known to have run off-peak - this module does
#: not itself track wall-clock time against the peak/off-peak schedule.
DEEPSEEK_PRICING_PER_MILLION_TOKENS: dict[str, tuple[float, float]] = {
    "deepseek-v4-flash": (0.14, 0.28),
}

#: 429 backoff schedule (seconds) - four retries, then the call fails.
RATE_LIMIT_BACKOFF_SECONDS: tuple[float, ...] = (2.0, 4.0, 8.0, 16.0)


class MissingDeepSeekAPIKeyError(OpenAIClientError):
    pass


def _load_dotenv_if_present() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()  # no-op if no .env file is found; never overrides a real env var


def load_deepseek_api_key() -> str:
    """Read `DEEPSEEK_API_KEY` from the environment, first loading a
    `.env` file (via python-dotenv) if one is present. Raises
    `MissingDeepSeekAPIKeyError` with setup instructions if the key still
    isn't set - callers should catch this and print `str(exc)` rather
    than letting a traceback surface."""
    _load_dotenv_if_present()
    api_key = os.environ.get(DEEPSEEK_API_KEY_ENV_VAR)
    if not api_key:
        raise MissingDeepSeekAPIKeyError(
            f"{DEEPSEEK_API_KEY_ENV_VAR} is not set.\n\n"
            "Set it one of these ways:\n"
            f"  export {DEEPSEEK_API_KEY_ENV_VAR}=sk-...\n"
            "  or create a .env file (in the project root) containing:\n"
            f"    {DEEPSEEK_API_KEY_ENV_VAR}=sk-...\n\n"
            "Get a key at https://platform.deepseek.com/api_keys"
        )
    return api_key


class DeepSeekClient:
    """The DeepSeek pilot's own LLM client - same `complete()` shape as
    `benchmarks.openai_client.LLMClient` (so `run_tsr_prompt` below works
    unchanged with either), pointed at DeepSeek's OpenAI-compatible
    endpoint instead of OpenAI's own. A real, separate client rather than
    a subclass of `LLMClient`, since `LLMClient` is shared by several
    OpenAI-only harness tools (`live_eval.py`, `clone_eval.py`,
    `run_comparison_suite.py`, etc.) that must keep talking to OpenAI
    unaffected by this pilot's own base URL/API key/retry policy.
    """

    def __init__(self, api_key: str | None = None, base_url: str = DEEPSEEK_BASE_URL) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise OpenAIClientError(
                "the 'openai' package is not installed. Install it with: pip install -e '.[dev]'"
            ) from exc
        self._client = OpenAI(api_key=api_key or load_deepseek_api_key(), base_url=base_url)

    def complete(
        self,
        model: str,
        system: str,
        user: str,
        temperature: float = DEFAULT_TEMPERATURE,
        max_tokens: int | None = DEFAULT_MAX_TOKENS,
        seed: int | None = None,
    ) -> CallResult:
        """One DeepSeek chat-completions call, retrying on HTTP 429
        (`openai.RateLimitError`) per `RATE_LIMIT_BACKOFF_SECONDS`
        (2s, 4s, 8s, 16s) before giving up and raising `LLMCallError`.
        Logs prompt/completion/total token usage per call to stderr for
        calibration, regardless of outcome.
        """
        import openai as openai_module

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        attempt = 0
        start = time.perf_counter()
        while True:
            try:
                response = self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **({"seed": seed} if seed is not None else {}),
                )
                break
            except openai_module.RateLimitError as exc:
                if attempt >= len(RATE_LIMIT_BACKOFF_SECONDS):
                    raise LLMCallError(
                        f"DeepSeek rate-limited this request after {len(RATE_LIMIT_BACKOFF_SECONDS)} retries: {exc}"
                    ) from exc
                delay = RATE_LIMIT_BACKOFF_SECONDS[attempt]
                print(f"[deepseek] 429, retrying in {delay:.0f}s (attempt {attempt + 1}/{len(RATE_LIMIT_BACKOFF_SECONDS)})", file=sys.stderr)
                time.sleep(delay)
                attempt += 1
            except openai_module.AuthenticationError as exc:
                raise LLMCallError(
                    f"DeepSeek rejected the API key (authentication error): {exc}. "
                    f"Check that {DEEPSEEK_API_KEY_ENV_VAR} is correct and active."
                ) from exc
            except openai_module.APIConnectionError as exc:
                raise LLMCallError(f"could not reach the DeepSeek API (network error): {exc}") from exc
            except openai_module.NotFoundError as exc:
                raise LLMCallError(f"model '{model}' was not found or is not available to this account: {exc}") from exc
            except openai_module.APIStatusError as exc:
                raise LLMCallError(f"DeepSeek API returned an error (status {exc.status_code}): {exc}") from exc
        latency = time.perf_counter() - start

        choice = response.choices[0]
        content = choice.message.content or ""
        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        total_tokens = usage.total_tokens if usage else prompt_tokens + completion_tokens

        cost = estimate_cost_usd(
            model, prompt_tokens, completion_tokens,
            *_deepseek_pricing_override(model),
        )

        print(
            f"[deepseek] model={model} seed={seed} prompt_tokens={prompt_tokens} "
            f"completion_tokens={completion_tokens} total_tokens={total_tokens} "
            f"cost_usd={cost} latency_s={latency:.3f}",
            file=sys.stderr,
        )

        return CallResult(
            model=model,
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            latency_seconds=round(latency, 4),
            seed=seed,
        )


def _deepseek_pricing_override(model: str) -> tuple[float | None, float | None]:
    """`estimate_cost_usd`'s own pricing table (`openai_client.
    PRICING_PER_MILLION_TOKENS`) doesn't know about DeepSeek models - this
    resolves against `DEEPSEEK_PRICING_PER_MILLION_TOKENS` instead and
    hands the result back as the `price_in_override`/`price_out_override`
    pair `estimate_cost_usd` already accepts, rather than duplicating its
    division-by-1e6 arithmetic here. `(None, None)` for an unlisted model
    - `estimate_cost_usd` itself falls through to its own (also empty for
    DeepSeek models) table and returns `None`, never a guessed cost.
    """
    pricing = DEEPSEEK_PRICING_PER_MILLION_TOKENS.get(model)
    if pricing is None:
        return None, None
    return pricing


@dataclass
class TSRRunResult:
    seed: int
    call: CallResult


def build_prompt(system_prompt: str, rendered_xml: str, task_prompt: str) -> tuple[str, str]:
    """`(system, user)` for the OpenAI chat API, matching the spec's own
    literal construction `SYSTEM_PROMPT + "\\n\\n" + rendered_xml +
    "\\n\\nTask:\\n" + T.prompt` - `system_prompt` stays the system
    message; the envelope plus task prompt become the user message
    (chat-completions has no single combined-string request shape, so
    this is the natural system/user split of that construction)."""
    user = f"{rendered_xml}\n\nTask:\n{task_prompt}"
    return system_prompt, user


def run_tsr_prompt(
    client: LLMClient | DeepSeekClient,
    system_prompt: str,
    rendered_xml: str,
    task_prompt: str,
    model: str = DEFAULT_MODEL,
    seeds: tuple[int, ...] = DEFAULT_SEEDS,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[TSRRunResult]:
    """Runs the constructed prompt through `client` once per seed in
    `seeds` (5 real API calls at the spec's own defaults) - never
    batched or deduplicated, since a distinct `seed` per request is the
    entire point of the protocol."""
    system, user = build_prompt(system_prompt, rendered_xml, task_prompt)
    results = []
    for seed in seeds:
        call = client.complete(model, system, user, temperature=temperature, max_tokens=max_tokens, seed=seed)
        results.append(TSRRunResult(seed=seed, call=call))
    return results
