"""Plumbing of the Turn-1 atomic-capability micro-ablation
(`harness/experiments/layer4/atomic_caps_ablation.py`)."""
from __future__ import annotations

from benchmarks.final_sweep import config as C
from benchmarks.final_sweep.context_ops import (
    TURN1_CAPS_GUIDANCE,
    annotate_manifest_atomic_capabilities,
    annotate_manifest_axes,
    caps_field,
)
from benchmarks.final_sweep.runner import turn1_system_prompt
from prism.semantics.bitmask import FeatureBit
from prism.semantics.capabilities import CapabilityProfile

MANIFEST = "\n".join([
    "<candidate_index>",
    "pkg.seed|seed|function|def seed():|calls=[pkg.store]",
    "pkg.store|callee|function|def store():|calls=[]",
    "pkg.view|caller|function|def view():|calls=[pkg.seed]",
    "</candidate_index>",
])


def test_caps_field_names_each_primitive_at_its_first_hop():
    profile = CapabilityProfile(by_hop=(int(FeatureBit.PRIM_DB_WRITE), int(FeatureBit.PRIM_DB_WRITE),
                                        int(FeatureBit.PRIM_NET_CONNECT)))
    assert caps_field(profile) == "caps=[network.connect@2,database.write@0]"
    assert caps_field(None) == "caps=[]"
    assert caps_field(CapabilityProfile(by_hop=(0, 0, 0))) == "caps=[]"


def test_caps_are_appended_after_the_axes_and_wrapper_lines_pass_through():
    profiles = {"pkg.store": CapabilityProfile(by_hop=(int(FeatureBit.PRIM_FS_WRITE), 0, 0))}
    with_axes = annotate_manifest_axes(MANIFEST, {}, "all")
    out = annotate_manifest_atomic_capabilities(with_axes, profiles).splitlines()
    assert out[0] == "<candidate_index>" and out[-1] == "</candidate_index>"
    assert out[2] == with_axes.splitlines()[2] + "|caps=[filesystem.write@0]"
    assert out[1].endswith("|caps=[]") and out[3].endswith("|caps=[]")


def test_only_the_caps_arm_prompt_describes_and_asks_for_caps():
    plain, caps = turn1_system_prompt("all"), turn1_system_prompt("all", atomic_caps=True)
    assert "caps" not in plain and TURN1_CAPS_GUIDANCE.strip() in caps and "|caps=[...]" in caps
    assert caps.replace(TURN1_CAPS_GUIDANCE, "").replace(caps[caps.index(", followed by |caps"):caps.index(" - followed by a real task")], "") == plain


def test_ablation_arms_differ_from_prism_full_only_in_their_flag():
    full = C.resolve_arm("prism_full")
    caps, rule = C.resolve_arm("prism_atomic_caps"), C.resolve_arm("prism_r0_rule_callers")
    for spec, flag in ((caps, "atomic_caps"), (rule, "turn1_rule_callers")):
        assert getattr(spec, flag) and not getattr(full, flag)
        assert (spec.kind, spec.anchor, spec.axes, spec.distractor_k, spec.breadcrumbs) == (
            full.kind, full.anchor, full.axes, full.distractor_k, full.breadcrumbs)
