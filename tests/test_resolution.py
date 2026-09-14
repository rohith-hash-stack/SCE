"""Test-practice: graph resolution tests, focused on two documented but
previously unregression-tested call-resolution edge cases
(`TASK_AUTHORING.md` item 2, sub-points 3-4; `docs/design_formalism.md`
G40/G41) plus a defensive control for genuine, irreducible ambiguity.

G40: `self.<method>()` where `<method>` is declared on a base class, not
overridden locally - `_resolve_segments` (concrete_builder.py:~1774-1786)
walks the MRO ancestor chain for this exact case, so it's expected to
already work; tested here as a positive control, not a known gap.

G41: `self.<attr>.<method>()` where `<attr>`'s type can't be tracked by
the deterministic instance map (e.g. a constructor parameter assigned
through with no `SomeClass(...)` call site, `self.x = x`) falls through
to `_resolve_ambiguous_call` (concrete_builder.py:~1622), which picks
among every same-simple-name candidate in the repo by a namespace/
locality/arity *score* - not by the receiver's actual type. Demonstrated
here with a minimal, deterministic repro: two same-named methods on
unrelated classes, only their definition order in the file swapped -
resolution flips with it. This is real, receiver-type-blind resolution,
not a hypothetical.
"""
from __future__ import annotations

import pytest

from prism.cli import build_pipeline


def _successors(tmp_path, source: str, qualified_name: str, subdir: str = "repo"):
    repo = tmp_path / subdir
    repo.mkdir()
    (repo / "mod.py").write_text(source)
    builder, _ = build_pipeline(str(repo))
    return set(builder.graph.successors(qualified_name))


# --- G40: inherited (not overridden) self.method() call - positive control ---

def test_g40_self_method_call_resolves_via_mro_when_not_overridden_locally(tmp_path):
    source = (
        "class Base:\n"
        "    def validate(self):\n"
        "        return True\n"
        "\n\n"
        "class Sub(Base):\n"
        "    def run(self):\n"
        "        return self.validate()\n"
    )
    succ = _successors(tmp_path, source, "mod.Sub.run")
    assert "mod.Base.validate" in succ


def test_g40_self_method_call_resolves_directly_when_overridden_locally(tmp_path):
    source = (
        "class Base:\n"
        "    def validate(self):\n"
        "        return True\n"
        "\n\n"
        "class Sub(Base):\n"
        "    def validate(self):\n"
        "        return False\n"
        "\n"
        "    def run(self):\n"
        "        return self.validate()\n"
    )
    succ = _successors(tmp_path, source, "mod.Sub.run")
    assert "mod.Sub.validate" in succ
    assert "mod.Base.validate" not in succ


# --- G41: receiver-type-blind polysemy resolution ---

_G41_TEMPLATE = (
    "class {first}:\n"
    "    def render(self, context):\n"
    "        return 'first'\n"
    "\n\n"
    "class {second}:\n"
    "    def render(self, context):\n"
    "        return 'second'\n"
    "\n\n"
    "class Worker:\n"
    "    def __init__(self, nodelist):\n"
    "        self.nodelist = nodelist\n"
    "\n"
    "    def run(self, context):\n"
    "        return self.nodelist.render(context)\n"
)


def test_g41_ambiguous_receiver_resolution_is_order_dependent(tmp_path):
    """Same two classes, same call site, only the definition order
    swapped between the two source files - the resolved target flips.
    A real receiver-type-directed resolver would pick the same answer
    (whichever is "correct") regardless of source order; this proves
    the current fallback is order-sensitive, i.e. not receiver-type-
    directed at all."""
    source_a = _G41_TEMPLATE.format(first="NodeList", second="Template")
    source_b = _G41_TEMPLATE.format(first="Template", second="NodeList")

    succ_a = _successors(tmp_path, source_a, "mod.Worker.run", subdir="repo_a")
    succ_b = _successors(tmp_path, source_b, "mod.Worker.run", subdir="repo_b")

    resolved_a = {s for s in succ_a if s.endswith(".render")}
    resolved_b = {s for s in succ_b if s.endswith(".render")}

    assert resolved_a == {"mod.NodeList.render"}
    assert resolved_b == {"mod.Template.render"}
    # Both call sites are textually identical (`self.nodelist.render(context)`)
    # and `nodelist` is unambiguously the parameter name in both files - a
    # type-directed resolver would resolve both to NodeList.render.
    assert resolved_a != resolved_b


@pytest.mark.xfail(
    reason="G41: self.<attr>.<method>() resolution is receiver-type-blind. Deferred to v1.2.",
    strict=True,
)
def test_g41_receiver_named_after_its_real_type_still_resolves_correctly(tmp_path):
    """KNOWN GAP (G41), asserted as the correct/desired behavior: when
    Template is declared *before* NodeList in the file, the receiver
    `self.nodelist.render(...)` should still resolve to NodeList.render
    (the class the parameter name and usage clearly indicate), not
    Template.render. Currently fails - `_resolve_ambiguous_call` has no
    signal from the receiver's own name/assignment, only namespace/
    locality/arity scoring, which favors whichever candidate sorts
    first. This is the same failure mode as the real
    `self.nodelist.render(context)` -> `Template.render` misresolution
    documented in TASK_AUTHORING.md/G41."""
    source = _G41_TEMPLATE.format(first="Template", second="NodeList")
    succ = _successors(tmp_path, source, "mod.Worker.run")
    resolved = {s for s in succ if s.endswith(".render")}
    assert resolved == {"mod.NodeList.render"}


# --- Defensive control: genuine single-candidate ambiguity stays unresolved ---

@pytest.mark.xfail(
    reason="G44: unique-candidate calls silently unlinked when receiver type is untrackable. Deferred to v1.2.",
    strict=True,
)
def test_single_candidate_for_a_name_does_not_get_silently_dropped_or_guessed(tmp_path):
    """Only one `finalize` method exists repo-wide, but its receiver type
    still can't be tracked (plain-parameter passthrough) - `_resolve_
    ambiguous_call` requires >= 2 candidates to run its scoring at all,
    so this must resolve directly rather than getting stuck unresolved
    for want of "genuine polysemy" to disambiguate."""
    source = (
        "class Report:\n"
        "    def finalize(self):\n"
        "        return True\n"
        "\n\n"
        "class Worker:\n"
        "    def __init__(self, report):\n"
        "        self.report = report\n"
        "\n"
        "    def run(self):\n"
        "        return self.report.finalize()\n"
    )
    succ = _successors(tmp_path, source, "mod.Worker.run")
    assert "mod.Report.finalize" in succ
