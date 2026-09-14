"""Test-practice: end-to-end envelope determinism, through the real
`build_pipeline` -> `build_context_package` -> `render` chain, not just
`render()` given an already-constructed `ContextPackage` (`surface/
test_renderer_properties.py`'s `test_determinism_render_twice_is_
byte_identical` already covers that, from hypothesis-generated
packages). The gap this file closes: is the *whole pipeline*
deterministic when re-run from scratch - two independent
`build_pipeline` calls on identical source, simulating two separate
processes/pilot runs - not just re-rendering one already-built object
twice."""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.surface.build import build_context_package
from prism.surface.renderer import render

_SOURCE = (
    "def leaf_a():\n    return 1\n\n\n"
    "def leaf_b():\n    return 2\n\n\n"
    "def mid(x):\n    return leaf_a() + leaf_b() + x\n\n\n"
    "class Worker:\n"
    "    def __init__(self, x):\n        self.x = x\n\n"
    "    def run(self):\n        return mid(self.x)\n"
)


def _build_and_render(tmp_path, subdir):
    repo = tmp_path / subdir
    repo.mkdir()
    (repo / "svc.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))
    pkg = build_context_package(builder, "svc.Worker.run", str(repo), 2000)
    return render(pkg)


def test_full_pipeline_is_byte_identical_across_two_independent_builds(tmp_path):
    """Same source, two separate `build_pipeline` calls (different repo
    directories, simulating two separate processes) - the rendered
    envelope must be byte-identical, run_id/generated_at aside (both
    omitted by RenderOptions' own defaults)."""
    xml_a = _build_and_render(tmp_path, "repo_a")
    xml_b = _build_and_render(tmp_path, "repo_b")
    assert xml_a == xml_b


def test_full_pipeline_is_deterministic_across_repeated_builds_on_the_same_builder(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))

    xml_first = render(build_context_package(builder, "svc.Worker.run", str(repo), 2000))
    xml_second = render(build_context_package(builder, "svc.Worker.run", str(repo), 2000))
    assert xml_first == xml_second


def test_full_pipeline_determinism_survives_a_fresh_index_cache(tmp_path):
    """The on-disk sqlite index cache (`prism.cache.sqlite_cache`) is
    warm after the first build_pipeline call in a given repo dir. A
    second, independent repo (different content-hash key, so its own
    fresh cache entry) must still render identically to the first -
    determinism must not depend on whether the file-content cache was
    warm or cold for a given repo."""
    repo_a = tmp_path / "cache_repo_a"
    repo_a.mkdir()
    (repo_a / "svc.py").write_text(_SOURCE)
    builder_a, _ = build_pipeline(str(repo_a))
    xml_cold = render(build_context_package(builder_a, "svc.Worker.run", str(repo_a), 2000))

    # Re-index the *same* repo - now a cache hit, not a cold rebuild.
    builder_a_warm, _ = build_pipeline(str(repo_a))
    xml_warm = render(build_context_package(builder_a_warm, "svc.Worker.run", str(repo_a), 2000))

    assert xml_cold == xml_warm


def test_full_pipeline_is_deterministic_across_several_seeds_in_one_build(tmp_path):
    """Not a one-seed accident: check determinism (build twice, compare)
    for every function/method in the small repo, not just the one most
    obviously exercised seed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "svc.py").write_text(_SOURCE)
    builder, _ = build_pipeline(str(repo))

    for seed in ("svc.leaf_a", "svc.mid", "svc.Worker.run", "svc.Worker.__init__"):
        first = render(build_context_package(builder, seed, str(repo), 2000))
        second = render(build_context_package(builder, seed, str(repo), 2000))
        assert first == second, seed
