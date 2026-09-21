"""Phase B, Step 2: verifies `_is_role_mismatched_verification`
(`prism.packer.submodular_knapsack`) - the unconditional, role-based
admissibility gate that replaced the hardcoded `_NEVER_PIPELINE_MODULE_
PREFIXES` module-path blacklist - actually excludes a VERIFICATION-role
candidate from `pack_symbol_context`'s real selection, at every real
admission path.

**A real gap found by running this exact scenario, not by static
reading**: the first version of this fix patched the three call sites
`_is_zero_novelty_test_fixture` used to occupy (the cascade loop, the
main greedy loop, and Phase E's mandatory-downstream-successor
protection), but missed a fourth, independent admission path -
"Mandatory upstream protection" - which force-admits the single
strongest upstream caller *unconditionally*, with no gate of any kind.
A synthetic test function that is its seed's *only* upstream caller
(exactly the django_t02_017 shape: a test method calling straight into
the function under test) is the single most common real case that path
exists for, and it slipped straight through the first patch. Caught by
actually running `pack_symbol_context` against a synthetic repo before
trusting the fix, not by re-reading the diff - the same "measure, don't
guess" discipline this codebase's own design history keeps re-learning.
"""
from __future__ import annotations

from prism.cli import build_pipeline
from prism.packer.submodular_knapsack import pack_symbol_context

_SOURCE = (
    "def seed():\n"
    "    return helper()\n\n\n"
    "def helper():\n"
    "    return 1\n\n\n"
    "def test_seed_returns_one():\n"
    "    assert seed() == 1\n"
)


def _build(tmp_path, subdir="repo"):
    repo = tmp_path / subdir
    repo.mkdir()
    (repo / "mod.py").write_text(_SOURCE)
    builder, _tags = build_pipeline(str(repo))
    return builder


def test_verification_upstream_caller_is_excluded_at_every_budget(tmp_path):
    """`test_seed_returns_one` is `seed`'s *only* upstream caller - the
    exact shape "Mandatory upstream protection" force-admits
    unconditionally without the gate. Real pipeline symbol (`helper`)
    stays selected throughout; the test-shaped caller never is, at any
    of these budgets (deliberately swept, not just the one that happened
    to expose the bug during development).
    """
    builder = _build(tmp_path)
    for budget in (500, 1000, 2000, 4000):
        result = pack_symbol_context(builder, "mod.seed", budget)
        assert "mod.helper" in result.selected, f"budget={budget}: real pipeline symbol missing"
        assert "mod.test_seed_returns_one" not in result.selected, (
            f"budget={budget}: VERIFICATION-role caller was admitted - "
            f"selected={sorted(result.selected)}"
        )


def test_gate_is_unconditional_on_novelty_not_gated_behind_delta_feat_zero(tmp_path):
    """The specific gap the old, novelty-gated module-prefix check had:
    a VERIFICATION candidate whose own call site (`seed()` inside an
    `assert`) gives it real, nonzero feature-mask novelty must still be
    excluded - unlike `_is_zero_novelty_test_fixture`, which only ever
    fired at `delta_feat == 0`. This is the same scenario as the test
    above; asserted separately, and explicitly, as the regression this
    whole step exists to close.
    """
    builder = _build(tmp_path)
    result = pack_symbol_context(builder, "mod.seed", 4000)
    assert "mod.test_seed_returns_one" not in result.selected


def test_seed_itself_being_verification_role_does_not_exclude_its_own_callees(tmp_path):
    """The gate only ever fires for an IMPLEMENTATION-role seed - if the
    seed *itself* is VERIFICATION (a debugging/blast-radius query that
    starts from a test), its own real callees must not be excluded from
    their own seed's package; `_is_role_mismatched_verification` returns
    `False` unconditionally whenever `seed_role != IMPLEMENTATION`.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "def helper():\n"
        "    return 1\n\n\n"
        "def test_helper_behavior():\n"
        "    assert helper() == 1\n"
    )
    builder, _tags = build_pipeline(str(repo))
    result = pack_symbol_context(builder, "mod.test_helper_behavior", 2000)
    assert "mod.helper" in result.selected


def test_ordinary_implementation_callee_is_unaffected(tmp_path):
    """Baseline control: an ordinary, non-test callee of an
    IMPLEMENTATION seed is never touched by this gate - proves the fix
    is a targeted exclusion, not a general regression in selection.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        "def seed():\n"
        "    return helper()\n\n\n"
        "def helper():\n"
        "    return 1\n"
    )
    builder, _tags = build_pipeline(str(repo))
    result = pack_symbol_context(builder, "mod.seed", 2000)
    assert "mod.helper" in result.selected
