"""v1.1+ Empirical Benchmarking Harness: the TSR pipeline's LLM client -
a thin, TSR-specific wrapper around the existing, already-tested
`benchmarks.openai_client.LLMClient` (extended with a `seed` parameter
for this module's own 5-run protocol) rather than a second OpenAI SDK
wrapper.

**Stated honestly**: real, working code - but no `OPENAI_API_KEY` is
configured in this environment, and this module never calls the API on
its own initiative. Running a real TSR sweep is the operator's own
action, with their own credentials and their own cost.
"""
from __future__ import annotations

from dataclasses import dataclass

from benchmarks.openai_client import CallResult, LLMClient

#: The spec's own pinned defaults.
DEFAULT_MODEL = "gpt-4o-2024-11-20"
DEFAULT_SEEDS: tuple[int, ...] = (42, 43, 44, 45, 46)
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 2048


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
    client: LLMClient,
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
