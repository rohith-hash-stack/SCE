"""P3b focal draw (harness/experiments/retrieval_pivot/p3b_draw.py): Prism-neutral and reproducible."""
from __future__ import annotations

import random
from types import SimpleNamespace

from harness.experiments.retrieval_pivot import p3b_draw as D


def _corpus(names, kinds):
    table = {q: SimpleNamespace(kind=kinds.get(q, "function")) for q in names}
    return SimpleNamespace(name="fastapi", known=set(names),
                           state=SimpleNamespace(builder=SimpleNamespace(symbol_table=table)))


def test_candidates_are_library_functions_not_nested_not_overloads_not_already_used():
    names = ["fastapi.a.f", "fastapi.a.f#2", "fastapi.a.g", "fastapi.a.g.inner", "fastapi.a.used", "scripts.x.run"]
    keep, dropped = D.candidates(_corpus(names, {}), excluded={"fastapi.a.used"})
    assert keep == ["fastapi.a.f", "fastapi.a.g"]
    assert dropped == {"overload_variant": 1, "nested_function": 1, "used_by_existing_tasks": 1, "outside_library_package": 1}


def test_the_order_depends_only_on_the_seed_and_the_candidate_names():
    names = [f"fastapi.m.f{i}" for i in range(50)]
    one, two = sorted(names), list(reversed(names))
    random.Random(f"p3b:{D.SEED}:fastapi").shuffle(one)
    two = sorted(two)  # the draw always shuffles the sorted list
    random.Random(f"p3b:{D.SEED}:fastapi").shuffle(two)
    assert one == two and one != names


def test_the_change_template_names_only_the_focal():
    assert D.display("lib.response.download", "method") == "response.download"
    assert D.leaf("observable.ObservableAbortError.constructor") == "ObservableAbortError"
    text = D.TEMPLATES["python"].format(name="Store.save")
    assert text.startswith("Store.save() gains a required keyword-only argument `context`")
