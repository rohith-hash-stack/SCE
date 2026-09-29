"""`CorpusSpec.subdir` (`benchmarks/corpora/resolver.py`,
`docs/architecture_boundaries.md` Category 10): a monorepo corpus (tRPC)
must be indexed at one real package's own root, not the bare clone root -
otherwise every ground-truth qualified name authored against it (e.g.
`core.router.createRouterFactory`) would never match what `build_pipeline`
actually produces (`packages.server.src.core.router.createRouterFactory`
instead). These tests use a real local git repo (no network) to verify
`resolve()`'s own subdir-joining and fail-loud behavior, and a single-
package corpus is completely unaffected.
"""
from __future__ import annotations

import subprocess

import pytest

from benchmarks.corpora.resolver import CorpusResolutionError, CorpusSpec, resolve_corpus


def _init_local_repo(repo_dir) -> str:
    repo_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    (repo_dir / "packages").mkdir()
    (repo_dir / "packages" / "server").mkdir()
    (repo_dir / "packages" / "server" / "index.ts").write_text("export function f() { return 1; }\n")
    (repo_dir / "top_level.txt").write_text("monorepo root file\n")
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo_dir, check=True)
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def test_resolve_joins_the_declared_subdir_onto_the_real_clone_root(tmp_path):
    source_repo = tmp_path / "source"
    sha = _init_local_repo(source_repo)

    spec = CorpusSpec(name="mono", url=str(source_repo), pinned_commit=sha, subdir="packages/server")
    dest = resolve_corpus(spec, cache_dir=tmp_path / "cache")
    assert dest.name == "mono"
    assert (dest / "packages" / "server" / "index.ts").is_file()

    from benchmarks.corpora import resolver

    resolver.CORPORA["mono"] = spec
    try:
        resolved = resolver.resolve("mono", cache_dir=tmp_path / "cache2")
    finally:
        del resolver.CORPORA["mono"]

    assert resolved.name == "server"
    assert (resolved / "index.ts").is_file()


def test_resolve_raises_when_the_declared_subdir_does_not_exist(tmp_path):
    source_repo = tmp_path / "source"
    sha = _init_local_repo(source_repo)

    spec = CorpusSpec(name="mono2", url=str(source_repo), pinned_commit=sha, subdir="packages/nonexistent")

    from benchmarks.corpora import resolver

    resolver.CORPORA["mono2"] = spec
    try:
        with pytest.raises(CorpusResolutionError, match="does not exist"):
            resolver.resolve("mono2", cache_dir=tmp_path / "cache3")
    finally:
        del resolver.CORPORA["mono2"]


def test_a_corpus_with_no_subdir_resolves_to_the_bare_clone_root_unchanged(tmp_path):
    source_repo = tmp_path / "source"
    sha = _init_local_repo(source_repo)

    spec = CorpusSpec(name="single", url=str(source_repo), pinned_commit=sha)
    assert spec.subdir is None

    from benchmarks.corpora import resolver

    resolver.CORPORA["single"] = spec
    try:
        resolved = resolver.resolve("single", cache_dir=tmp_path / "cache4")
    finally:
        del resolver.CORPORA["single"]

    assert resolved.name == "single"
    assert (resolved / "top_level.txt").is_file()
    assert (resolved / "packages" / "server" / "index.ts").is_file()
