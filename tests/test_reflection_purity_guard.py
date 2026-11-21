"""Reflection guard for the Substance axis (Operation Groundwire, Phase 1 /
Spec 3, Part A).

A function whose own body contains a dynamic-hazard construct (reflection,
`eval`/`exec`, dynamic import, dynamic namespace lookup, subscript dispatch)
gets `FeatureBit.HAS_REFLECTION` and is never `SINK_PURE_COMPUTE`, so it is
never `ROLE_LEAF_UTILITY` either. It is an own-body property, never
propagated to callers. The cached and uncached mask paths agree.
"""
from __future__ import annotations

import textwrap

import pytest

from prism.cli import build_pipeline
from prism.graph.call_site import has_dynamic_hazard_construct
from prism.semantics.bitmask import (
    ALL_KNOWN_BITS,
    FORM_BITS,
    MODIFIER_BITS,
    OUTPUT_BITS,
    ROLE_BITS,
    SUBSTANCE_BITS,
    FeatureBit,
    describe_mask,
)
from prism.semantics.extractor import compute_feature_masks, compute_feature_masks_cached
from prism.semantics.substance import _is_pure_eligible, compute_substance_bits

REFLECT = int(FeatureBit.HAS_REFLECTION)
PURE = int(FeatureBit.SINK_PURE_COMPUTE)
LEAF = int(FeatureBit.ROLE_LEAF_UTILITY)


def _build(tmp_path, filename: str, source: str):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / filename).write_text(textwrap.dedent(source))
    builder, _ = build_pipeline(str(repo))
    return builder, str(repo)


# --------------------------------------------------------------------- #
# The bit itself
# --------------------------------------------------------------------- #
def test_has_reflection_is_a_modifier_bit_outside_every_axis():
    assert FeatureBit.HAS_REFLECTION == 1 << 7
    assert MODIFIER_BITS == frozenset({FeatureBit.HAS_REFLECTION})
    for axis in (SUBSTANCE_BITS, FORM_BITS, OUTPUT_BITS, ROLE_BITS):
        assert not (MODIFIER_BITS & axis)
    assert FeatureBit.HAS_REFLECTION in ALL_KNOWN_BITS
    assert describe_mask(REFLECT) == ["HAS_REFLECTION"]


# --------------------------------------------------------------------- #
# Python constructs
# --------------------------------------------------------------------- #
PY_REFLECTIVE = {
    "getattr": "def f(o, k):\n    return getattr(o, k)\n",
    "setattr": "def f(o, k):\n    setattr(o, k, 1)\n",
    "delattr": "def f(o, k):\n    delattr(o, k)\n",
    "dunder_import": "def f(name):\n    return __import__(name)\n",
    "importlib": "import importlib\n\n\ndef f(name):\n    return importlib.import_module(name)\n",
    "importlib_from": "from importlib import import_module\n\n\ndef f(name):\n    return import_module(name)\n",
    "eval": "def f(src):\n    return eval(src)\n",
    "exec": "def f(src):\n    exec(src)\n",
    "globals_lookup": "def f(name):\n    return globals()[name]\n",
    "locals_lookup": "def f(name):\n    x = 1\n    return locals()[name]\n",
    "vars_lookup": "def f(o, name):\n    return vars(o)[name]\n",
    "subscript_dispatch": "def f(handlers, action):\n    return handlers[action]()\n",
}

PY_PURE = {
    "math": "def f(a, b):\n    return a * b + 1\n",
    "vars_self": "def f(o):\n    return dict(vars(o))\n",
    "globals_literal_key": "def f():\n    return globals()['__name__']\n",
    "attribute_named_getattr": "def f(o):\n    return o.getattr\n",
}


@pytest.mark.parametrize("name", sorted(PY_REFLECTIVE))
def test_python_reflective_function_gets_has_reflection_not_pure(tmp_path, name):
    builder, _ = _build(tmp_path, "svc.py", PY_REFLECTIVE[name])
    mask = compute_feature_masks(builder)["svc.f"]
    assert mask & REFLECT, describe_mask(mask)
    assert not mask & PURE, describe_mask(mask)


@pytest.mark.parametrize("name", sorted(PY_PURE))
def test_python_non_reflective_function_stays_pure(tmp_path, name):
    builder, _ = _build(tmp_path, "svc.py", PY_PURE[name])
    mask = compute_feature_masks(builder)["svc.f"]
    assert mask & PURE, describe_mask(mask)
    assert not mask & REFLECT, describe_mask(mask)


def test_is_pure_eligible_reports_both_reasons(tmp_path):
    builder, _ = _build(
        tmp_path,
        "svc.py",
        """
        class C:
            def reflect(self, k):
                return getattr(self, k)

            def mutate(self):
                self.x = 1

            def plain(self, a):
                return a + 1
        """,
    )
    results = {}
    for name in ("reflect", "mutate", "plain"):
        q = f"svc.C.{name}"
        def_node = builder.def_node(q)
        parsed = builder.parsed_file(next(s.file for s in builder.symbol_table if s.qualified_name == q))
        results[name] = _is_pure_eligible(def_node, parsed)
    assert results == {"reflect": (False, True), "mutate": (False, False), "plain": (True, False)}


# --------------------------------------------------------------------- #
# JS / TS constructs
# --------------------------------------------------------------------- #
JS_REFLECTIVE = {
    "reflect_get": "function f(o, k) {\n  return Reflect.get(o, k);\n}\n",
    "reflect_apply": "function f(fn, args) {\n  return Reflect.apply(fn, null, args);\n}\n",
    "proxy": "function f(t, h) {\n  return new Proxy(t, h);\n}\n",
    "new_function": "function f(src) {\n  return new Function(src);\n}\n",
    "function_call": "function f(src) {\n  return Function(src)();\n}\n",
    "eval": "function f(src) {\n  return eval(src);\n}\n",
}


@pytest.mark.parametrize("ext", ["js", "ts"])
@pytest.mark.parametrize("name", sorted(JS_REFLECTIVE))
def test_js_ts_reflective_function_gets_has_reflection(tmp_path, name, ext):
    builder, _ = _build(tmp_path, f"svc.{ext}", JS_REFLECTIVE[name])
    mask = compute_feature_masks(builder)["svc.f"]
    assert mask & REFLECT, describe_mask(mask)
    assert not mask & PURE, describe_mask(mask)


def test_js_plain_function_is_pure(tmp_path):
    builder, _ = _build(tmp_path, "svc.js", "function f(a, b) {\n  return a + b;\n}\n")
    mask = compute_feature_masks(builder)["svc.f"]
    assert mask & PURE and not mask & REFLECT


def test_js_reflect_named_local_is_not_reflection(tmp_path):
    builder, _ = _build(tmp_path, "svc.js", "function f(o) {\n  return o.Reflect;\n}\n")
    assert not compute_feature_masks(builder)["svc.f"] & REFLECT


# --------------------------------------------------------------------- #
# Interaction with sinks, callers, roles
# --------------------------------------------------------------------- #
def test_reflective_function_with_real_sink_keeps_its_sink(tmp_path):
    builder, _ = _build(
        tmp_path,
        "svc.py",
        "import requests\n\n\ndef f(url, k):\n    getattr(requests, k)\n    return requests.get(url)\n",
    )
    mask = compute_feature_masks(builder)["svc.f"]
    assert mask & int(FeatureBit.SINK_NETWORK_IO) and mask & REFLECT and not mask & PURE


def test_reflection_is_not_propagated_to_callers(tmp_path):
    builder, _ = _build(
        tmp_path,
        "svc.py",
        "def pick(o, k):\n    return getattr(o, k)\n\n\ndef caller(o):\n    return pick(o, 'x')\n",
    )
    masks = compute_feature_masks(builder)
    assert masks["svc.pick"] & REFLECT
    assert not masks["svc.caller"] & REFLECT
    assert masks["svc.caller"] & PURE


def test_substance_bits_carry_the_modifier(tmp_path):
    builder, _ = _build(tmp_path, "svc.py", PY_REFLECTIVE["getattr"])
    assert compute_substance_bits(builder)["svc.f"] & REFLECT


LEAF_SHAPE = """
def helper(o, k):
    return {body}


def c1(o):
    return helper(o, 'a')


def c2(o):
    return helper(o, 'b')


def c3(o):
    return helper(o, 'c')


def c4(o):
    return helper(o, 'd')


def c5(o):
    return helper(o, 'e')
"""


def test_pure_high_fan_in_helper_is_leaf_utility_control(tmp_path):
    builder, _ = _build(tmp_path, "svc.py", LEAF_SHAPE.format(body="(o, k)"))
    assert compute_feature_masks(builder)["svc.helper"] & LEAF


def test_reflective_high_fan_in_helper_is_never_leaf_utility(tmp_path):
    builder, _ = _build(tmp_path, "svc.py", LEAF_SHAPE.format(body="getattr(o, k)"))
    mask = compute_feature_masks(builder)["svc.helper"]
    assert mask & REFLECT and not mask & LEAF, describe_mask(mask)


# --------------------------------------------------------------------- #
# Cached path parity and detector coverage
# --------------------------------------------------------------------- #
def test_cached_and_uncached_masks_agree_on_reflective_code(tmp_path):
    source = "\n\n".join(src.replace("def f(", f"def f_{name}(") for name, src in sorted(PY_REFLECTIVE.items()))
    source += "\n\n" + "\n\n".join(src.replace("def f(", f"def g_{name}(") for name, src in sorted(PY_PURE.items()))
    builder, repo = _build(tmp_path, "svc.py", source)
    uncached = compute_feature_masks(builder)
    cached = compute_feature_masks_cached(builder, repo)
    assert uncached == cached
    assert all(uncached[f"svc.f_{n}"] & REFLECT for n in PY_REFLECTIVE)
    # Second cached call is served from the file cache; still identical.
    assert compute_feature_masks_cached(builder, repo) == uncached


def test_detector_presence_scan_matches_mask(tmp_path):
    builder, _ = _build(tmp_path, "svc.py", PY_REFLECTIVE["delattr"])
    parsed = builder.parsed_file(next(s.file for s in builder.symbol_table if s.qualified_name == "svc.f"))
    assert has_dynamic_hazard_construct(builder.def_node("svc.f"), parsed)
