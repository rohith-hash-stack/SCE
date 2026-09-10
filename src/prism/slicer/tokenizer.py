"""Exact BPE token counting for the core context-packing engine (Issues
#11/#13).

`ContextKnapsackPacker` previously gated its whole token budget on a flat
word-count heuristic (`TOKENS_PER_WORD = 2.6`) precisely because a real
BPE tokenizer needs a model-specific vocabulary this "zero-install,
offline-first" engine didn't want to depend on. Measured against a real
punctuation-aware tokenizer, that heuristic drifted well outside any
single fixed ratio's error bars across languages (source code compresses
far denser than prose in Python, even denser in Go, denser still in
near-binary formats like minified JSON) - a genuine +-25%-class drift the
knapsack's own `SAFETY_MARGIN` could only partially absorb, and could
still overflow a caller's real LLM context window.

`tiktoken` is now a core dependency (not dev-only - see `pyproject.toml`),
so this module prefers its `cl100k_base` encoding (the standard reference
BPE vocabulary for GPT-3.5/4-class budgeting) and only falls back to a
deterministic regex approximation if the encoding table can't be loaded
(a genuinely air-gapped environment with `tiktoken`'s remote blob host
unreachable on first use) - never raising, so a transient tokenizer
failure degrades this one call rather than crashing a whole pack.
"""
from __future__ import annotations

import re
import threading

# Approximates BPE token boundaries well enough to stay a safe (never
# under-counting-by-much) fallback: runs of word characters count as one
# token, and every other non-space character (punctuation, operators,
# brackets - both far denser in source code than in prose) counts as its
# own token.
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
    input degrades to the fallback for that call rather than aborting a
    pack partway through.
    """
    if not text:
        return 0
    encoding = _STATE.encoding
    if encoding is not None:
        try:
            # disallowed_special=() lets tokenizer text contain sequences
            # that merely resemble special tokens (e.g. inside a
            # docstring or a fenced code block) without raising - packed
            # context is arbitrary source code, not a trusted chat prompt.
            return len(encoding.encode(text, disallowed_special=()))
        except Exception:  # noqa: BLE001 - degrade, don't crash the pack
            pass
    return len(_FALLBACK_TOKEN_RE.findall(text))
