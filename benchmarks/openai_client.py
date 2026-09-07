"""Thin OpenAI SDK wrapper shared by `live_eval.py` and `clone_eval.py`:
API key loading (.env + environment), a `complete()` call that tracks usage,
latency, and estimated cost, and clean, actionable error handling.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

API_KEY_ENV_VAR = "OPENAI_API_KEY"

# USD per 1,000,000 tokens. Verify against https://platform.openai.com/docs/pricing
# before trusting cost figures for anything but a rough order-of-magnitude
# estimate - OpenAI revises pricing over time and this table is a snapshot,
# not a live lookup (this tool has no network access to that pricing page).
# An unlisted model still runs fine; its cost is just reported as `None`
# rather than silently guessed, unless overridden with --price-in/--price-out.
PRICING_PER_MILLION_TOKENS: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-2024-08-06": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o-mini-2024-07-18": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "o1": (15.00, 60.00),
    "o1-mini": (1.10, 4.40),
    "o3-mini": (1.10, 4.40),
}


class OpenAIClientError(Exception):
    """Base class for user-facing failures from this module."""


class MissingAPIKeyError(OpenAIClientError):
    pass


class LLMCallError(OpenAIClientError):
    """Wraps any failure from the underlying OpenAI SDK call with a plain-
    English message; the original exception is chained via `__cause__`.
    """


def _load_dotenv_if_present() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()  # no-op if no .env file is found; never overrides a real env var


def load_api_key() -> str:
    """Read `OPENAI_API_KEY` from the environment, first loading a `.env`
    file (via python-dotenv) if one is present in the working directory or
    any parent. Raises `MissingAPIKeyError` with setup instructions if the
    key still isn't set - callers should catch this and print `str(exc)`
    rather than letting a traceback surface.
    """
    _load_dotenv_if_present()
    api_key = os.environ.get(API_KEY_ENV_VAR)
    if not api_key:
        raise MissingAPIKeyError(
            f"{API_KEY_ENV_VAR} is not set.\n\n"
            "Set it one of these ways:\n"
            f"  export {API_KEY_ENV_VAR}=sk-...\n"
            "  or create a .env file (in the project root) containing:\n"
            f"    {API_KEY_ENV_VAR}=sk-...\n\n"
            "Get a key at https://platform.openai.com/api-keys"
        )
    return api_key


def estimate_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    price_in_override: float | None = None,
    price_out_override: float | None = None,
) -> float | None:
    """Estimated cost in USD, or `None` when the model isn't in the pricing
    table and no override was given - never silently guesses.
    """
    if price_in_override is not None and price_out_override is not None:
        price_in, price_out = price_in_override, price_out_override
    else:
        pricing = PRICING_PER_MILLION_TOKENS.get(model)
        if pricing is None:
            return None
        price_in, price_out = pricing
    return round((prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000, 6)


@dataclass(frozen=True)
class CallResult:
    model: str
    content: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float | None
    latency_seconds: float


class LLMClient:
    """A single, narrow entry point onto the OpenAI chat completions API -
    every call goes through `complete()` so usage/cost/latency tracking and
    error handling live in exactly one place.
    """

    def __init__(self, api_key: str | None = None, price_in_override: float | None = None, price_out_override: float | None = None) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise OpenAIClientError(
                "the 'openai' package is not installed. Install it with: pip install -e '.[dev]'"
            ) from exc
        self._client = OpenAI(api_key=api_key or load_api_key())
        self._price_in_override = price_in_override
        self._price_out_override = price_out_override

    def complete(
        self,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> CallResult:
        import openai as openai_module

        start = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except openai_module.AuthenticationError as exc:
            raise LLMCallError(
                f"OpenAI rejected the API key (authentication error): {exc}. "
                f"Check that {API_KEY_ENV_VAR} is correct and active."
            ) from exc
        except openai_module.RateLimitError as exc:
            raise LLMCallError(f"OpenAI rate-limited this request: {exc}") from exc
        except openai_module.APIConnectionError as exc:
            raise LLMCallError(f"could not reach the OpenAI API (network error): {exc}") from exc
        except openai_module.NotFoundError as exc:
            raise LLMCallError(f"model '{model}' was not found or is not available to this account: {exc}") from exc
        except openai_module.APIStatusError as exc:
            raise LLMCallError(f"OpenAI API returned an error (status {exc.status_code}): {exc}") from exc
        latency = time.perf_counter() - start

        choice = response.choices[0]
        content = choice.message.content or ""
        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        total_tokens = usage.total_tokens if usage else prompt_tokens + completion_tokens

        cost = estimate_cost_usd(
            model, prompt_tokens, completion_tokens, self._price_in_override, self._price_out_override
        )

        return CallResult(
            model=model,
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            latency_seconds=round(latency, 4),
        )
