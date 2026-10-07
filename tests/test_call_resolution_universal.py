"""Universal call-resolution rules (language-level, not project-specific):
virtual-dispatch expansion, single-family resolution, builtin-method guard,
*args/**kwargs as builtins, naming-convention receiver typing, return-type
inference and the repo-wide attribute index - plus the blast-radius
manifest mode (Design C) built on top of them."""
import textwrap

import pytest

from prism.cli import build_pipeline
from prism.packer.candidate_index import build_candidate_manifest


def _build(tmp_path, source: str):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "m.py").write_text(textwrap.dedent(source))
    builder, _ = build_pipeline(str(tmp_path), use_cache=False)
    return builder


def _calls(builder, caller):
    return {v: d.get("kind") for _, v, d in builder.graph.out_edges(f"pkg.m.{caller}", data=True)
            if d.get("relation") == "CALLS"}


def test_self_call_links_subclass_overrides(tmp_path):
    b = _build(tmp_path, """
        class Base:
            def run(self):
                return self.step()
            def step(self):
                return 1
        class Child(Base):
            def step(self):
                return 2
        class GrandChild(Child):
            def step(self):
                return 3
    """)
    calls = _calls(b, "Base.run")
    assert calls["pkg.m.Base.step"] is None                      # the statically resolved target, unchanged
    assert calls["pkg.m.Child.step"] == "TENTATIVE_CALL"         # possible at run time
    assert calls["pkg.m.GrandChild.step"] == "TENTATIVE_CALL"    # transitive overrider


def test_unknown_receiver_resolves_within_a_single_class_family(tmp_path):
    b = _build(tmp_path, """
        class Handler:
            def handle(self, x):
                return x
        class SubHandler(Handler):
            def handle(self, x):
                return x + 1
        def use(obj):
            return obj.handle(1)
    """)
    assert set(_calls(b, "use")) == {"pkg.m.Handler.handle", "pkg.m.SubHandler.handle"}


def test_unknown_receiver_with_two_unrelated_families_is_not_guessed_by_family(tmp_path):
    b = _build(tmp_path, """
        class A:
            def handle(self):
                return 1
        class B:
            def handle(self):
                return 2
        def use(obj):
            return obj.handle()
    """)
    assert not {"pkg.m.A.handle", "pkg.m.B.handle"} <= set(_calls(b, "use"))


@pytest.mark.parametrize("call", ["kwargs.get('a')", "args.count(1)"])
def test_star_args_and_kwargs_are_builtin_receivers(tmp_path, call):
    b = _build(tmp_path, f"""
        class Repo:
            def get(self, k):
                return k
            def count(self, x):
                return x
        def use(*args, **kwargs):
            return {call}
    """)
    assert _calls(b, "use") == {}


@pytest.mark.parametrize("call", ["obj.get(2)", "obj.items()", "obj.update({})"])
def test_builtin_named_method_with_several_repo_definitions_gets_a_sentinel_not_a_guess(tmp_path, call):
    b = _build(tmp_path, f"""
        class Repo:
            def get(self, k):
                return k
            def items(self):
                return []
            def update(self, d):
                return d
        class Cache:
            def get(self, k):
                return k
            def items(self):
                return []
            def update(self, d):
                return d
        def use(obj):
            return {call}
    """)
    targets = list(_calls(b, "use"))
    assert len(targets) == 1 and b.graph.nodes[targets[0]].get("sentinel_type") == "unresolved_polymorphic"


def test_typed_receiver_still_reaches_a_builtin_named_method(tmp_path):
    b = _build(tmp_path, """
        class Repo:
            def get(self, k):
                return k
        def use():
            repo = Repo()
            return repo.get(1)
    """)
    assert "pkg.m.Repo.get" in _calls(b, "use")


def test_receiver_named_after_a_unique_class(tmp_path):
    b = _build(tmp_path, """
        class AppConfig:
            def ready(self):
                return True
        class Unrelated:
            def ready(self):
                return False
        def populate(app_config):
            return app_config.ready()
    """)
    assert _calls(b, "populate") == {"pkg.m.AppConfig.ready": "TENTATIVE_CALL"}


def test_return_type_inference_through_fluent_chains(tmp_path):
    b = _build(tmp_path, """
        class QS:
            def _clone(self):
                c = self.__class__()
                return c
            def filter(self, **kw):
                clone = self._clone()
                return clone
            def first(self):
                return 1
        class Manager:
            def all(self):
                return QS()
        class View:
            def get_queryset(self):
                return Manager().all()
            def run(self):
                qs = self.get_queryset()
                qs.filter(a=1)
                return self.get_queryset().filter(b=2).first()
    """)
    calls = _calls(b, "View.run")
    assert "pkg.m.QS.filter" in calls and "pkg.m.QS.first" in calls


def test_attribute_index_types_receivers_by_the_classes_assigned_to_the_attribute(tmp_path):
    b = _build(tmp_path, """
        class Options:
            def contribute(self, cls):
                cls._meta = self
            def get_field(self, name):
                return name
        class FormOptions:
            def other(self):
                return 1
        class Form:
            _meta = FormOptions()
        def use(model, rel):
            opts = model._meta
            opts.get_field("a")
            return rel.model._meta.get_field("b")
    """)
    assert _calls(b, "use") == {"pkg.m.Options.get_field": "TENTATIVE_CALL"}


def test_blast_mode_manifest_walks_transitive_callers_and_default_is_unchanged(tmp_path):
    b = _build(tmp_path, """
        def seed(x):
            return helper(x)
        def helper(x):
            return x
        def c1():
            v = seed(1)
            return v
        def c2():
            return c1()
        def c3():
            return c2()
    """)
    _, default = build_candidate_manifest(b, "pkg.m.seed")
    assert default == {"pkg.m.seed", "pkg.m.helper", "pkg.m.c1"}
    manifest, both = build_candidate_manifest(b, "pkg.m.seed", direction="both")
    assert both == {"pkg.m.seed", "pkg.m.helper", "pkg.m.c1", "pkg.m.c2", "pkg.m.c3"}
    roles = {line.split("|")[0]: line.split("|")[1] for line in manifest.splitlines()[1:-1]}
    assert roles["pkg.m.c3"] == "caller" and roles["pkg.m.helper"] == "callee"
    _, tight = build_candidate_manifest(b, "pkg.m.seed", direction="both", budget_tokens=40)
    assert "pkg.m.seed" in tight and len(tight) < len(both)
    with pytest.raises(ValueError):
        build_candidate_manifest(b, "pkg.m.seed", direction="sideways")


def test_blast_mode_skips_test_code_callers(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "m.py").write_text("def seed():\n    return 1\n\ndef prod_caller():\n    return seed()\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_m.py").write_text(
        "import unittest\nfrom pkg.m import seed\n\n"
        "class SeedTests(unittest.TestCase):\n    def test_seed(self):\n        self.assertEqual(seed(), 1)\n")
    b, _ = build_pipeline(str(tmp_path), use_cache=False)
    _, universe = build_candidate_manifest(b, "pkg.m.seed", direction="both")
    assert "pkg.m.prod_caller" in universe
    assert not any(q.startswith("tests.") for q in universe)


def test_calls_through_an_inferred_binding_are_tentative(tmp_path):
    b = _build(tmp_path, """
        class QS:
            def filter(self):
                return self
        class Manager:
            def all(self):
                return QS()
        def typed():
            qs = QS()
            return qs.filter()
        def inferred(m):
            qs = Manager().all()
            return qs.filter()
    """)
    assert _calls(b, "typed")["pkg.m.QS.filter"] is None
    assert _calls(b, "inferred")["pkg.m.QS.filter"] == "TENTATIVE_CALL"


def test_causal_graph_discounts_tentative_edges_and_derives_no_coupling_from_them(tmp_path):
    from prism.traversal.causal_weights import compute_causal_edges

    b = _build(tmp_path, """
        class QS:
            def filter(self):
                return self
        class Manager:
            def all(self):
                return QS()
        class Exists:
            def __init__(self, q):
                self.q = q
        def confident():
            qs = QS()
            return qs.filter()
        def guessed():
            qs = Manager().all()
            return Exists(qs.filter())
    """)
    weights, synthetic = compute_causal_edges(b)
    assert weights[("pkg.m.guessed", "pkg.m.QS.filter")] < weights[("pkg.m.confident", "pkg.m.QS.filter")]
    # `filter`'s result flows into `Exists` only at a guessed call site.
    assert ("pkg.m.QS.filter", "pkg.m.Exists") not in synthetic
