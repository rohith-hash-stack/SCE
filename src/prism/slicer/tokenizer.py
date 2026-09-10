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
from pathlib import Path

TIKTOKEN_ENCODING_NAME = "cl100k_base"

#: Item 1 (second post-implementation audit): a vendored, offline copy of
#: cl100k_base's merge-rank table, checked *before* ever attempting
#: `tiktoken.get_encoding`'s network fetch. This repository does not
#: currently ship that file here - see this module's own docstring for
#: why (no network path exists in this project's own development/CI
#: sandbox to fetch and verify it, and a fabricated/placeholder binary
#: would silently corrupt every token count, which is strictly worse
#: than the honest regex fallback this module already has) - but the
#: loading code below is real and works the moment a maintainer commits
#: the real ~1.6MB asset here from a machine with network access
#: (`tiktoken.get_encoding("cl100k_base")` once, then copy the file
#: `tiktoken`'s own cache wrote - see `tests/fixtures/tokens/README.md`
#: for the equivalent `TIKTOKEN_CACHE_DIR` mechanism, which achieves the
#: same zero-network-at-runtime property without duplicating the asset
#: into version control).
OFFLINE_BPE_ASSET_PATH = Path(__file__).resolve().parent / "assets" / "cl100k_base.tiktoken"

# Splits a `\w+` run into subword pieces at each lower->upper or
# upper->upper-then-lower boundary - the standard camelCase/PascalCase
# split point (`calculateTotalOffset` -> `calculate`/`Total`/`Offset`,
# `HTTPServer` -> `HTTP`/`Server`). Mirrors how a BPE vocabulary trained
# on real code tends to fragment compound identifiers into whole-word
# subpieces, which is what makes counting a long identifier as a single
# token (the pre-A1 fallback's behavior) an under-count.
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

_ALL_DIGITS_RE = re.compile(r"^\d+$")

# Item 2 (second post-implementation audit): multi-character compound
# operators/punctuation that a real BPE vocabulary trained on code
# typically merges into a single token (`->`, `==`, `!=`, `::`, `:=`,
# `...`, plus the common comparison/logical/increment compounds) - each
# matched here as ONE token, not the 2-3 single-character tokens the
# base `[^\w\s]` alternative below would otherwise count them as.
# Ordered longest-first so `...` matches before a lone `.`, `==` before
# a lone `=`, etc. (regex alternation picks the first matching branch,
# not the longest).
_COMPOUND_OPERATORS = (
    "...", "->", "=>", "::", ":=", "==", "!=", "<=", ">=", "&&", "||", "++", "--", "**",
)

# Item 2: a quoted string literal (single/double/triple-quoted Python,
# or backtick JS/Go template/raw strings) is tokenized as one contiguous
# unit by byte-density rather than run through the identifier/punct
# splitting below - real BPE tokenizes arbitrary string *content*
# (prose, URLs, JSON blobs, ...) far more densely than source-code
# identifiers, so applying the same subword-splitting heuristics inside
# a long string literal would both misrepresent what's actually being
# counted and be needlessly expensive on a large embedded blob. Short
# strings (<=32 chars) are cheap either way and fall through to the
# ordinary path instead, since the length-based heuristic below is
# calibrated for the "large embedded blob" case, not (e.g.) a bare `"ok"`.
_STRING_LITERAL_RE = re.compile(
    r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'|`(?:[^`\\]|\\.)*`'
)
STRING_LITERAL_DENSITY_THRESHOLD = 32
STRING_LITERAL_BYTES_PER_TOKEN = 3

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
# characters, a known multi-character compound operator (Item 2 -
# tried before the single-punct-char alternative so e.g. `->` matches
# as one `compound` unit, not two `punct` ones), or exactly one other
# non-space character - named groups so `_fallback_count_tokens` can
# tell which alternative matched without re-testing the character class.
_FALLBACK_TOKEN_RE = re.compile(
    r"(?P<word>\w+)|(?P<compound>" + "|".join(re.escape(op) for op in _COMPOUND_OPERATORS) + r")|(?P<punct>[^\w\s])"
)


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


def get_offline_bpe_encoding():
    """Construct a real `tiktoken.Encoding` directly from
    `OFFLINE_BPE_ASSET_PATH`, bypassing `tiktoken.get_encoding`'s network
    fetch entirely - `load_tiktoken_bpe` only ever reads the local file
    this points at. Raises `FileNotFoundError` if that asset isn't
    present (the caller, `_TokenizerState.resolve` below, catches this
    and falls through to the network-attempting path instead - the
    presence of this file is optional, checked once per process, never
    required).
    """
    import tiktoken
    from tiktoken.load import load_tiktoken_bpe

    if not OFFLINE_BPE_ASSET_PATH.exists():
        raise FileNotFoundError(f"No vendored offline BPE asset at {OFFLINE_BPE_ASSET_PATH}")
    mergeable_ranks = load_tiktoken_bpe(str(OFFLINE_BPE_ASSET_PATH))
    return tiktoken.Encoding(
        name="cl100k_base_offline",
        pat_str=(
            r"""(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}{1,3}"""
            r"""| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"""
        ),
        mergeable_ranks=mergeable_ranks,
        special_tokens={
            "<|endoftext|>": 100257,
            "<|fim_prefix|>": 100258,
            "<|fim_middle|>": 100259,
            "<|fim_suffix|>": 100260,
            "<|endofprompt|>": 100276,
        },
    )


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
            # Item 1: try the vendored-offline path first - zero network
            # calls, zero dependency on this process's egress policy -
            # and only fall through to tiktoken's own (network-capable)
            # `get_encoding` if no local asset is present.
            try:
                self._encoding = get_offline_bpe_encoding()
                self._backend_name = f"tiktoken:{TIKTOKEN_ENCODING_NAME}_offline"
                self._resolved = True
                return
            except Exception:  # noqa: BLE001 - fall through to the network path
                pass
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


#: Item 1: alias for `is_exact()` under the audit's own naming - every
#: existing call site in this codebase already uses `is_exact()`, kept
#: as the primary name; this exists so `tokenizer.is_real_bpe` (a
#: property-style read, matching the audit's exact phrasing) also works.
is_real_bpe = is_exact


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


def _tokenize_ordinary_span(text: str) -> int:
    """The word/compound-operator/punctuation counting loop
    `_fallback_count_tokens` used to run over its whole input directly -
    factored out so it can be applied separately to the code spans
    between string literals (Item 2) without duplicating this loop."""
    total = 0
    for match in _FALLBACK_TOKEN_RE.finditer(text):
        word = match.group("word")
        if word is not None:
            total += _subword_token_count(word)
        else:
            total += 1
    return total


def _string_literal_token_count(literal: str) -> int:
    """Item 2: a quoted string literal's token count, by byte density
    rather than the identifier/punct splitting `_fallback_count_tokens`
    applies to ordinary code text - see `_STRING_LITERAL_RE`'s own
    comment for why. Quote characters themselves are excluded from the
    length used (`literal[1:-1]` for a single/double-quoted string, the
    3-char delimiter for a triple-quoted one) since they're structural
    delimiters, not content.
    """
    if literal.startswith(('"""', "'''")):
        content = literal[3:-3]
    else:
        content = literal[1:-1]
    if len(content) <= STRING_LITERAL_DENSITY_THRESHOLD:
        # Short strings are cheap either way - let them fall through to
        # the ordinary word/compound/punct path instead (this function
        # is only reached for a span `_STRING_LITERAL_RE` matched at
        # the call site below, which already checks this threshold
        # before delegating here, so this branch is defensive, not
        # reachable in normal use - see `_fallback_count_tokens`).
        return max(1, len(_FALLBACK_TOKEN_RE.findall(literal)))
    # +2 for the delimiter/quote characters themselves - a real BPE
    # vocabulary tokenizes a quote character too, it's just cheap
    # relative to the content this heuristic is actually calibrated for.
    return (len(content) + STRING_LITERAL_BYTES_PER_TOKEN - 1) // STRING_LITERAL_BYTES_PER_TOKEN + 2


def _fallback_count_tokens(text: str) -> int:
    """Issue A1 (fail-closed subword/digit splitting), extended by Item 2
    (second post-implementation audit) with compound-operator and
    string-literal handling. A word-character run is decomposed into a
    conservative subword-piece estimate (`_subword_token_count`) rather
    than counted flatly as one token; a recognized multi-character
    compound operator (`_COMPOUND_OPERATORS`) counts as one token, not
    one per character; every other non-space character still counts as
    its own token; a quoted string literal longer than
    `STRING_LITERAL_DENSITY_THRESHOLD` is counted by byte density
    (`_string_literal_token_count`) instead of run through the
    identifier-splitting path meant for code, not prose/blob content.
    `FALLBACK_SAFETY_MULTIPLIER` is applied once to the summed total,
    ceiling-rounded, as an explicit buffer against input shapes even
    these heuristics don't model well - see the module docstring for why
    fail-*closed* (over-count) is the correct direction for a budget-
    admission fallback.
    """
    total = 0
    pos = 0
    for literal_match in _STRING_LITERAL_RE.finditer(text):
        # Ordinary code text between the previous literal (or the start
        # of `text`) and this one, tokenized the normal way.
        total += _tokenize_ordinary_span(text[pos : literal_match.start()])
        literal = literal_match.group(0)
        content_len = len(literal) - (6 if literal.startswith(('"""', "'''")) else 2)
        if content_len > STRING_LITERAL_DENSITY_THRESHOLD:
            total += _string_literal_token_count(literal)
        else:
            total += _tokenize_ordinary_span(literal)
        pos = literal_match.end()
    total += _tokenize_ordinary_span(text[pos:])
    if total == 0:
        return 0
    return math.ceil(total * FALLBACK_SAFETY_MULTIPLIER)
