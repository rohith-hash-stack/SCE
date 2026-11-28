"""Phase B requirements (`harness/experiments/r4/PHASE2_B_DESIGN.md`): the connections PRISM's graph must hold beyond
calls and inheritance, on a fixture repository modelled on the 3-arm audit's cases (delete-05's class-body
`classmethod(GenericAlias)` under a conditional definition; delete-09's `__all__` entry).

The connections go in `builder.reference_graph`, a graph of their own: PRISM's main graph keeps one edge per pair (a
reference added there would overwrite a CALLS edge between the same two definitions), and 103 places in `src/prism`
walk the main graph's edges, several without looking at the relation. Each edge there holds every kind of use between
its two nodes (`ref_kinds`); module nodes exist only there.

Each kind is one test (written as `xfail(strict=True)` before the pass existed; unmarked as each kind landed). The last tests pin what Phase B must not change before B10: the main graph,
the relations PRISM's caller views traverse, and the symbol table.
"""
import os
import subprocess

import pytest

FILES = {
    "pkg/__init__.py": (
        "from .core import helper, BaseThing\n"
        "from . import core as core_mod\n\n"
        "__all__ = [\"helper\", \"BaseThing\"]\n"
    ),
    "pkg/compat.py": (
        "import sys\nimport types\n\n"
        "if sys.version_info >= (3, 9):\n"
        "    GenericAlias = types.GenericAlias\n"
        "else:\n\n"
        "    def GenericAlias(cls):\n"
        "        return cls\n\n\n"
        "class Holder:\n"
        "    __class_getitem__ = classmethod(GenericAlias)\n"
    ),
    "pkg/core.py": (
        "from functools import wraps\n\n\n"
        "def helper(x):\n    return x\n\n\n"
        "def deco(f):\n    return f\n\n\n"
        "class BaseThing:\n    LIMIT = 3\n\n\n"
        "class BoomError(Exception):\n    pass\n\n\n"
        "Alias = BaseThing\n"
        "TOP = helper(1)\n"
        "REGISTRY = {\"h\": helper}\n\n\n"
        "@deco\n"
        "def decorated():\n    return 1\n\n\n"
        "class Child(BaseThing):\n"
        "    alias = helper\n\n"
        "    def run(self, v: BaseThing) -> int:\n"
        "        if isinstance(v, Alias):\n            pass\n"
        "        try:\n            pass\n        except BoomError:\n            pass\n"
        "        items = [helper]\n"
        "        n = BaseThing.LIMIT\n"
        "        return helper\n\n\n"
        "def shadow(helper):\n"
        "    return helper\n\n\n"
        "def uses_unknown():\n"
        "    return not_defined_anywhere\n"
    ),
}


@pytest.fixture(scope="module")
def builder(tmp_path_factory):
    from prism.cli import LANGUAGE_TIER_PERMISSIVE, build_pipeline
    root = str(tmp_path_factory.mktemp("refs") / "r")
    for p, text in FILES.items():
        os.makedirs(os.path.dirname(os.path.join(root, p)), exist_ok=True)
        open(os.path.join(root, p), "w").write(text)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    b, _ = build_pipeline(root, LANGUAGE_TIER_PERMISSIVE, use_cache=False)
    return b


def kinds(b, src, dst) -> set:
    """The kinds of use from `src` to `dst` in the reference graph (empty if none, or no reference graph yet)."""
    g = getattr(b, "reference_graph", None)
    if g is None or not g.has_edge(src, dst):
        return set()
    return set(g.edges[src, dst].get("ref_kinds", ()))


def relation(b, src, dst):
    """The main graph's relation from `src` to `dst`, or None."""
    return b.graph.edges[src, dst].get("relation", "CALLS") if b.graph.has_edge(src, dst) else None


C, P = "pkg.core", "pkg"


# ---------------------------------------------------------------------------------------------- B1 + B2
def test_import_from_a_package_init(builder):
    assert "import" in kinds(builder, P, f"{C}.helper")
    assert "import" in kinds(builder, P, f"{C}.BaseThing")


def test_all_entry(builder):
    assert kinds(builder, P, f"{C}.helper") >= {"import", "export"}


def test_module_level_call_and_value(builder):
    assert "call" in kinds(builder, C, f"{C}.helper")            # TOP = helper(1)
    assert "value" in kinds(builder, C, f"{C}.helper")           # REGISTRY = {"h": helper}
    assert "value" in kinds(builder, C, f"{C}.BaseThing")        # Alias = BaseThing


def test_module_nodes_are_marked_and_kept_out_of_the_main_graph(builder):
    node = builder.reference_graph.nodes[C]
    assert node.get("kind") == "module" and node.get("synthetic") is True
    assert C not in builder.graph and builder.symbol_table.get(C) is None


def test_class_body_reference_under_a_conditional_definition(builder):
    # delete-05: `__class_getitem__ = classmethod(GenericAlias)` uses the conditionally defined function
    assert kinds(builder, "pkg.compat.Holder", "pkg.compat.GenericAlias") & {"value", "call"}


def test_class_body_value(builder):
    assert "value" in kinds(builder, f"{C}.Child", f"{C}.helper")   # alias = helper


# ---------------------------------------------------------------------------------------------- B3 + B4
def test_parameter_annotation(builder):
    assert "annotation" in kinds(builder, f"{C}.Child.run", f"{C}.BaseThing")


def test_isinstance_through_an_alias(builder):
    # `Alias = BaseThing` at module level, then isinstance(v, Alias): the alias resolves to the class
    assert "isinstance" in kinds(builder, f"{C}.Child.run", f"{C}.BaseThing")


def test_except_clause(builder):
    assert "except" in kinds(builder, f"{C}.Child.run", f"{C}.BoomError")


def test_values_in_a_function(builder):
    assert "value" in kinds(builder, f"{C}.Child.run", f"{C}.helper")   # items = [helper]; return helper


def test_decorator(builder):
    assert "decorator" in kinds(builder, f"{C}.decorated", f"{C}.deco")


# ---------------------------------------------------------------------------------------------- B5
def test_attribute_read(builder):
    assert "attribute" in kinds(builder, f"{C}.Child.run", f"{C}.BaseThing")   # BaseThing.LIMIT


# ---------------------------------------------------------------------------------------------- precision
def test_no_edge_for_a_parameter_shadowing_a_global(builder):
    assert not kinds(builder, f"{C}.shadow", f"{C}.helper") and relation(builder, f"{C}.shadow", f"{C}.helper") is None


def test_no_edge_for_unresolved_names(builder):
    g = getattr(builder, "reference_graph", builder.graph)
    succ = list(g.successors(f"{C}.uses_unknown")) if f"{C}.uses_unknown" in g else []
    assert not [v for v in succ if "not_defined" in v]


# ---------------------------------------------------------------------------------------------- unchanged before B10
def test_caller_relations_unchanged():
    from prism.graph.concrete_builder import TRAVERSABLE_RELATIONS
    assert TRAVERSABLE_RELATIONS == frozenset({"CALLS", "INSTANTIATES", "EXTENDS", "IMPLEMENTS", "OVERRIDES", "EMBEDS"})


def test_main_graph_unchanged(builder):
    assert relation(builder, f"{C}.Child", f"{C}.BaseThing") == "EXTENDS"
    assert not any(d.get("relation") == "REFERENCES" for _u, _v, d in builder.graph.edges(data=True))
    assert not any(d.get("kind") == "module" for _n, d in builder.graph.nodes(data=True))


def test_reference_graph_survives_the_index_cache(tmp_path):
    from prism.cli import LANGUAGE_TIER_PERMISSIVE, build_pipeline
    root = str(tmp_path / "r")
    for p, text in FILES.items():
        os.makedirs(os.path.dirname(os.path.join(root, p)), exist_ok=True)
        open(os.path.join(root, p), "w").write(text)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    first, _ = build_pipeline(root, LANGUAGE_TIER_PERMISSIVE, use_cache=True)      # builds and saves
    again, _ = build_pipeline(root, LANGUAGE_TIER_PERMISSIVE, use_cache=True)      # loads
    edges = lambda b: sorted((u, v, tuple(d["ref_kinds"]), d["count"]) for u, v, d in b.reference_graph.edges(data=True))
    assert edges(first) and edges(again) == edges(first)
    assert again.reference_graph.nodes[C].get("kind") == "module"
    assert again.reference_aliases == first.reference_aliases and f"{C}.Alias" in again.reference_aliases
