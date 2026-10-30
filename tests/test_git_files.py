"""Inside a git work tree, PRISM indexes only what git considers part of it (`prism.git_files`)."""
from __future__ import annotations

import os
import subprocess

import pytest

from prism.cli import build_pipeline, discover_files

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env={**os.environ, **GIT_ENV})


def _rel(root, files):
    return sorted(os.path.relpath(f, root) for f in files)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "a.py").write_text("def kept():\n    return 1\n")
    (root / ".gitignore").write_text("generated/\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "one")
    return root


def test_ignored_and_embedded_repo_files_are_left_out(repo):
    (repo / "generated").mkdir()
    (repo / "generated" / "out.py").write_text("def built():\n    pass\n")
    vendored = repo / "vendor" / "lib"  # an initialised submodule's checkout: its own repository
    vendored.mkdir(parents=True)
    _git(vendored, "init", "-q")
    (vendored / "index.js").write_text("function vendored() {}\n")
    (repo / "new.py").write_text("def uncommitted():\n    pass\n")  # new and not ignored: indexed
    assert _rel(repo, discover_files(str(repo))) == ["a.py", "new.py"]


def test_disabled_or_outside_git_nothing_is_filtered(repo, tmp_path, monkeypatch):
    (repo / "generated").mkdir()
    (repo / "generated" / "out.py").write_text("x = 1\n")
    monkeypatch.setenv("PRISM_GIT_DISCOVERY", "0")
    assert _rel(repo, discover_files(str(repo))) == ["a.py", "generated/out.py"]
    monkeypatch.delenv("PRISM_GIT_DISCOVERY")
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "b.py").write_text("x = 1\n")
    assert _rel(plain, discover_files(str(plain))) == ["b.py"]


def test_cached_index_after_checkout_matches_a_fresh_one(repo, tmp_path):
    """The aiohttp case: index at one revision, check out another in place, index again with the cache."""
    (repo / "b.py").write_text("def removed_later():\n    return 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "two")
    newer = {s.qualified_name for s in build_pipeline(str(repo), use_cache=True)[0].symbol_table}
    _git(repo, "checkout", "-q", "HEAD~1")
    (repo / "generated").mkdir()
    (repo / "generated" / "stale.py").write_text("def phantom():\n    pass\n")
    cached = {s.qualified_name for s in build_pipeline(str(repo), use_cache=True)[0].symbol_table}
    fresh_root = tmp_path / "fresh"
    _git(repo, "worktree", "add", "-q", "--detach", str(fresh_root), "HEAD")
    fresh = {s.qualified_name.replace("fresh", "repo") for s in build_pipeline(str(fresh_root), use_cache=False)[0].symbol_table}
    assert any("removed_later" in q for q in newer)
    assert not any("removed_later" in q or "phantom" in q for q in cached)
    assert cached == fresh
