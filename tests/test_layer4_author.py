"""Layer 4 task authoring (`harness/experiments/layer4/author.py`) and the committed tasks."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.experiments.layer4 import author as A
from harness.experiments.layer4 import tasks as T

pytest.importorskip("jsonschema")
TASKS = sorted((Path(__file__).resolve().parents[1] / "benchmarks" / "layer4" / "tasks").glob("l4_*.json"))


def test_sql_filter_drops_test_app_and_backend_tables():
    sql = {"django_session": {"INSERT": 2}, "sessions_tests_customsession": {"INSERT": 1},
           "sqlite_master": {"SELECT": 4}, "_transaction": {"SAVEPOINT": 2}}
    kept, dropped = A.filter_sql(sql, ["sessions_tests", "cache"])
    assert set(kept) == {"django_session", "_transaction"} and dropped == ["sessions_tests_customsession", "sqlite_master"]
    assert T.side_effects_from_sql(kept) == ["DB_TRANSACTION", "DB_WRITE"]  # no DB_READ from introspection


def test_commands_and_gating_paths(tmp_path):
    (tmp_path / "test").mkdir()
    for name in ("b.js", "a.js"):
        (tmp_path / "test" / name).write_text("")
    cmd = A.expand(["{python}", "node_modules/.bin/mocha", "test/*.js"], str(tmp_path), "/venv/python")
    assert cmd == ["/venv/python", "node_modules/.bin/mocha", "test/a.js", "test/b.js"]
    assert A.test_files(cmd, "express") == ["test/a.js", "test/b.js"]
    assert A.django_label_paths(["cache.tests.LocMemCacheTests", "update"]) == ["tests/cache/", "tests/update/"]


def test_trpc_paths_and_vitest_filters(monkeypatch):
    # Vitest filters are relative to its root (packages/): the gating file is packages/<filter>
    cmd = ["{node}", "{vitest}", "--run", "--no-threads", "tests/server/index.test.ts",
           "server/src/observable/operators/share.test.ts"]
    assert A.test_files(cmd, "trpc") == ["packages/tests/server/index.test.ts",
                                         "packages/server/src/observable/operators/share.test.ts"]
    monkeypatch.setattr(A, "corpus_root", lambda corpus: "/c/trpc/packages/server/src")
    assert A.clone_root("trpc") == "/c/trpc"
    monkeypatch.setattr(A, "vitest_bin", lambda clone: "/c/trpc/packages/tests/node_modules/vitest/vitest.mjs")
    A.NODE["bin"] = "/opt/node20/bin/node"
    assert A.expand(cmd[:3], "/c/trpc", None) == ["/opt/node20/bin/node", "/c/trpc/packages/tests/node_modules/vitest/vitest.mjs", "--run"]


def test_trpc_specs_target_the_index_root():
    from benchmarks.layer4.specs import SPECS
    trpc = [s for s in SPECS if s["corpus"] == "trpc"]
    assert len(trpc) == 4 and {s["family"] for s in trpc} == {"forward_blast", "backward_adaptation"}
    for spec in trpc:
        for cand in [spec, *spec.get("alternatives", [])]:
            assert "." in cand["qname"] and not cand["qname"].startswith(("packages", "client"))


def test_oracle_callees_python_direct_only_node_all():
    m = {"descendants": {"a.f": 5, "a.g": 9, "a.deep": 20, "x.unknown": 3}, "descendant_depth": {"a.f": 1, "a.g": 1, "a.deep": 3}}
    assert A.oracle_callees(m, {"a.f", "a.g", "a.deep"}, "a.focal") == ["a.g", "a.f"]
    m.pop("descendant_depth")  # Node: coverage has no call order
    assert A.oracle_callees(m, {"a.f", "a.g", "a.deep"}, "a.focal") == ["a.deep", "a.g", "a.f"]


@pytest.mark.parametrize("path", TASKS, ids=[p.stem for p in TASKS])
def test_committed_tasks_validate(path):
    task = json.loads(path.read_text())
    assert T.validate_task(task) == []
    manifest = Path(__file__).resolve().parents[1] / "benchmarks" / "layer4" / "manifests" / f"{task['task_id']}.json"
    assert manifest.exists()
    if task["family"] == "backward_adaptation":
        assert (manifest.parent.parent / "gates" / f"{task['task_id']}.json").exists()


def test_committed_suite_composition():
    tasks = [json.loads(p.read_text()) for p in TASKS]
    authored = tuple(sorted({t["corpus"] for t in tasks}))
    assert T.validate_suite(tasks, corpora=authored) == []
