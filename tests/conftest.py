import os
from pathlib import Path

import pytest

from prism.cli import build_pipeline

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
PYTHON_REPO = os.path.join(FIXTURES_DIR, "python_repo")


@pytest.fixture(scope="session")
def python_repo_root() -> str:
    return PYTHON_REPO


@pytest.fixture(scope="session")
def built_repo(python_repo_root):
    builder, tag_matrix = build_pipeline(python_repo_root)
    return builder, tag_matrix


@pytest.fixture(scope="session", autouse=True)
def _guard_default_pilot_checkpoint_path():
    """fix-test-checkpoint-isolation: `benchmarks.runner.save_checkpoint`
    is the one real write choke-point every `run_evaluation` checkpoint
    save goes through - a test that calls `run_evaluation(...)` without
    its own `checkpoint_path=` (a `tmp_path`-based one) silently falls
    back to `DEFAULT_CHECKPOINT_PATH` ("reports/pilot/checkpoint.json"),
    the real pilot-1 baseline file this repo commits. Session-scoped and
    autouse - active for every test in the whole run, not just whichever
    one happens to be named `test_default_checkpoint_path_is_never_
    written_by_tests` below - so a write attempt fails immediately, at
    the exact test responsible, not silently on disk.

    A real, legitimate write to any *other* path (every test that
    already passes its own `tmp_path`-based `checkpoint_path=`) is
    untouched - only a write whose resolved path matches the real
    default is intercepted.
    """
    import benchmarks.runner as runner_module

    guarded_path = (Path(runner_module.PROJECT_ROOT) / runner_module.DEFAULT_CHECKPOINT_PATH).resolve()
    original_save_checkpoint = runner_module.save_checkpoint

    def _guarded_save_checkpoint(path, checkpoint):
        if Path(path).resolve() == guarded_path:
            current_test = os.environ.get("PYTEST_CURRENT_TEST", "<unknown test>")
            raise AssertionError(
                f"{current_test} attempted to write the real pilot-1 baseline checkpoint "
                f"({guarded_path}) - pass an explicit tmp_path-based checkpoint_path= to "
                "run_evaluation(...) instead."
            )
        return original_save_checkpoint(path, checkpoint)

    mp = pytest.MonkeyPatch()
    mp.setattr(runner_module, "save_checkpoint", _guarded_save_checkpoint)
    yield
    mp.undo()
