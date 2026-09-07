"""Tests for the clone-and-index harness. The default suite is fully
hermetic - it clones from a local throwaway git repo, never the network -
so it stays fast and deterministic in CI. A real-GitHub smoke test is
included but skipped unless `PRISM_LIVE_NETWORK_TESTS=1` is set, since it
depends on outbound network access this sandbox may not always have.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from benchmarks.clone_eval import (
    CloneError,
    TargetSelectionError,
    auto_select_target,
    clone_repo,
    derive_repo_name,
)
from prism.cli import build_pipeline

PYTHON_FIXTURE = "tests/fixtures/python_repo"
STRESS_FIXTURE = "benchmarks/fixtures/stress_repo"
TASK_FIXTURE = "benchmarks/fixtures/task_repo"


# --------------------------------------------------------------------- #
# derive_repo_name
# --------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/encode/starlette.git", "starlette"),
        ("https://github.com/pallets/flask", "flask"),
        ("git@github.com:tiangolo/fastapi.git", "fastapi"),
        ("/local/path/to/some-repo", "some-repo"),
        ("/local/path/to/some-repo/", "some-repo"),
    ],
)
def test_derive_repo_name(url, expected):
    assert derive_repo_name(url) == expected


# --------------------------------------------------------------------- #
# clone_repo, against a local throwaway git repo (hermetic)
# --------------------------------------------------------------------- #
@pytest.fixture
def local_git_repo(tmp_path):
    """A minimal, real git repo (via `git init` + commit) so `clone_repo`
    exercises the actual `git clone --depth 1` code path without touching
    the network.
    """
    src = tmp_path / "source_repo"
    src.mkdir()
    (src / "greeter.py").write_text("def greet(name):\n    return f'hello {name}'\n")
    subprocess.run(["git", "init", "-q"], cwd=src, check=True)
    subprocess.run(["git", "add", "-A"], cwd=src, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-q", "-m", "initial"],
        cwd=src,
        check=True,
    )
    return src


def test_clone_repo_clones_a_local_repo(tmp_path, local_git_repo):
    cache_dir = tmp_path / "cache"
    dest = clone_repo(str(local_git_repo), cache_dir=cache_dir)
    assert dest == cache_dir / "source_repo"
    assert (dest / "greeter.py").exists()
    assert (dest / ".git").exists()


def test_clone_repo_reuses_cache_by_default(tmp_path, local_git_repo, capsys):
    cache_dir = tmp_path / "cache"
    first = clone_repo(str(local_git_repo), cache_dir=cache_dir)
    marker = first / "marker.txt"
    marker.write_text("still here")

    second = clone_repo(str(local_git_repo), cache_dir=cache_dir)
    assert second == first
    assert marker.exists()  # not re-cloned, so our marker survived
    assert "Using cached clone" in capsys.readouterr().err


def test_clone_repo_force_reclones(tmp_path, local_git_repo):
    cache_dir = tmp_path / "cache"
    first = clone_repo(str(local_git_repo), cache_dir=cache_dir)
    marker = first / "marker.txt"
    marker.write_text("should be wiped")

    second = clone_repo(str(local_git_repo), cache_dir=cache_dir, force=True)
    assert second == first
    assert not marker.exists()


def test_clone_repo_raises_clean_error_for_bad_source(tmp_path):
    cache_dir = tmp_path / "cache"
    with pytest.raises(CloneError):
        clone_repo(str(tmp_path / "does_not_exist"), cache_dir=cache_dir)


def test_clone_repo_raises_clean_error_when_git_missing(tmp_path, local_git_repo, monkeypatch):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(CloneError, match="git is not installed"):
        clone_repo(str(local_git_repo), cache_dir=tmp_path / "cache")


# --------------------------------------------------------------------- #
# auto_select_target
# --------------------------------------------------------------------- #
def test_auto_select_target_prefers_route_handler():
    builder, tag_matrix = build_pipeline(STRESS_FIXTURE)
    target = auto_select_target(builder, tag_matrix)
    assert "#route_handler" in tag_matrix.get(target, set())


def test_auto_select_target_falls_back_without_route_handler():
    """task_repo has no #route_handler-tagged symbol at all - auto-select
    must still return *some* real function/method rather than failing."""
    builder, tag_matrix = build_pipeline(TASK_FIXTURE)
    assert not any("#route_handler" in tags for tags in tag_matrix.values())
    target = auto_select_target(builder, tag_matrix)
    symbol = builder.symbol_table.get(target)
    assert symbol is not None
    assert symbol.kind in ("function", "method")


def test_auto_select_target_raises_on_empty_graph(tmp_path):
    empty_repo = tmp_path / "empty_repo"
    empty_repo.mkdir()
    (empty_repo / "constants.py").write_text("X = 1\nY = 2\n")
    builder, tag_matrix = build_pipeline(str(empty_repo))
    with pytest.raises(TargetSelectionError):
        auto_select_target(builder, tag_matrix)


# --------------------------------------------------------------------- #
# End-to-end CLI, still hermetic (a real local git repo, no --live)
# --------------------------------------------------------------------- #
@pytest.fixture
def python_fixture_as_git_repo(tmp_path):
    """`git clone` needs an actual git repository as its source - the
    tracked fixture directories under `tests/fixtures/` aren't standalone
    repos themselves (they're just files tracked inside this project's own
    repo), so build a throwaway one with the same content.
    """
    src = tmp_path / "python_repo_source"
    shutil.copytree(PYTHON_FIXTURE, src)
    subprocess.run(["git", "init", "-q"], cwd=src, check=True)
    subprocess.run(["git", "add", "-A"], cwd=src, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-q", "-m", "initial"],
        cwd=src,
        check=True,
    )
    return src


def test_clone_eval_cli_end_to_end_on_local_repo(tmp_path, python_fixture_as_git_repo, capsys):
    from benchmarks.clone_eval import main

    cache_dir = tmp_path / "cache"
    exit_code = main(
        [
            "--repo", str(python_fixture_as_git_repo),
            "--target", "src.controllers.checkout.CheckoutController.process_checkout",
            "--budget", "2000",
            "--cache-dir", str(cache_dir),
        ]
    )
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Indexed successfully" in output
    assert "did not crash" in output
    assert "Syntactic validity" in output


def test_clone_eval_cli_unknown_target_exits_nonzero(tmp_path, python_fixture_as_git_repo):
    from benchmarks.clone_eval import main

    cache_dir = tmp_path / "cache"
    exit_code = main(
        ["--repo", str(python_fixture_as_git_repo), "--target", "does.not.exist", "--cache-dir", str(cache_dir)]
    )
    assert exit_code == 1


# --------------------------------------------------------------------- #
# Real-network smoke test (opt-in only)
# --------------------------------------------------------------------- #
@pytest.mark.skipif(
    os.environ.get("PRISM_LIVE_NETWORK_TESTS") != "1",
    reason="set PRISM_LIVE_NETWORK_TESTS=1 to clone a real GitHub repo over the network",
)
def test_clone_real_github_repo_and_index_without_crashing(tmp_path):
    cache_dir = tmp_path / "cache"
    dest = clone_repo("https://github.com/encode/starlette.git", cache_dir=cache_dir)
    builder, tag_matrix = build_pipeline(str(dest))
    assert len(builder.symbol_table) > 100
    target = auto_select_target(builder, tag_matrix)
    assert builder.symbol_table.get(target) is not None
    shutil.rmtree(cache_dir, ignore_errors=True)
