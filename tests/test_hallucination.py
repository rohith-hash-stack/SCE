import math

import pytest
from pathlib import Path

from harness.scoring import hallucination as H
from harness.scoring.canonical import DeliveredContext, DeliveredItem
from harness.tasks.schema import LocalizationTask, GroundTruth


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text("class RealThing:\n    def compute_total(self):\n        return helper_fn()\n")
    return tmp_path


def _task(root, gold=("pkg.core.RealThing.compute_total",)):
    return LocalizationTask(task_id="t", repo_id="r", repo_root=str(root), query="q",
                            ground_truth=GroundTruth(pipeline_symbols=list(gold)))


def test_extracts_code_identifiers_not_prose():
    text = ("The `RealThing.compute_total()` method in pkg/core.py calls helper_fn, e.g. via "
            "pkg.core.RealThing. This Function is fine. See FakeWidget too.")
    ids = H.extract_identifiers(text)
    assert ids[0] == "RealThing.compute_total"
    assert {"pkg/core.py", "helper_fn", "pkg.core.RealThing", "FakeWidget"} <= set(ids)
    assert "This" not in ids and "Function" not in ids and "e.g" not in ids


def test_categories():
    assert H.categorize("pkg/core.py") == "file_path"
    assert H.categorize("pkg.core") == "module_import"
    assert H.categorize("RealThing") == "class_name"
    assert H.categorize("compute_total") == "method_name"


def test_rate_uses_universe_then_repo_resolution(tmp_path):
    root = _repo(tmp_path)
    task = _task(root)
    ctx = DeliveredContext("arm1", "t", [DeliveredItem("x", "c", 1, 1, "code_chunk", ["pkg.core.Bogus"])], 1, 13000)
    rate, bd = H.hallucination_rate(
        ["pkg.core.RealThing.compute_total", "compute_total", "helper_fn", "pkg/core.py",
         "pkg.core.Bogus", "NonExistentWidget", "abc"], task, ctx)
    # 6 candidates (>=4 chars); Bogus and NonExistentWidget unresolved.
    # pkg.core.Bogus being in the ARM's delivered set does not rescue it.
    assert rate == 2 / 6
    assert bd["module_import"] == ["pkg.core.Bogus"] and bd["class_name"] == ["NonExistentWidget"]


def test_nan_when_no_candidates_and_cache_hits(tmp_path):
    task = _task(_repo(tmp_path))
    rate, _ = H.hallucination_rate(["abc", "x"], task)
    assert math.isnan(rate)
    cache = H.build_symbol_cache(["lib.mod.Widget.render"])
    assert {"render", "Widget.render", "lib.mod.Widget.render"} <= cache
    assert H.resolve_identifier("Widget.render", str(tmp_path), cache)
    assert not H.resolve_identifier("Widget.paint", str(tmp_path), cache)


def _fastapi_like(tmp_path):
    (tmp_path / "fastapi" / "dependencies").mkdir(parents=True)
    (tmp_path / "fastapi" / "dependencies" / "utils.py").write_text(
        "async def solve_dependencies(request):\n    values = {}\n    errors = []\n    return values, errors\n")
    (tmp_path / "fastapi" / "exception_handlers.py").write_text(
        "async def request_validation_exception_handler(request, exc):\n    return exc.errors()\n")
    cache = H.build_symbol_cache(["fastapi.dependencies.utils.solve_dependencies",
                                  "fastapi.exception_handlers.request_validation_exception_handler"])
    return tmp_path, cache


def test_invented_dotted_names_are_hallucinations_even_if_last_word_exists(tmp_path):
    """Regression (Kaggle d087d1b): these resolved through their last
    component ("values", "errors"), which ripgrep finds as plain words."""
    root, cache = _fastapi_like(tmp_path)
    assert H.resolve_identifier("values", str(root), cache)            # the plain words do exist
    assert H.resolve_identifier("errors", str(root), cache)
    for invented in ("fastapi.dependencies.utils.solve_dependencies.values",
                     "fastapi.exception_handlers.exc.errors"):
        assert not H.resolve_identifier(invented, str(root), cache), invented
    task = _task(root, gold=("fastapi.dependencies.utils.solve_dependencies",))
    rate, bd = H.hallucination_rate(
        ["fastapi.dependencies.utils.solve_dependencies", "fastapi.dependencies.utils.solve_dependencies.values",
         "fastapi.exception_handlers.exc.errors", "utils.solve_dependencies"], task, symbol_cache=cache)
    assert rate == 2 / 4
    assert bd["module_import"] == ["fastapi.dependencies.utils.solve_dependencies.values",
                                   "fastapi.exception_handlers.exc.errors"]


def test_real_dotted_suffixes_still_resolve(tmp_path):
    root, cache = _fastapi_like(tmp_path)
    for real in ("fastapi.dependencies.utils.solve_dependencies", "utils.solve_dependencies",
                 "exception_handlers.request_validation_exception_handler"):
        assert H.resolve_identifier(real, str(root), cache), real
    assert not H.resolve_identifier("utils.solve_dependencies", str(root), None)   # no cache: not resolvable


# ---- import-aware and inheritance-aware dotted resolution (Kaggle 1fd53af) ----
def test_module_bindings_and_inheritance_unit(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "helpers.py").write_text("def real_helper():\n    pass\n")
    (pkg / "core.py").write_text(
        "from pkg.helpers import real_helper\nfrom ext.lib import Thing as Thing\nimport os.path\n"
        "try:\n    from fast import speedup\nexcept ImportError:\n    speedup = None\n"
        "CONSTANT = 1\n\ndef run(values):\n    local_cache = {}\n    return values\n")
    cache = H.build_symbol_cache(["pkg.helpers.real_helper", "pkg.core.run", "pkg.core.Base.errors", "pkg.core.Child"])
    ancestors = {"pkg.core.Child": frozenset({"pkg.core.Base"})}
    root = str(tmp_path)
    for real in ("pkg.core.real_helper", "pkg.core.Thing", "pkg.core.os", "pkg.core.speedup",
                 "pkg.core.CONSTANT", "pkg.core.Child.errors", "core.Child.errors"):
        assert H.resolve_identifier(real, root, cache, ancestors), real
    for invented in ("pkg.core.run.values", "pkg.core.local_cache", "pkg.core.real_helper.extra",
                     "pkg.core.Child.missing", "pkg.nomodule.thing"):
        assert not H.resolve_identifier(invented, root, cache, ancestors), invented
    assert not H.resolve_identifier("pkg.core.Child.errors", root, cache, None)    # no hierarchy: strict


FASTAPI_ROOT = "/home/user/SCE/.benchmarks/corpora/fastapi"
KAGGLE_MUST_RESOLVE = [
    "fastapi.dependencies.utils.get_path_param_names", "fastapi.dependencies.utils.copy_field_info",
    "fastapi.dependencies.utils.field_annotation_is_scalar", "fastapi.dependencies.utils.get_annotation_from_field_info",
    "fastapi.dependencies.utils.get_origin", "fastapi.dependencies.utils.get_args", "fastapi.responses.JSONResponse",
    "fastapi.exceptions.RequestValidationError.errors",
]
KAGGLE_MUST_FAIL = [
    "fastapi.dependencies.utils.solve_dependencies.values", "fastapi.exception_handlers.exc.errors",
    "fastapi.dependencies.utils.dependency_cache", "fastapi.dependencies.utils.sub_dependant",
    "fastapi.dependencies.utils.solve_dependencies.request_params_to_args", "fastapi.routing.APIRoute._get_dependencies",
]


@pytest.fixture(scope="module")
def fastapi_index():
    import os
    if not os.path.isdir(FASTAPI_ROOT):
        pytest.skip("FastAPI checkout missing")
    from prism.cli import build_pipeline

    from harness.pipeline import class_ancestors
    b, _ = build_pipeline(FASTAPI_ROOT)
    return H.build_symbol_cache(b.symbol_table._symbols), class_ancestors(b)


@pytest.mark.parametrize("name", KAGGLE_MUST_RESOLVE)
def test_kaggle_real_names_resolve(fastapi_index, name):
    cache, ancestors = fastapi_index
    assert H.resolve_identifier(name, FASTAPI_ROOT, cache, ancestors)


@pytest.mark.parametrize("name", KAGGLE_MUST_FAIL)
def test_kaggle_invented_names_still_fail(fastapi_index, name):
    cache, ancestors = fastapi_index
    assert not H.resolve_identifier(name, FASTAPI_ROOT, cache, ancestors)
