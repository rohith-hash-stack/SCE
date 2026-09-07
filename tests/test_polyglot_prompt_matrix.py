"""Hermetic tests for `benchmarks/polyglot_prompt_matrix.py`. The pure/
static pieces (scenario definitions, CLI argument validation) run here with
no cloning, no network - the same split `tests/test_multi_repo_eval.py`
already uses. Actually running the Java (spring-petclinic) / C# (eShopOnWeb)
suite against real clones is gated behind SCE_LIVE_NETWORK_TESTS=1 below.
"""
from __future__ import annotations

import os

import pytest

from benchmarks.polyglot_prompt_matrix import REPOS, SCENARIOS, main, run_repo


def test_scenarios_defined_for_every_repo():
    assert set(SCENARIOS) == set(REPOS)
    for repo_key, scenarios in SCENARIOS.items():
        assert len(scenarios) >= 2, f"{repo_key} needs at least 2 scenarios"
        assert len({s.scenario_id for s in scenarios}) == len(scenarios), f"{repo_key} scenario ids must be unique"
        for s in scenarios:
            assert s.query_type in ("A", "B", "C")


def test_repos_have_a_language_id_matching_a_universal_slicer_target():
    from sce.slicer.universal_slicer import UniversalSlicer

    for spec in REPOS.values():
        assert spec.language_id in UniversalSlicer.SUPPORTED_LANGUAGES


def test_main_requires_suite_or_repo():
    with pytest.raises(SystemExit):
        main([])


def test_main_rejects_unknown_repo():
    with pytest.raises(SystemExit):
        main(["--repo", "not-a-real-repo"])


# --------------------------------------------------------------------- #
# Real-network smoke test (opt-in only)
# --------------------------------------------------------------------- #
@pytest.mark.skipif(
    os.environ.get("SCE_LIVE_NETWORK_TESTS") != "1",
    reason="set SCE_LIVE_NETWORK_TESTS=1 to clone and evaluate real repositories over the network",
)
@pytest.mark.parametrize("repo_key", sorted(REPOS))
def test_run_repo_against_real_clone(repo_key, tmp_path):
    result = run_repo(repo_key, [2000, 4000], tmp_path / "cache", force_clone=False, k_hops=3)
    assert result.graph_metrics.total_symbols > 50
    for scenario in result.scenarios:
        assert scenario.passed, (scenario.scenario_id, scenario.budget, scenario.checks)
