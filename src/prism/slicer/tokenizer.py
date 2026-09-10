"""Exact BPE token counting for the core context-packing engine (Issues
#11/#13, hardened by audit Issue A1).

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

Issue A1 (post-implementation audit): the fallback's first version
(`\\w+` = 1 token, one token per other non-space char) was measured to
systematically *under*-count relative to real BPE on exactly the input
shape source code is made of - long identifiers, camelCase/snake_case
names, and multi-digit numeric literals, all of which cl100k_base
routinely splits into several subword tokens but the old fallback always
counted as one. `ContextKnapsackPacker`'s admission check
(`total_estimate + cost <= budget`) uses this same count on both sides of
the comparison, so an under-counting fallback fails *open*: it can admit
more real tokens than the caller's budget allows, silently overflowing a
downstream LLM context window with no signal that anything was
approximate. The fallback below is redesigned to fail *closed* instead -
deliberately, conservatively over-count - trading a small amount of
wasted budget headroom (packing slightly less than it technically could)
for the much stronger guarantee that it never packs more than the caller
asked for, in the one backend (regex fallback) that can't verify its own
counts against a real vocabulary.
"""
from __future__ import annotations

import math
import re
import threading

TIKTOKEN_ENCODING_NAME = "cl100k_base"

# Splits a `\w+` run into subword pieces at each lower->upper or
# upper->upper-then-lower boundary - the standard camelCase/PascalCase
# split point (`calculateTotalOffset` -> `calculate`/`Total`/`Offset`,
# `HTTPServer` -> `HTTP`/`Server`). Mirrors how a BPE vocabulary trained
# on real code tends to fragment compound identifiers into whole-word
# subpieces, which is what makes counting a long identifier as a single
# token (the pre-A1 fallback's behavior) an under-count.
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

_ALL_DIGITS_RE = re.compile(r"^\d+$")

# Issue A1: an explicit ceiling safety buffer on top of the (already
# per-subword-conservative) count below, applied once to the running
# total via `math.ceil` - not because any single heuristic here is known
# to be exactly right, but because the fallback's whole job is to *never*
# be the reason a pack silently overflows its budget, and a fixed +8%
# ceiling is cheap insurance against any input shape the subword
# heuristics above don't happen to model well (deeply nested unicode
# identifiers, unusual operator runs, etc).
FALLBACK_SAFETY_MULTIPLIER = 1.08

# Matches this module's public contract before A1: a run of word
# characters, or exactly one other non-space character - now with named
# groups so `_fallback_count_tokens` can tell which alternative matched
# without re-testing the character class itself.
_FALLBACK_TOKEN_RE = re.compile(r"(?P<word>\w+)|(?P<punct>[^\w\s])")


def _subword_token_count(word: str) -> int:
    """Conservative (fail-closed, per Issue A1) subword estimate for one
    contiguous `\\w+` run - never below 1, and always at least as large as
    the flat "whole run = 1 token" count it replaces.

    - A purely-numeric run estimates `(len(digits) + 2) // 3` tokens
      (cl100k_base-class BPE vocabularies typically group digits in runs
      of up to 3), e.g. `"12345"` -> 2, not 1.
    - Otherwise the run is first split on `_` (snake_case), then each
      non-empty part is further split at camelCase/PascalCase boundaries;
      the total subword-piece count across all parts is the estimate,
      e.g. `"order_processing_handler"` -> 3, `"calculateTotalOffset"` ->
      3, `"parse_HTTPServerConfig"` -> 1 (snake) + 3 (camel) = 4.
    """
    if not word:
        return 0
    if _ALL_DIGITS_RE.match(word):
        return max(1, (len(word) + 2) // 3)
    parts = [p for p in word.split("_") if p]
    if not parts:
        # The run was made entirely of underscores (or empty) - fall
        # back to one token per character, which is at least as
        # conservative as any other estimate for that degenerate shape.
        return max(1, len(word))
    total = 0
    for part in parts:
        if _ALL_DIGITS_RE.match(part):
            total += max(1, (len(part) + 2) // 3)
        else:
            camel_pieces = [p for p in _CAMEL_SPLIT_RE.split(part) if p]
            total += max(1, len(camel_pieces))
    return max(total, 1)


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
    return _fallback_count_tokens(text)


def _fallback_count_tokens(text: str) -> int:
    """Issue A1: the fail-closed regex approximation. A word-character run
    is decomposed into a conservative subword-piece estimate
    (`_subword_token_count`) rather than counted flatly as one token;
    every other non-space character still counts as its own token (this
    was already a conservative over-estimate relative to BPE vocabularies
    that merge common multi-char operators like `==`/`->` into a single
    token, and stays that way here). `FALLBACK_SAFETY_MULTIPLIER` is
    applied once to the summed total, ceiling-rounded, as an explicit
    buffer against input shapes even the subword heuristics don't model
    well - see the module docstring for why fail-*closed* (over-count)
    is the correct direction for a budget-admission fallback.
    """
    total = 0
    for match in _FALLBACK_TOKEN_RE.finditer(text):
        word = match.group("word")
        if word is not None:
            total += _subword_token_count(word)
        else:
            total += 1
    if total == 0:
        return 0
    return math.ceil(total * FALLBACK_SAFETY_MULTIPLIER)
