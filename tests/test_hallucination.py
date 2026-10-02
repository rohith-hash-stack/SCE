import math
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
