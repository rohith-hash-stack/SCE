"""The one LLM client every arm uses: an OpenAI-compatible chat endpoint
(Ollama's /v1 or llama-server), same model, same sampling for every arm.

`ChatLLM.__call__(system, user, max_tokens, seed, purpose)` returns a
`Completion`. Tests and dry runs pass any callable with that signature.
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass

from harness import config as C


@dataclass
class Completion:
    text: str
    prompt_tokens: int
    completion_tokens: int
    latency_seconds: float
    model: str = ""
    finish_reason: str = ""
    purpose: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class ChatLLM:
    def __init__(self, base_url: str | None = None, model: str | None = None,
                 temperature: float = C.TEMPERATURE, timeout: float = C.REQUEST_TIMEOUT_S,
                 num_ctx: int = C.CONTEXT_WINDOW) -> None:
        self.base_url = (base_url or C.LLM_BASE_URL).rstrip("/")
        self.model = model or C.MODEL_NAME
        self.temperature = temperature
        self.timeout = timeout
        self.num_ctx = num_ctx
        self.log: list[dict] = []

    def __call__(self, system: str, user: str, max_tokens: int = C.GENERATION_RESERVE,
                 seed: int | None = None, purpose: str = "answer") -> Completion:
        payload = {
            "model": self.model, "temperature": self.temperature, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            # Ollama reads num_ctx from `options`; llama-server ignores it
            # (its window is fixed by -c).
            "options": {"num_ctx": self.num_ctx},
        }
        if seed is not None:
            payload["seed"] = seed
        req = urllib.request.Request(f"{self.base_url}/chat/completions", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer local"})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            out = json.loads(resp.read().decode("utf-8"))
        dt = time.perf_counter() - t0
        choice = out["choices"][0]
        usage = out.get("usage") or {}
        comp = Completion(
            text=choice["message"].get("content") or "", prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)), latency_seconds=dt,
            model=out.get("model", self.model), finish_reason=choice.get("finish_reason") or "", purpose=purpose,
        )
        self.log.append(comp.to_dict())
        return comp
