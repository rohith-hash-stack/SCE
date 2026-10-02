"""One tokenizer for every token count in the harness.

Backends:
- `HuggingFaceTokenizer`: Qwen2.5's tokenizer via `transformers`, loaded
  from the Hub id, a local directory, or a GGUF file (llama.cpp and Ollama
  model files embed the exact tokenizer the server uses). Used in Phases
  1-2, on CPU.
- `LlamaServerTokenizer`: llama-server's `/tokenize` endpoint.
- `OllamaPromptCounter`: Ollama has no tokenize endpoint; this counts a
  text as the `prompt_eval_count` of a raw, 1-token generation. Only good
  for the parity check (it costs a forward pass).

`verify_tokenizer_parity` compares the HF backend with a server-side
reference on sample texts. Above `TOKENIZER_PARITY_TOLERANCE` relative
divergence the caller must switch counting to the server-side tokenizer
and log it.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Protocol

from harness import config as C


class Tokenizer(Protocol):
    name: str

    def count(self, text: str) -> int: ...


class HuggingFaceTokenizer:
    def __init__(self, source: str | None = None) -> None:
        from transformers import AutoTokenizer

        source = source or C.TOKENIZER_LOCAL_PATH or C.TOKENIZER_HF_ID
        path = Path(source)
        if path.suffix == ".gguf" and path.is_file():
            self._tok = AutoTokenizer.from_pretrained(str(path.parent), gguf_file=path.name)
        else:
            self._tok = AutoTokenizer.from_pretrained(source)
        self.name = f"hf:{source}"

    def encode(self, text: str) -> list[int]:
        return self._tok.encode(text, add_special_tokens=False)

    def decode(self, ids: list[int]) -> str:
        return self._tok.decode(ids)

    def count(self, text: str) -> int:
        return len(self.encode(text)) if text else 0

    def truncate(self, text: str, max_tokens: int) -> str:
        ids = self.encode(text)
        return text if len(ids) <= max_tokens else self.decode(ids[:max(max_tokens, 0)])


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class LlamaServerTokenizer:
    def __init__(self, base_url: str | None = None, timeout: float = 60) -> None:
        self.base_url = (base_url or C.LLAMA_SERVER_URL).rstrip("/")
        self.timeout = timeout
        self.name = f"llama-server:{self.base_url}"

    def encode(self, text: str) -> list[int]:
        return list(_post_json(f"{self.base_url}/tokenize", {"content": text}, self.timeout)["tokens"])

    def count(self, text: str) -> int:
        return len(self.encode(text)) if text else 0


class OllamaPromptCounter:
    def __init__(self, model: str | None = None, base_url: str = "http://127.0.0.1:11434", timeout: float = 300) -> None:
        self.model = model or C.MODEL_NAME
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.name = f"ollama-prompt-eval:{self.model}"

    def count(self, text: str) -> int:
        if not text:
            return 0
        out = _post_json(f"{self.base_url}/api/generate", {
            "model": self.model, "prompt": text, "raw": True, "stream": False,
            "options": {"num_predict": 1, "temperature": 0},
        }, self.timeout)
        return int(out["prompt_eval_count"])


@dataclass
class ParityReport:
    reference: str
    candidate: str
    per_sample: list[dict] = field(default_factory=list)
    max_divergence: float = 0.0
    total_divergence: float = 0.0
    within_tolerance: bool = True
    tolerance: float = C.TOKENIZER_PARITY_TOLERANCE

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def verify_tokenizer_parity(candidate: Tokenizer, reference: Tokenizer | Callable[[str], int],
                            samples: list[str], tolerance: float = C.TOKENIZER_PARITY_TOLERANCE) -> ParityReport:
    """Relative divergence |cand - ref| / ref per sample and in total. Each
    sample should be distinct (a prompt cache could otherwise shorten a
    server-side count)."""
    ref_count = reference.count if hasattr(reference, "count") else reference
    ref_name = getattr(reference, "name", getattr(reference, "__name__", "reference"))
    report = ParityReport(reference=ref_name, candidate=candidate.name, tolerance=tolerance)
    tot_c = tot_r = 0
    for i, text in enumerate(samples):
        c, r = candidate.count(text), ref_count(text)
        div = abs(c - r) / max(r, 1)
        report.per_sample.append({"i": i, "candidate": c, "reference": r, "divergence": div})
        tot_c, tot_r = tot_c + c, tot_r + r
        report.max_divergence = max(report.max_divergence, div)
    report.total_divergence = abs(tot_c - tot_r) / max(tot_r, 1)
    report.within_tolerance = report.max_divergence <= tolerance
    return report


_ACTIVE: Tokenizer | None = None


@lru_cache(maxsize=4)
def _hf(source: str | None) -> HuggingFaceTokenizer:
    return HuggingFaceTokenizer(source)


def get_tokenizer() -> Tokenizer:
    """The harness-wide tokenizer: whatever `set_tokenizer` installed, else
    the HF Qwen2.5 tokenizer."""
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = _hf(C.TOKENIZER_LOCAL_PATH)
    return _ACTIVE


def set_tokenizer(tok: Tokenizer | None) -> None:
    """Install a backend for every later count (e.g. the server-side one
    after a failed parity check, or a test double)."""
    global _ACTIVE
    _ACTIVE = tok


def count_tokens(text: str) -> int:
    return get_tokenizer().count(text)
