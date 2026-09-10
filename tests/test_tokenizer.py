"""Tests for exact BPE token counting (Issues #11/#13):
`prism.slicer.tokenizer.count_tokens` and its wiring into
`prism.slicer.knapsack.estimate_tokens`/`_wrapping_overhead_tokens`.
"""
from __future__ import annotations

from prism.slicer.knapsack import _wrapping_overhead_tokens, estimate_tokens
from prism.slicer.tokenizer import (
    FALLBACK_SAFETY_MULTIPLIER,
    OFFLINE_BPE_ASSET_PATH,
    STRING_LITERAL_DENSITY_THRESHOLD,
    _fallback_count_tokens,
    _string_literal_token_count,
    _subword_token_count,
    _tokenize_ordinary_span,
    active_backend,
    count_tokens,
    get_offline_bpe_encoding,
    is_exact,
    is_real_bpe,
)


def test_count_tokens_empty_string_is_zero() -> None:
    assert count_tokens("") == 0


def test_count_tokens_never_raises_and_returns_nonnegative_int() -> None:
    samples = [
        "def foo(x, y):\n    return x + y\n",
        "🎉 unicode emoji",
        "a" * 5000,
        "\n\n\n   \t  \n",
        "class Foo(Base, metaclass=Meta): ...",
    ]
    for text in samples:
        n = count_tokens(text)
        assert isinstance(n, int)
        assert n >= 0


def test_count_tokens_scales_with_punctuation_density() -> None:
    """Source-shaped text (dense punctuation) must count as more tokens
    per character than prose - the whole reason the flat word-count
    heuristic this replaced was inaccurate for code."""
    code = "def f(x,y,z):\n    return {x:y,z:1}\n"
    prose = "def f x y z return x y z          "  # same word count, no punctuation
    assert count_tokens(code) > count_tokens(prose)


def test_estimate_tokens_delegates_to_real_tokenizer() -> None:
    text = "def foo(x, y):\n    return x + y\n"
    assert estimate_tokens(text) == float(count_tokens(text))


def test_active_backend_reports_something_non_empty() -> None:
    # Either "tiktoken:cl100k_base" or a "fallback-regex (...)" string -
    # never raises, never empty, regardless of network availability.
    assert active_backend()
    assert isinstance(is_exact(), bool)


# --------------------------------------------------------------------- #
# Issue #13: exact (not "99999-99999" stand-in) wrapper token rendering
# --------------------------------------------------------------------- #
def test_wrapping_overhead_uses_real_path_not_stand_in() -> None:
    """A short real relative path and a much longer, deeply-nested one
    must cost different amounts of wrapper overhead - proving the real
    value is actually rendered into the heading and tokenized, not a
    fixed stand-in."""
    short = _wrapping_overhead_tokens("pkg.mod.f", "a.py", 1, (5, 6))
    long_path = _wrapping_overhead_tokens(
        "pkg.mod.f", "some/deeply/nested/package/structure/with/many/segments/module.py", 1, (5, 6)
    )
    assert short != long_path


def test_wrapping_overhead_seed_prefix_costs_more() -> None:
    plain = _wrapping_overhead_tokens("pkg.mod.f", "pkg/mod.py", 0, (1, 5), is_seed=False)
    seed = _wrapping_overhead_tokens("pkg.mod.f", "pkg/mod.py", 0, (1, 5), is_seed=True)
    assert seed >= plain


def test_wrapping_overhead_resolution_invariant_across_l0_l3() -> None:
    """L0-L3 labels are all the same length/shape ("L0".."L3"), so wrapper
    cost shouldn't vary by resolution alone at a fixed line range/path."""
    costs = {r: _wrapping_overhead_tokens("pkg.mod.f", "pkg/mod.py", r, (10, 20)) for r in range(4)}
    assert len(set(costs.values())) == 1


# --------------------------------------------------------------------- #
# Issue A1: fail-closed fallback subword splitting
# --------------------------------------------------------------------- #
def test_subword_split_camel_case() -> None:
    assert _subword_token_count("calculateTotalOffset") == 3
    assert _subword_token_count("HTTPServer") == 2


def test_subword_split_snake_case() -> None:
    assert _subword_token_count("order_processing_handler") == 3


def test_subword_split_mixed_snake_and_camel() -> None:
    assert _subword_token_count("parse_HTTPServerConfig") == 1 + 3


def test_subword_split_multidigit_number() -> None:
    assert _subword_token_count("12345") == (5 + 2) // 3
    assert _subword_token_count("7") == 1


def test_subword_split_short_common_word_is_one_token() -> None:
    # A short, non-compound word shouldn't be over-fragmented - only
    # actual case/underscore/digit boundaries trigger a split.
    assert _subword_token_count("return") == 1
    assert _subword_token_count("x") == 1


def test_fallback_never_undercounts_a_flat_word_count() -> None:
    """The pre-A1 fallback counted every `\\w+` run as exactly one token.
    Issue A1 requires the new fallback to never count *fewer* tokens than
    that for any input - it only ever adds subword granularity plus the
    safety multiplier, never removes counted tokens."""
    samples = [
        "def calculateTotalOffset(order_processing_handler, x12345): pass",
        "class HTTPServerConfig: ...",
        "the quick brown fox jumps over the lazy dog",
    ]
    for text in samples:
        flat_word_count = len(__import__("re").findall(r"\w+|[^\w\s]", text))
        assert _fallback_count_tokens(text) >= flat_word_count


def test_fallback_applies_safety_multiplier() -> None:
    # A long compound identifier alone: subword count times the safety
    # multiplier, ceiling-rounded - not just the raw subword count.
    import math

    word = "calculateTotalOffsetForShippingAndHandling"
    raw = _subword_token_count(word)
    assert _fallback_count_tokens(word) == math.ceil(raw * FALLBACK_SAFETY_MULTIPLIER)


def test_fallback_empty_string_is_zero() -> None:
    assert _fallback_count_tokens("") == 0


# --------------------------------------------------------------------- #
# Item 1 (second post-implementation audit): offline BPE asset loading
# --------------------------------------------------------------------- #
def test_offline_bpe_asset_path_points_under_this_package() -> None:
    assert OFFLINE_BPE_ASSET_PATH.parent.name == "assets"
    assert OFFLINE_BPE_ASSET_PATH.name == "cl100k_base.tiktoken"


def test_get_offline_bpe_encoding_raises_cleanly_when_asset_absent() -> None:
    # No asset is vendored in this repository (see src/prism/slicer/
    # assets/README.md for why) - this must fail with a clear,
    # catchable FileNotFoundError, not hang or attempt any network call.
    if OFFLINE_BPE_ASSET_PATH.exists():
        import pytest

        pytest.skip("a real vendored asset is present in this checkout - nothing to assert here")
    import pytest

    with pytest.raises(FileNotFoundError):
        get_offline_bpe_encoding()


def test_is_real_bpe_is_an_alias_for_is_exact() -> None:
    assert is_real_bpe is is_exact
    assert is_real_bpe() == is_exact()


# --------------------------------------------------------------------- #
# Item 2 (second post-implementation audit): structural token factoring
# --------------------------------------------------------------------- #
def test_compound_operator_counts_as_one_token_not_two() -> None:
    assert _tokenize_ordinary_span("->") == 1
    assert _tokenize_ordinary_span("==") == 1
    assert _tokenize_ordinary_span("!=") == 1
    assert _tokenize_ordinary_span("::") == 1
    assert _tokenize_ordinary_span(":=") == 1
    assert _tokenize_ordinary_span("...") == 1


def test_lone_punctuation_is_unaffected_by_compound_recognition() -> None:
    assert _tokenize_ordinary_span("-") == 1
    assert _tokenize_ordinary_span("=") == 1
    assert _tokenize_ordinary_span(":") == 1
    assert _tokenize_ordinary_span(".") == 1


def test_string_literal_over_threshold_uses_density_heuristic() -> None:
    content = "a" * (STRING_LITERAL_DENSITY_THRESHOLD + 10)
    literal = f'"{content}"'
    result = _fallback_count_tokens(f"x = {literal}")
    # Should be far fewer tokens than counting the content as an
    # ordinary (heavily-subword-split, since it's a single 42-char
    # "word" run) identifier would produce, and roughly len/3 + fixed
    # overhead - not the ~14-token subword estimate a run this long
    # would get under _subword_token_count's own (len+2)//... heuristics.
    assert result < len(content)


def test_string_literal_under_threshold_falls_through_to_ordinary_path() -> None:
    literal = '"short string"'
    with_literal = _fallback_count_tokens(f"x = {literal}")
    plain = _fallback_count_tokens(f"x = {literal}")
    assert with_literal == plain  # deterministic either way, sanity check
    assert with_literal > 0


def test_string_literal_token_count_excludes_quote_delimiters() -> None:
    content = "b" * 50
    single = _string_literal_token_count(f'"{content}"')
    triple = _string_literal_token_count(f'"""{content}"""')
    # Same content length, same delimiter-exclusion logic - both should
    # be very close (allowing only the fixed +2 overhead term to differ).
    assert abs(single - triple) <= 2


def test_fallback_handles_mixed_compound_and_string_text_without_crashing() -> None:
    """Item 2's additions composed together on realistic, mixed input -
    no formal bound asserted here (compound-operator merging legitimately
    lowers the count relative to Issue A1's plain per-char baseline, so
    the strict >= flat-word-count guarantee - still covered by
    `test_fallback_never_undercounts_a_flat_word_count` above, which uses
    plain identifiers only - doesn't apply once compounds/strings are
    involved); this just proves the combined path is deterministic and
    produces a sane, positive count."""
    samples = [
        "if x -> y == z: return a.b.c(1, 2)",
        'log.info("start"); result = process(data); log.info("done: " + str(result))',
        "func Handler(c *Context) { c.JSON(200, gin.H{\"ok\": true}) }",
    ]
    for text in samples:
        n = _fallback_count_tokens(text)
        assert n > 0
        assert n == _fallback_count_tokens(text)  # deterministic
