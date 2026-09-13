"""Bookmark 1 Item 5: fuzzy seed suggestion on a not-found seed -
`suggest_similar_seeds`/`SeedNotFoundError` in
`prism.packer.submodular_knapsack`. Deterministic, token-Jaccard +
normalized-Levenshtein, never an ML/embedding model.
"""
from __future__ import annotations

import time

from prism.cli import build_pipeline
from prism.packer.submodular_knapsack import (
    SeedNotFoundError,
    pack_symbol_context,
    suggest_similar_seeds,
)

_SOURCE = (
    "class UserService:\n"
    "    def get_user(self, uid):\n"
    "        return uid\n\n"
    "    def get_user_profile(self, uid):\n"
    "        return uid\n\n"
    "    def get_user_settings(self, uid):\n"
    "        return uid\n\n"
    "class OrderService:\n"
    "    def process_order(self, oid):\n"
    "        return oid\n"
)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(_SOURCE)
    return repo


def test_close_typo_returns_the_real_candidate(tmp_path):
    repo = _repo(tmp_path)
    builder, _ = build_pipeline(str(repo))

    candidates = suggest_similar_seeds(builder, "get_user")
    assert "svc.UserService.get_user" in candidates


def test_missing_separators_still_ranks_a_candidate_list(tmp_path):
    """A query with no underscores at all can't Jaccard-match a
    snake_case symbol on tokens - Levenshtein against the symbol's own
    unqualified name is what should carry this one."""
    repo = _repo(tmp_path)
    builder, _ = build_pipeline(str(repo))

    candidates = suggest_similar_seeds(builder, "getuserprofile")
    assert candidates, "expected at least one ranked candidate"
    assert "svc.UserService.get_user_profile" in candidates


def test_unrelated_query_returns_empty_list(tmp_path):
    repo = _repo(tmp_path)
    builder, _ = build_pipeline(str(repo))

    candidates = suggest_similar_seeds(builder, "abcdefg")
    assert candidates == []


def test_returns_at_most_five_ranked_by_similarity_desc(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    lines = ["class Svc:"] + [f"    def get_user_{i}(self, uid):\n        return uid\n" for i in range(10)]
    (repo / "svc.py").write_text("\n".join(lines))
    builder, _ = build_pipeline(str(repo))

    candidates = suggest_similar_seeds(builder, "get_user")
    assert len(candidates) <= 5


def test_ties_broken_by_qualified_name_ascending(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    # two functions with identical token sets relative to the query -
    # a genuine tie, must break alphabetically, not by insertion order.
    (repo / "svc.py").write_text(
        "def zzz_get_x(uid):\n    return uid\n\n\ndef aaa_get_x(uid):\n    return uid\n"
    )
    builder, _ = build_pipeline(str(repo))

    candidates = suggest_similar_seeds(builder, "get_x")
    tied = [c for c in candidates if c.endswith("aaa_get_x") or c.endswith("zzz_get_x")]
    assert tied == sorted(tied), "tied candidates must be ascending by qualified name"


def test_determinism_same_query_same_ordering_twice(tmp_path):
    repo = _repo(tmp_path)
    builder, _ = build_pipeline(str(repo))

    run_1 = suggest_similar_seeds(builder, "get_user")
    run_2 = suggest_similar_seeds(builder, "get_user")
    assert run_1 == run_2


def test_pack_symbol_context_raises_seed_not_found_with_candidates(tmp_path):
    """Primary path still requires an exact match - the fuzzy matcher
    is a fallback attached to the failure, never a silent substitute."""
    repo = _repo(tmp_path)
    builder, _ = build_pipeline(str(repo))

    try:
        pack_symbol_context(builder, "get_user", 2000)
        assert False, "expected SeedNotFoundError for a seed not in the symbol table"
    except SeedNotFoundError as exc:
        assert exc.seed_id == "get_user"
        assert "svc.UserService.get_user" in exc.candidates


def test_pack_symbol_context_still_resolves_a_real_exact_seed(tmp_path):
    """The exact-match path is untouched - a real, exactly-spelled seed
    still works exactly as before Item 5."""
    repo = _repo(tmp_path)
    builder, _ = build_pipeline(str(repo))

    result = pack_symbol_context(builder, "svc.UserService.get_user", 2000)
    assert result.selected


def test_fuzzy_match_performance_on_real_corpus():
    """Performance target: fuzzy match on 50k symbols < 50ms."""
    from benchmarks.corpora.resolver import resolve

    repo_path = str(resolve("django"))
    builder, _ = build_pipeline(repo_path)

    symbol_count = sum(1 for s in builder.symbol_table if s.kind in ("function", "method"))
    assert symbol_count > 10000, f"expected a large real corpus, got only {symbol_count} function/method symbols"

    t0 = time.time()
    suggest_similar_seeds(builder, "get_user_profle")  # a plausible real typo against a large real corpus
    elapsed = time.time() - t0

    assert elapsed < 0.05, f"fuzzy match took {elapsed:.4f}s on {symbol_count} symbols, target < 50ms"
