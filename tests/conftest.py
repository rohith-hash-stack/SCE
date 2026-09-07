import os

import pytest

from sce.cli import build_pipeline

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
PYTHON_REPO = os.path.join(FIXTURES_DIR, "python_repo")


@pytest.fixture(scope="session")
def python_repo_root() -> str:
    return PYTHON_REPO


@pytest.fixture(scope="session")
def built_repo(python_repo_root):
    builder, tag_matrix = build_pipeline(python_repo_root)
    return builder, tag_matrix
