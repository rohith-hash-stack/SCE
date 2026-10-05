"""M4 item 4: T5 (blast) tasks derived from T2 seeds by
benchmarks/scripts/derive_t5_from_t2.py. Every derived YAML loads through
the existing loader and agreement gate, becomes a BlastRadiusTask with a
non-empty gold affected set, and is scorable by _score_t5 with a fractional
score in [0, 1]. Plus unit tests for the generator's pure helpers."""
import math
from pathlib import Path

import pytest
import yaml

from benchmarks.ground_truth.loader import load_tasks_from_dir
from benchmarks.scripts import derive_t5_from_t2 as D
from harness.scoring.canonical import DeliveredContext, NormalizedAnswer
from harness.scoring.scorer import score
from harness.tasks.loaders import from_legacy

TASKS = Path(__file__).resolve().parent.parent / "benchmarks" / "ground_truth" / "tasks"
CORPORA = ["fastapi", "django", "express", "trpc"]


def _derived(corpus):
    return sorted((TASKS / corpus).glob(f"{corpus}_t5_*.yaml"))


def _loaded(corpus):
    result = load_tasks_from_dir(TASKS / corpus)
    by_id = {t.task_id: t for t in result.accepted}
    return result, by_id


@pytest.mark.parametrize("corpus", CORPORA)
def test_every_derived_yaml_loads_with_a_non_empty_affected_set(corpus):
    files = _derived(corpus)
    assert files, f"no derived T5 tasks for {corpus}"
    result, by_id = _loaded(corpus)
    assert not result.rejected, result.rejected
    for f in files:
        raw = yaml.safe_load(f.read_text())
        legacy = by_id[raw["task_id"]]
        assert legacy.task_type == "blast" and legacy.cohen_kappa == 1.0      # two derivation runs agree
        assert legacy.prompt == f"What breaks if `{legacy.seed_symbol}` changes?"
        task = from_legacy(legacy, "/tmp/repo")
        assert task.task_type == "T5_blast_radius"
        gold = task.ground_truth.pipeline_symbols
        assert len(gold) >= D.MIN_CALLERS and gold == sorted(set(gold))
        assert legacy.seed_symbol not in gold
        assert "Derived from T2 task:" in f.read_text().splitlines()[0]


@pytest.mark.parametrize("corpus", CORPORA)
def test_derived_tasks_never_touch_the_t2_seed_task(corpus):
    """The derived-from T2 task exists, is unchanged in type, and shares the seed."""
    _, by_id = _loaded(corpus)
    for f in _derived(corpus):
        t2_id = f.read_text().splitlines()[0].split("Derived from T2 task:")[1].strip()
        t5 = by_id[f.stem]
        assert by_id[t2_id].task_type == "debug" and by_id[t2_id].seed_symbol == t5.seed_symbol


@pytest.mark.parametrize("corpus", CORPORA)
def test_one_derived_task_per_corpus_scores_fractionally(corpus):
    _, by_id = _loaded(corpus)
    legacy = by_id[_derived(corpus)[0].stem]
    task = from_legacy(legacy, "/tmp/repo")
    gold = task.ground_truth.pipeline_symbols
    half = gold[: len(gold) // 2]
    ctx = DeliveredContext("arm0", task.task_id, [], 0, 0, {})
    ans = NormalizedAnswer("arm0", task.task_id, "x", "x", half, "plain_text", True, 1, 0.1)
    res = score(task, ctx, ans)
    assert 0.0 <= res.tsr <= 1.0 and not math.isnan(res.tsr)
    assert res.tsr == pytest.approx(len(half) / len(gold))
    assert res.task_specific["false_negative_rate"] == pytest.approx(1 - res.tsr)
    full = NormalizedAnswer("arm0", task.task_id, "x", "x", list(gold), "plain_text", True, 1, 0.1)
    assert score(task, ctx, full).tsr == 1.0


# ------------------------------------------------------------- generator
def test_is_production():
    for path in ("fastapi/routing.py", "lib/router/index.js", "core/router.ts", "django/utils/http.py"):
        assert D.is_production(path), path
    for path in ("tests/test_x.py", "docs_src/app/main.py", "test/app.use.js", "examples/auth/index.js",
                 "django/contrib/admin/tests.py", "pkg/test_util.py", "pkg/util_test.py", "pkg/conftest.py",
                 "src/router.test.ts", "src/a.spec.tsx", "benchmarks/x.py", "scripts/build.js", "docs/conf.py"):
        assert not D.is_production(path), path


def test_closure_is_transitive_breadth_first_and_order_independent():
    edges = {"seed": {("a", "a.py"), ("b", "b.py")}, "a": {("c", "c.py")}, "c": {("a", "a.py"), ("seed", "s.py")}}
    got = D.closure(lambda s: edges.get(s, set()), "seed")
    assert got == {"a": (1, "a.py"), "b": (1, "b.py"), "c": (2, "c.py")}     # cycles and the seed itself handled


def test_short_name_and_rendered_yaml_round_trips(tmp_path):
    assert D.short_name("express_t02_003_routing_dispatch_loop") == "routing_dispatch_loop"
    row = {"t2_task_id": "fastapi_t02_001_x", "seed": "pkg.mod.f",
           "production_callers": {"pkg.mod.g": 1, "pkg.other.h": 2}}
    text = D.render_yaml("fastapi", "abc123", "fastapi_t5_001_x", row)
    (tmp_path / "fastapi_t5_001_x.yaml").write_text(text)
    result = load_tasks_from_dir(tmp_path)
    assert not result.rejected and len(result.accepted) == 1
    t = result.accepted[0]
    assert t.adjudicated.critical_callers == {"pkg.mod.g", "pkg.other.h"} and t.cohen_kappa == 1.0
    assert "1 direct, 1 transitive (up to 2 hops)" in t.adjudicated.expected_solution


def test_named_fqn_stops_at_anonymous_callbacks():
    def sym(name, start, end, children=()):
        return {"name": name, "range": {"start": {"line": start, "character": 0}, "end": {"line": end, "character": 0}},
                "children": list(children)}

    def contains(s, line, character):
        return s["range"]["start"]["line"] <= line <= s["range"]["end"]["line"]

    outline = [sym("handle", 1, 50, [sym("next", 5, 40, [sym("self.process_params() callback", 10, 20)])]),
               sym("<function>", 60, 70)]
    assert D.named_fqn("lib.router", outline, 15, 4, contains)[0] == "lib.router.handle.next"
    assert D.named_fqn("lib.router", outline, 7, 4, contains)[0] == "lib.router.handle.next"
    name, best = D.named_fqn("lib.router", outline, 65, 0, contains)
    assert name == "lib.router" and best is None                     # anonymous at module level: no caller symbol


def test_gold_names_drop_collision_suffix_and_non_production():
    callers = {"m.app": (1, "m.py"), "m.app#2": (2, "m.py"), "m.helper": (3, "m.py"), "t.test_x": (1, "tests/t.py"),
               "m.seed#2": (1, "m.py")}
    assert D.gold_names(callers, "m.seed") == {"m.app": 1, "m.helper": 3}
    # a seed known under two names (T2's flat name and tsserver's nested one) is never its own caller
    assert D.gold_names(callers, {"m.seed", "m.helper"}) == {"m.app": 1}


@pytest.mark.parametrize("corpus", CORPORA)
def test_derived_gold_names_are_plain_identifiers(corpus):
    import re
    # module segments come from file paths and may hold hyphens (tRPC's adapters/aws-lambda.ts)
    ident = re.compile(r"^[A-Za-z_$][\w$-]*(\.[A-Za-z_$][\w$-]*)*$")
    for f in _derived(corpus):
        gold = yaml.safe_load(f.read_text())["adjudicated"]["critical_callers"]
        assert all(ident.match(g) for g in gold), [g for g in gold if not ident.match(g)]


# ------------------------------------------------- Pyright cross-check, cap
def test_cross_check_excludes_a_task_when_prism_and_pyright_differ_by_one_symbol():
    prism = {("pkg/a.py", 10), ("pkg/b.py", 20), ("pkg/c.py", 30)}
    same = D.cross_check(prism, {"seed_found": True, "identities": set(prism)})
    assert same["agree"] and same["only_prism"] == [] and same["only_pyright"] == []
    one_more = D.cross_check(prism, {"seed_found": True, "identities": prism | {("pkg/d.py", 5)}})
    assert not one_more["agree"] and one_more["only_pyright"] == [["pkg/d.py", 5]] and one_more["only_prism"] == []
    one_less = D.cross_check(prism, {"seed_found": True, "identities": prism - {("pkg/c.py", 30)}})
    assert not one_less["agree"] and one_less["only_prism"] == [["pkg/c.py", 30]]
    unlocated = D.cross_check(prism, {"seed_found": False, "identities": set()})
    assert not unlocated["agree"] and unlocated["pyright_seed_found"] is False


def test_definition_identity_skips_decorators(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("import functools\n\n\n@functools.lru_cache\ndef f():\n    return 1\n\n\nclass C:\n"
                 "    @property\n    def p(self):\n        return f()\n")
    assert D.definition_identity(str(tmp_path), str(f), "m.f", 4) == ("m.py", 5)          # range starts at @
    assert D.definition_identity(str(tmp_path), str(f), "m.C.p#2", 10) == ("m.py", 11)


@pytest.mark.parametrize("corpus", CORPORA)
def test_derived_gold_sets_respect_the_cap(corpus):
    for f in _derived(corpus):
        gold = yaml.safe_load(f.read_text())["adjudicated"]["critical_callers"]
        assert D.MIN_CALLERS <= len(gold) <= D.MAX_GOLD, (f.name, len(gold))


@pytest.mark.parametrize("corpus", ["fastapi", "django"])
def test_python_gold_is_cross_verified_with_pyright(corpus):
    for f in _derived(corpus):
        assert "# Cross-verified: Pyright textDocument/references" in f.read_text(), f.name


# ----------------------------------------------------------- bias control
def test_t5_bias_control_compares_arm3_with_the_other_retrieval_arms():
    import pandas as pd

    from harness.reporting.t5_bias_control import bias_control
    rows = []
    for task in ("trpc_t5_001", "express_t5_001"):
        for arm, cpi in (("arm0", 0.0), ("arm1", 0.2), ("arm2", 0.2), ("arm3", 0.8), ("arm4", 0.2), ("arm5", 0.2),
                         ("oracle", 1.0)):
            rows.append({"task_id": task, "repo_id": task.split("_")[0], "task_type": "T5_blast_radius",
                         "arm": arm, "uniform_cpi": cpi})
    rows.append({"task_id": "fastapi_t5_001", "repo_id": "fastapi", "task_type": "T5_blast_radius",
                 "arm": "arm3", "uniform_cpi": 0.0})                     # Python: not part of the control
    res = bias_control(pd.DataFrame(rows))
    assert res["n_tasks"] == 2 and res["by_arm"]["arm3"]["mean_overlap"] == pytest.approx(0.8)
    assert res["comparison_arms_mean_overlap"] == pytest.approx(0.2)
    assert res["arm3_ratio"] == pytest.approx(4.0)
    assert set(res["by_corpus"]) == {"express", "trpc"}
