"""R3-5 equivalence tests E1-E3 (harness/experiments/r3/equivalence.py) on real repositories.

They need full clones, so they run only when R3_EQ_REPOS lists them (os.pathsep-separated paths) and R3_EQ_SCRATCH
names a scratch directory. Otherwise they are skipped. The harness is frozen only after these pass on a Python and a
TypeScript repository from R3's pool (amendment R3-5).
"""
import os

import pytest

REPOS = [p for p in os.environ.get("R3_EQ_REPOS", "").split(os.pathsep) if p]
SCRATCH = os.environ.get("R3_EQ_SCRATCH")


@pytest.mark.skipif(not REPOS or not SCRATCH, reason="set R3_EQ_REPOS and R3_EQ_SCRATCH to run on real clones")
@pytest.mark.parametrize("clone", REPOS)
def test_equivalence_e1_e3(clone):
    from harness.experiments.r3.equivalence import run
    res = run(os.path.abspath(clone), os.path.abspath(SCRATCH))
    assert res["E1"]["pass"], res["E1"]
    assert res["E2"]["pass"], res["E2"]
    assert res["E3"]["pass"], res["E3"]
