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
locality/arity *score* - not by the receiver's actual type. This is
real, receiver-type-blind resolution, not a hypothetical, still true
after the Zero-Debt Hardening Pass's own Task 4 change below.

**Zero-Debt Hardening Pass, Task 4 update**: `_resolve_ambiguous_call`'s
own tie-break, on a genuine score tie among candidates, used to be
"whichever candidate happened to come first in `candidates_for_simple_
name`'s own list" - itself Pass 1's symbol-registration order, never
guaranteed stable (a real, separate bug: it made `prism.semantics.
extractor.compute_feature_masks_cached`'s cold/warm parity
nondeterministic on repos with many same-named symbols, e.g. minified
JS vendor files). Fixed by sorting candidates canonically (by qualified
name) before scoring. This is a genuine, real improvement - deterministic
beats order-dependent - but it is *alphabetical* determinism, not
receiver-type-awareness: G41 itself remains exactly as receiver-type-
blind as before, deferred to v1.2 exactly as before. The two tests below
were updated accordingly, not to claim a fix that didn't happen -
`test_g41_ambiguous_receiver_resolution_is_now_order_independent` now
demonstrates the real, new order-independence with the original class
names (where alphabetical order happens to match this example's own
"receiver name suggests the right type" naming), while
`test_g41_receiver_named_after_its_real_type_still_resolves_correctly`
was moved onto a *different* class-name pair, chosen so alphabetical
tie-breaking does *not* coincidentally satisfy it, so it keeps failing
for the right (real, still-open) reason.
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


def test_g41_ambiguous_receiver_resolution_is_now_order_independent(tmp_path):
    """Same two classes, same call site, only the definition order
    swapped between the two source files - Zero-Debt Hardening Pass
    Task 4's canonical (alphabetical-by-qualified-name) tie-break in
    `_resolve_ambiguous_call` means the resolved target no longer flips
    with source order, a real, verifiable improvement over the old
    "whichever candidate happened to register first" behavior this test
    used to characterize (see this module's own top-level docstring).
    Still not receiver-type-directed - see the sibling xfail test right
    below for a class-name pair where alphabetical order and "the type
    the receiver's own name suggests" disagree, and resolution is still
    wrong."""
    source_a = _G41_TEMPLATE.format(first="NodeList", second="Template")
    source_b = _G41_TEMPLATE.format(first="Template", second="NodeList")

    succ_a = _successors(tmp_path, source_a, "mod.Worker.run", subdir="repo_a")
    succ_b = _successors(tmp_path, source_b, "mod.Worker.run", subdir="repo_b")

    resolved_a = {s for s in succ_a if s.endswith(".render")}
    resolved_b = {s for s in succ_b if s.endswith(".render")}

    # Both call sites are textually identical (`self.nodelist.render(context)`)
    # and "NodeList" < "Template" alphabetically, so the canonical
    # tie-break now picks NodeList.render in both files, regardless of
    # which one was declared first.
    assert resolved_a == {"mod.NodeList.render"}
    assert resolved_b == {"mod.NodeList.render"}
    assert resolved_a == resolved_b


# A second class-name pair, deliberately chosen so the receiver's own
# name (`zetalist`, suggesting `Zeta`) and alphabetical order (`Alpha` <
# `Zeta`) disagree - the canonical tie-break below always picks `Alpha`,
# the *wrong* one by the receiver-name signal, keeping the xfail test
# below a genuine, still-open characterization of G41's real receiver-
# type-blindness rather than a coincidental alphabetical pass.
_G41_TYPE_MISMATCH_TEMPLATE = (
    "class {first}:\n"
    "    def render(self, context):\n"
    "        return 'first'\n"
    "\n\n"
    "class {second}:\n"
    "    def render(self, context):\n"
    "        return 'second'\n"
    "\n\n"
    "class Worker:\n"
    "    def __init__(self, zetalist):\n"
    "        self.zetalist = zetalist\n"
    "\n"
    "    def run(self, context):\n"
    "        return self.zetalist.render(context)\n"
)


@pytest.mark.xfail(
    reason="G41: self.<attr>.<method>() resolution is receiver-type-blind. Deferred to v1.2.",
    strict=True,
)
def test_g41_receiver_named_after_its_real_type_still_resolves_correctly(tmp_path):
    """KNOWN GAP (G41), asserted as the correct/desired behavior: the
    receiver `self.zetalist.render(...)` should resolve to Zeta.render
    (the class the parameter name and usage clearly indicate), not
    Alpha.render. Currently fails - `_resolve_ambiguous_call` has no
    signal from the receiver's own name/assignment, only namespace/
    locality/arity scoring plus (since Task 4) a canonical alphabetical
    tie-break, which favors `Alpha` over `Zeta` regardless of which is
    declared first. Deliberately not the NodeList/Template pair the
    sibling order-independence test above uses - that pair's own
    alphabetical order happens to agree with its receiver-name signal
    (a coincidence Task 4 exposed), which would make this assertion pass
    for the wrong reason and silently stop characterizing the real,
    still-open gap."""
    source = _G41_TYPE_MISMATCH_TEMPLATE.format(first="Alpha", second="Zeta")
    succ = _successors(tmp_path, source, "mod.Worker.run")
    resolved = {s for s in succ if s.endswith(".render")}
    assert resolved == {"mod.Zeta.render"}


# --- Defensive control: genuine single-candidate ambiguity stays unresolved ---

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


# --- Builtin-Receiver Exclusion fix ---------------------------------------
#
# A narrower, fully-decidable slice of G41's general receiver-type-
# blindness (still correctly deferred to v1.2 above): when a local
# variable or `self.<attr>` is assigned from a Python builtin-container
# literal, comprehension, or bare/`collections.`-qualified factory call
# in the *same* scope (`field_names = set()`), its real type is not
# merely untracked - it is definitively known, and definitely not any
# repo-defined class. `_resolve_segments`/`InstanceTypeMap.is_builtin`
# now short-circuit this specific case before it ever reaches
# `_resolve_ambiguous_call`'s scoring, which previously happened to bind
# it to an unrelated same-named method elsewhere in the repo with no
# receiver-type check at all. Confirmed as a real, live bug via a direct
# trace against the real pinned Django 4.2.30 checkout: `Model.save`'s
# own `field_names = set()` / `field_names.difference(deferred_fields)`
# (django/db/models/base.py) resolved to `GeometryCollection.add` /
# `QuerySet.difference` - two unrelated, unrelated-module Django symbols
# - both admitted into the packed context at the seed's own `dist=1.0`
# priority tier.

def test_builtin_set_receiver_add_call_is_not_misresolved(tmp_path):
    source = (
        "class GeometryCollection:\n"
        "    def add(self, x):\n"
        "        return x\n"
        "\n\n"
        "def process(items):\n"
        "    field_names = set()\n"
        "    for item in items:\n"
        "        field_names.add(item)\n"
        "    return field_names\n"
    )
    succ = _successors(tmp_path, source, "mod.process")
    assert "mod.GeometryCollection.add" not in succ
    assert not any(s.endswith(".add") for s in succ)


def test_builtin_set_receiver_difference_call_is_not_misresolved(tmp_path):
    source = (
        "class QuerySet:\n"
        "    def difference(self, other):\n"
        "        return self\n"
        "\n\n"
        "def process(deferred_fields):\n"
        "    field_names = set()\n"
        "    field_names.add('a')\n"
        "    return field_names.difference(deferred_fields)\n"
    )
    succ = _successors(tmp_path, source, "mod.process")
    assert "mod.QuerySet.difference" not in succ
    assert not any(s.endswith(".difference") for s in succ)


def test_builtin_receiver_exclusion_covers_literal_display_and_comprehension(tmp_path):
    """Not just bare factory calls (`set()`) - a dict/list literal display
    and a comprehension must be recognized too, since both are real,
    common ways Python code builds a container that later calls `.get`/
    `.append`/etc. on it."""
    source = (
        "class Registry:\n"
        "    def get(self, key):\n"
        "        return None\n"
        "\n"
        "    def append(self, value):\n"
        "        return None\n"
        "\n\n"
        "def via_dict_literal(keys):\n"
        "    cache = {}\n"
        "    return cache.get('x')\n"
        "\n\n"
        "def via_list_comprehension(items):\n"
        "    bucket = [x for x in items]\n"
        "    bucket.append(1)\n"
        "    return bucket\n"
    )
    dict_succ = _successors(tmp_path, source, "mod.via_dict_literal", subdir="repo_dict")
    list_succ = _successors(tmp_path, source, "mod.via_list_comprehension", subdir="repo_list")
    assert "mod.Registry.get" not in dict_succ
    assert "mod.Registry.append" not in list_succ


def test_builtin_receiver_exclusion_covers_self_attribute(tmp_path):
    """`self.<attr> = set()` inside `__init__`, then `self.<attr>.add(...)`
    elsewhere in the class - the `class_instance_map`/self-prefixed
    resolution path, not just plain local variables."""
    source = (
        "class Sink:\n"
        "    def add(self, x):\n"
        "        return x\n"
        "\n\n"
        "class Collector:\n"
        "    def __init__(self):\n"
        "        self.seen = set()\n"
        "\n"
        "    def record(self, item):\n"
        "        self.seen.add(item)\n"
        "        return self.seen\n"
    )
    succ = _successors(tmp_path, source, "mod.Collector.record")
    assert "mod.Sink.add" not in succ
    assert not any(s.endswith(".add") for s in succ)


def test_builtin_receiver_exclusion_zero_false_positive_on_real_class(tmp_path):
    """The fix must never suppress resolution for a receiver that is
    genuinely untyped (not provably a builtin) - it should still reach
    the existing G41/G44 fallback and resolve normally, exactly as
    before this fix (a real, positive control against over-broad
    pruning)."""
    source = (
        "class Manager:\n"
        "    def add(self, x):\n"
        "        return x\n"
        "\n\n"
        "def process_with_real_manager(m):\n"
        "    m.add(5)\n"
        "    return m\n"
    )
    succ = _successors(tmp_path, source, "mod.process_with_real_manager")
    assert "mod.Manager.add" in succ


def test_builtin_receiver_exclusion_reassignment_to_real_class_wins(tmp_path):
    """A name first bound to a builtin container, then reassigned to a
    real, known class within the same scope, must resolve against the
    *real* class - `InstanceTypeMap.bind`/`bind_builtin` are symmetric,
    most-recent-assignment-wins, matching the existing `ambiguous`
    convention for a plain class-to-class rebind."""
    source = (
        "class Real:\n"
        "    def add(self, x):\n"
        "        return x\n"
        "\n\n"
        "def process(flag):\n"
        "    bucket = set()\n"
        "    bucket = Real()\n"
        "    bucket.add(1)\n"
        "    return bucket\n"
    )
    succ = _successors(tmp_path, source, "mod.process")
    assert "mod.Real.add" in succ
