import math

import pytest

from harness.scoring.agent_diagnostics import digest_safety_loss, recovery_rate, tool_fpr, verification_lift


def test_verification_lift():
    assert math.isnan(verification_lift(False, True))
    assert verification_lift(True, True) == 0.0
    assert verification_lift(True, False) == -1.0


def test_recovery_rate():
    assert math.isnan(recovery_rate(0, 0))
    assert recovery_rate(4, 1) == 0.25
    with pytest.raises(ValueError):
        recovery_rate(1, 2)


def test_arm4_diagnostics_are_explicit_m3_stubs():
    with pytest.raises(NotImplementedError, match="M3"):
        digest_safety_loss([], "", set())
    with pytest.raises(NotImplementedError, match="M3"):
        tool_fpr([])
