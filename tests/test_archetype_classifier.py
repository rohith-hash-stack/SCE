"""Layer 5 (`prism.graph.repository_profile`) end-to-end fixture tests -
the three archetypes the spec names explicitly: a small E2E test-harness
repo, a CLI automation tool, and a pure algorithmic data library. Each
fixture is a small, self-contained, hermetic repo (no network, no real
clone), built through the exact same `build_pipeline` -> `compute_contracts`
-> `compute_hierarchical_profile` path `prism query`/the MCP server use.
"""
from __future__ import annotations

import pytest

from prism.cli import build_pipeline
from prism.graph.contracts import compute_contracts
from prism.graph.hierarchy import compute_hierarchical_profile
from prism.graph.repository_profile import (
    ALGORITHMIC_DATA_LIBRARY,
    CLI_AUTOMATION_TOOL,
    E2E_VERIFICATION_HARNESS,
)


def _profile(repo_path: str):
    builder, _tag_matrix = build_pipeline(repo_path)
    contracts = compute_contracts(builder)
    return compute_hierarchical_profile(builder, contracts, repo_path)


@pytest.fixture
def e2e_harness_repo(tmp_path):
    repo = tmp_path / "e2e_repo"
    repo.mkdir()
    (repo / "test_checkout_flow.py").write_text(
        "import pytest\n"
        "\n"
        "def test_checkout_succeeds():\n"
        "    check_response()\n"
        "\n"
        "def test_checkout_rejects_bad_card():\n"
        "    check_response()\n"
        "\n"
        "def test_checkout_handles_timeout():\n"
        "    check_response()\n"
        "\n"
        "def check_response():\n"
        "    pytest.raises(ValueError)\n"
    )
    return str(repo)


@pytest.fixture
def cli_tool_repo(tmp_path):
    repo = tmp_path / "cli_repo"
    repo.mkdir()
    (repo / "cli.py").write_text(
        "import sys\n"
        "import logging\n"
        "\n"
        "def main():\n"
        "    logging.info(\"starting\")\n"
        "    sys.exit(run())\n"
        "\n"
        "def run():\n"
        "    logging.info(\"running\")\n"
        "    return 0\n"
    )
    return str(repo)


@pytest.fixture
def algorithmic_library_repo(tmp_path):
    repo = tmp_path / "algo_repo"
    repo.mkdir()
    (repo / "mathutils.py").write_text(
        "import math\n"
        "import json\n"
        "import functools\n"
        "\n"
        "def compute(a, b):\n"
        "    return math.sqrt(a) + math.floor(b)\n"
        "\n"
        "def serialize(data):\n"
        "    return json.dumps(data)\n"
        "\n"
        "def reduce_values(values):\n"
        "    return functools.reduce(lambda x, y: x + y, values)\n"
    )
    return str(repo)


def test_e2e_harness_extracts_e2e_verification_harness(e2e_harness_repo) -> None:
    profile = _profile(e2e_harness_repo)
    repo = profile.repository
    assert repo.global_sink_mass["ASSERT_SIGNAL"] > 0.6
    assert repo.archetype == E2E_VERIFICATION_HARNESS


def test_cli_tool_extracts_cli_automation_tool(cli_tool_repo) -> None:
    profile = _profile(cli_tool_repo)
    repo = profile.repository
    assert repo.archetype == CLI_AUTOMATION_TOOL
    assert repo.max_critical_path_depth < 3


def test_algorithmic_library_extracts_algorithmic_data_library(algorithmic_library_repo) -> None:
    profile = _profile(algorithmic_library_repo)
    repo = profile.repository
    assert repo.global_sink_mass["PURE_LEAF"] > 0.80
    assert repo.archetype == ALGORITHMIC_DATA_LIBRARY
