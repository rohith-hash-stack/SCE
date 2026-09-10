"""Tests for exact BPE token counting (Issues #11/#13):
`prism.slicer.tokenizer.count_tokens` and its wiring into
`prism.slicer.knapsack.estimate_tokens`/`_wrapping_overhead_tokens`.
"""
from __future__ import annotations

from prism.slicer.knapsack import _wrapping_overhead_tokens, estimate_tokens
from prism.slicer.tokenizer import active_backend, count_tokens, is_exact


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
