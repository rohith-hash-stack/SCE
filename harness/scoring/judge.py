"""T1 judge: a held-out LLM grades a conceptual answer for faithfulness and
answer relevancy (M4). Never a Qwen model: every arm answers with one, and a
Qwen judge would grade its own family's output.

Protocol: `judge(task, answer, context) -> {"faithfulness": float,
"answer_relevancy": float}`, both in [0, 1]. `scorer._score_t1` takes the
minimum as TSR.

Blind to the mechanism. The judge sees only:
- the task's question (`task.query`);
- the answer text (`NormalizedAnswer.answer_text`, else its raw text);
- the delivered context: each item's content, in rank order, numbered
  `[1]`, `[2]`, ... with its first line dropped when that line is the arm's
  item header (`# <source>  (<label>)`), which can name the mechanism
  (`outline`, `[signature]`, tool names).
Never the arm id, the build metadata, item kinds or provenance, the task's
reference answer or gold symbols.

Definitions given to the judge:
- faithfulness: the fraction of the answer's factual claims about the code
  that the context supports. With no context (Arm 0) nothing is supported,
  so a substantive answer scores 0: T1 measures grounded answers.
- answer_relevancy: how directly and completely the answer addresses the
  question, regardless of the context.

`OpenAICompatibleJudge` calls DeepSeek or Gemini through their
OpenAI-compatible chat-completions endpoints (`config.T1_JUDGE_PROVIDERS`)
at temperature 0, asks for a JSON object, and retries on HTTP 429/5xx and
network errors (2, 4, 8, 16 s). `configured_judge()` builds it from
`config.T1_JUDGE_*`; without the API key it raises NotImplementedError,
which the scorer records as an unavailable judge (NaN), as before M4.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Callable, Protocol

from harness import config as C
from harness.scoring.canonical import DeliveredContext, NormalizedAnswer

_HEADER = re.compile(r"^# \S.*$")
_JSON_OBJECT = re.compile(r"\{.*\}", re.S)

SYSTEM_PROMPT = (
    "You are an impartial grader of answers to questions about a code repository. "
    "You are given a QUESTION, the CONTEXT passages the answerer was shown, and the ANSWER. "
    "Grade two things, each a number from 0.0 to 1.0:\n"
    "- faithfulness: the fraction of the answer's factual claims about the code that are supported by the "
    "CONTEXT. A claim the context does not support counts against it even if it may be true. If the context "
    "is empty, no claim is supported. An answer with no factual claims scores 1.0.\n"
    "- answer_relevancy: how directly and completely the answer addresses the QUESTION, regardless of the "
    "context. 1.0 = fully on point; 0.0 = does not address it.\n"
    'Respond with only a JSON object: {"faithfulness": <number>, "answer_relevancy": <number>, '
    '"rationale": "<one sentence>"}'
)


class Judge(Protocol):
    def __call__(self, task, answer: NormalizedAnswer, context: DeliveredContext) -> dict: ...


class JudgeError(RuntimeError):
    pass


def context_passages(ctx: DeliveredContext, max_chars: int = C.T1_JUDGE_MAX_CONTEXT_CHARS) -> tuple[str, bool]:
    """The delivered items as numbered passages, mechanism-blind (see the
    module docstring), and whether the cap cut them."""
    parts, used, cut = [], 0, False
    for n, item in enumerate(sorted(ctx.items, key=lambda it: it.rank), start=1):
        lines = item.content.split("\n")
        if lines and _HEADER.match(lines[0]):
            lines = lines[1:]
        text = f"[{n}]\n" + "\n".join(lines).strip("\n")
        if used + len(text) > max_chars:
            cut = True
            break
        parts.append(text)
        used += len(text) + 2
    return "\n\n".join(parts), cut


def judge_messages(task, answer: NormalizedAnswer, ctx: DeliveredContext) -> tuple[list[dict], bool]:
    passages, cut = context_passages(ctx)
    answer_text = (answer.answer_text or answer.raw_text or "").strip()
    user = (f"QUESTION:\n{task.query}\n\n"
            f"CONTEXT:\n{passages if passages else '(no context)'}\n\n"
            f"ANSWER:\n{answer_text if answer_text else '(empty answer)'}")
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}], cut


def parse_verdict(text: str) -> dict:
    """The two scores from the judge's reply, clamped to [0, 1]."""
    m = _JSON_OBJECT.search(text or "")
    if not m:
        raise JudgeError(f"judge reply has no JSON object: {text[:200]!r}")
    try:
        obj = json.loads(m.group(0))
        out = {k: min(1.0, max(0.0, float(obj[k]))) for k in ("faithfulness", "answer_relevancy")}
    except (ValueError, KeyError, TypeError) as exc:
        raise JudgeError(f"judge reply is not a valid verdict: {text[:200]!r}") from exc
    if "rationale" in obj:
        out["rationale"] = str(obj["rationale"])
    return out


def _default_post(url: str, headers: dict, payload: dict, timeout: float):
    import requests
    return requests.post(url, headers=headers, json=payload, timeout=timeout)


class OpenAICompatibleJudge:
    """DeepSeek or Gemini through an OpenAI-compatible /chat/completions."""

    def __init__(self, provider: str = C.T1_JUDGE_PROVIDER, model: str = C.T1_JUDGE_MODEL,
                 api_key: str | None = None, api_key_env: str = C.T1_JUDGE_API_KEY_ENV,
                 base_url: str | None = None, post: Callable | None = None,
                 backoff: tuple[float, ...] = C.T1_JUDGE_BACKOFF_S, sleep: Callable[[float], None] = time.sleep) -> None:
        if provider not in C.T1_JUDGE_PROVIDERS:
            raise ValueError(f"unknown judge provider {provider!r}")
        if "qwen" in model.lower():
            raise ValueError(f"T1 judge must not be a Qwen model: {model!r}")
        self.provider, self.model = provider, model
        self.base_url = (base_url or C.T1_JUDGE_PROVIDERS[provider]["base_url"]).rstrip("/")
        self.api_key = api_key or os.environ.get(api_key_env)
        if not self.api_key:
            raise NotImplementedError(f"T1 judge not configured: ${api_key_env} is not set ({provider}/{model}; "
                                      f"held-out judge required — must NOT be a Qwen model)")
        self._post = post or _default_post
        self.backoff, self._sleep = tuple(backoff), sleep
        self.name = f"{provider}/{model}"
        #: token usage summed over calls (prompt, completion)
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    def _complete(self, messages: list[dict]) -> str:
        payload = {"model": self.model, "messages": messages, "temperature": C.T1_JUDGE_TEMPERATURE,
                   "max_tokens": C.T1_JUDGE_MAX_TOKENS, "response_format": {"type": "json_object"}}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last = ""
        for attempt in range(len(self.backoff) + 1):
            try:
                resp = self._post(f"{self.base_url}/chat/completions", headers, payload, C.T1_JUDGE_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 - network errors are retried, then reported
                last = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code == 200:
                    body = resp.json()
                    usage = body.get("usage") or {}
                    self.usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
                    self.usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)
                    self.usage["calls"] += 1
                    return body["choices"][0]["message"]["content"] or ""
                last = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if resp.status_code != 429 and resp.status_code < 500:
                    raise JudgeError(f"{self.name}: {last}")
            if attempt < len(self.backoff):
                self._sleep(self.backoff[attempt])
        raise JudgeError(f"{self.name}: gave up after {len(self.backoff) + 1} attempts: {last}")

    def __call__(self, task, answer: NormalizedAnswer, context: DeliveredContext) -> dict:
        messages, cut = judge_messages(task, answer, context)
        verdict = parse_verdict(self._complete(messages))
        verdict["judge"] = self.name
        verdict["context_truncated"] = cut
        return verdict


_CONFIGURED: OpenAICompatibleJudge | None = None


def configured_judge() -> OpenAICompatibleJudge:
    """The judge `config.T1_JUDGE_*` describes, built once. Raises
    NotImplementedError when its API key is not in the environment."""
    global _CONFIGURED
    if _CONFIGURED is None:
        _CONFIGURED = OpenAICompatibleJudge()
    return _CONFIGURED


__all__ = ["Judge", "JudgeError", "OpenAICompatibleJudge", "configured_judge", "context_passages",
           "judge_messages", "parse_verdict"]
