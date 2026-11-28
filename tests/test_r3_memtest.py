"""The memory test's checks under amendment R3-7."""
from harness.experiments.r3.dryrun import pad_calls
from harness.experiments.r3.memtest import _slope, plateaued


def test_plateau_flat_and_falling_pass():
    assert plateaued([25, 300, 580, 585, 585, 585])
    assert plateaued([25, 572, 572, 313])          # released as the child exits


def test_plateau_still_climbing_fails():
    assert not plateaued([25, 100, 200, 300, 400])


def test_slope():
    assert _slope([100.0] * 10) == 0.0
    assert abs(_slope([100 + 2 * i for i in range(10)]) - 2.0) < 1e-9
    assert _slope([1.0]) is None


def test_pad_calls_are_read_only_and_version_specific():
    t = {"anchor": "pkg.mod.Klass.meth", "anchor_file": "src/pkg/mod.py"}
    p, b = pad_calls(t, "PRISM", 8), pad_calls(t, "BASELINE", 8)
    assert {c["tool"] for c in p} == {"read_file", "grep", "list_dir", "prism_change_context"}
    assert {c["tool"] for c in b} == {"read_file", "grep", "list_dir", "related_code"}
    assert len(p) == 8 and pad_calls(t, "PRISM", 0) == []
