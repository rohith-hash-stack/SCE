"""Exact token counting for the benchmark's compression-ratio metric.

Prefers `tiktoken`'s `cl100k_base` encoding (the GPT-4/GPT-3.5 byte-pair
encoding) since that is the standard reference tokenizer for LLM context
budgeting. Prism itself is a 100%-offline, no-network tool, and `tiktoken`
lazily downloads its merge-rank table from a remote blob on first use - so
in a genuinely air-gapped environment (or one without that specific host
allowlisted) loading the encoding will fail. Rather than let that crash the
benchmark, we fall back to a deterministic regex-based approximation and
report which backend actually produced each number, so results stay
honest and reproducible either way.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass

# Approximates BPE token boundaries well enough for a relative comparison:
# runs of word characters count as one token, and every other non-space
# character (punctuation, operators, brackets) counts as its own token.
_FALLBACK_TOKEN_RE = re.compile(r"\w+|[^\w\s]")

TIKTOKEN_ENCODING_NAME = "cl100k_base"


class _TokenizerState:
    """Lazily resolves and memoizes the tokenizer backend, once per process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._resolved = False
        self._encoding = None  # type: ignore[var-annotated]
        self._backend_name = "uninitialized"

    def resolve(self) -> None:
        if self._resolved:
            return
        with self._lock:
            if self._resolved:  # re-check under lock
                return
            try:
                import tiktoken

                self._encoding = tiktoken.get_encoding(TIKTOKEN_ENCODING_NAME)
                self._backend_name = f"tiktoken:{TIKTOKEN_ENCODING_NAME}"
            except Exception as exc:  # noqa: BLE001 - any failure degrades to fallback
                self._encoding = None
                self._backend_name = f"fallback-regex (tiktoken unavailable: {exc.__class__.__name__})"
            self._resolved = True

    @property
    def encoding(self):
        self.resolve()
        return self._encoding

    @property
    def backend_name(self) -> str:
        self.resolve()
        return self._backend_name


_STATE = _TokenizerState()


def active_backend() -> str:
    """Human-readable name of the tokenizer backend actually in use."""
    return _STATE.backend_name


def is_exact() -> bool:
    """True when token counts come from the real tiktoken encoding rather
    than the regex approximation."""
    return _STATE.encoding is not None


def count_tokens(text: str) -> int:
    """Count tokens in `text` using tiktoken when available, else the
    regex-based fallback. Never raises: an encoding failure on a specific
    input degrades to the fallback for that call rather than crashing a
    benchmark run partway through.
    """
    if not text:
        return 0
    encoding = _STATE.encoding
    if encoding is not None:
        try:
            # disallowed_special=() lets tokenizer text contain sequences
            # that merely resemble special tokens (e.g. inside a docstring)
            # without raising - benchmark input is arbitrary source code.
            return len(encoding.encode(text, disallowed_special=()))
        except Exception:  # noqa: BLE001 - degrade, don't crash the run
            pass
    return len(_FALLBACK_TOKEN_RE.findall(text))


@dataclass(frozen=True)
class TokenCount:
    tokens: int
    backend: str
    exact: bool


def count_tokens_detailed(text: str) -> TokenCount:
    return TokenCount(tokens=count_tokens(text), backend=active_backend(), exact=is_exact())
