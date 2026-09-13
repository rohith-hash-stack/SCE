"""Bookmark 1 Item 4: content normalization before hashing -
`_normalize_content`/`_hash_file_bytes` in `prism.traversal._cache_keys`
strip whitespace/formatting differences that cannot change Prism's own
parse output before they ever reach `file_hash_set`, so a purely
cosmetic edit doesn't cost a real cache miss.
"""
from __future__ import annotations

from prism.traversal._cache_keys import target_repo_file_signature

_SOURCE = "def parse_order(raw):\n    return raw\n\n\ndef store_order(data):\n    return data\n"


def _repo(tmp_path, content=_SOURCE):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.py").write_text(content)
    return repo


def test_trailing_whitespace_is_a_hit(tmp_path):
    repo = _repo(tmp_path)
    sig_1 = target_repo_file_signature(str(repo))

    (repo / "x.py").write_text("def parse_order(raw):   \n    return raw\t\n\n\ndef store_order(data):\n    return data\n")
    sig_2 = target_repo_file_signature(str(repo))

    assert sig_1 == sig_2


def test_crlf_to_lf_is_a_hit(tmp_path):
    repo = _repo(tmp_path)
    sig_1 = target_repo_file_signature(str(repo))

    crlf_content = _SOURCE.replace("\n", "\r\n")
    (repo / "x.py").write_bytes(crlf_content.encode())
    sig_2 = target_repo_file_signature(str(repo))

    assert sig_1 == sig_2


def test_removing_a_redundant_blank_line_is_a_hit(tmp_path):
    """Two consecutive blank lines collapse to one - removing one of
    the *redundant* pair leaves the normalized content unchanged."""
    two_blanks = "def parse_order(raw):\n    return raw\n\n\ndef store_order(data):\n    return data\n"
    one_blank = "def parse_order(raw):\n    return raw\n\ndef store_order(data):\n    return data\n"

    repo = _repo(tmp_path, content=two_blanks)
    sig_1 = target_repo_file_signature(str(repo))

    (repo / "x.py").write_text(one_blank)
    sig_2 = target_repo_file_signature(str(repo))

    assert sig_1 == sig_2


def test_missing_trailing_newline_is_a_hit(tmp_path):
    repo = _repo(tmp_path)
    sig_1 = target_repo_file_signature(str(repo))

    (repo / "x.py").write_text(_SOURCE.rstrip("\n"))  # no trailing newline at all
    sig_2 = target_repo_file_signature(str(repo))

    assert sig_1 == sig_2


def test_doubled_trailing_newline_is_a_hit(tmp_path):
    repo = _repo(tmp_path)
    sig_1 = target_repo_file_signature(str(repo))

    (repo / "x.py").write_text(_SOURCE + "\n\n\n")  # several trailing newlines
    sig_2 = target_repo_file_signature(str(repo))

    assert sig_1 == sig_2


def test_internal_token_change_is_a_miss(tmp_path):
    repo = _repo(tmp_path)
    sig_1 = target_repo_file_signature(str(repo))

    (repo / "x.py").write_text(_SOURCE.replace("parse_order", "parse_orders"))
    sig_2 = target_repo_file_signature(str(repo))

    assert sig_1 != sig_2


def test_indentation_change_is_a_miss(tmp_path):
    """Indentation is semantic in Python - never normalized away."""
    reindented = "def parse_order(raw):\n        return raw\n\n\ndef store_order(data):\n    return data\n"
    repo = _repo(tmp_path)
    sig_1 = target_repo_file_signature(str(repo))

    (repo / "x.py").write_text(reindented)
    sig_2 = target_repo_file_signature(str(repo))

    assert sig_1 != sig_2


def test_added_line_of_code_is_a_miss(tmp_path):
    repo = _repo(tmp_path)
    sig_1 = target_repo_file_signature(str(repo))

    (repo / "x.py").write_text(_SOURCE + "\ndef new_function():\n    return 1\n")
    sig_2 = target_repo_file_signature(str(repo))

    assert sig_1 != sig_2


def test_internal_whitespace_within_a_line_is_a_miss(tmp_path):
    """Internal whitespace within a line is never normalized (it can be
    semantic - inside a string literal, for instance)."""
    repo = _repo(tmp_path)
    sig_1 = target_repo_file_signature(str(repo))

    (repo / "x.py").write_text(_SOURCE.replace("def parse_order(raw):", "def  parse_order(raw):"))  # doubled space
    sig_2 = target_repo_file_signature(str(repo))

    assert sig_1 != sig_2
