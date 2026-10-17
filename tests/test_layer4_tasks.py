"""Layer 4 task contract (`harness/experiments/layer4/tasks.py`, `task.schema.json`, `cell.schema.json`)."""
from __future__ import annotations

import copy

import pytest

from harness.experiments.layer4 import tasks as T

pytest.importorskip("jsonschema")

SHA = "2ec29bfa2e3a170901be5201962f30e9faf73f96"


def forward(corpus="django", n=1):
    django = corpus == "django"
    return {
        "schema_version": "layer4.task.v1", "task_id": f"l4_fwd_{corpus}_{n:02d}", "family": "forward_blast",
        "corpus": corpus, "pinned_commit": SHA,
        "focal": {"qname": f"pkg{n}.core.Repo.save_base", "file": "pkg/core.py", "line": 10, "index_file": "pkg/core.py",
                  "language": "python", "signature": "def save_base(self, raw=False):"},
        "change": {"kind": "raise_contract", "description": "save_base raises IntegrityError instead of returning None"},
        "prompt": f"If save_base starts raising IntegrityError, which callers are affected? ({n})",
        "answer_contract": "forward_json_v1",
        "arm_view": {"seed_symbol": f"pkg{n}.core.Repo.save_base", "task_type": "blast",
                     "oracle_symbols": [f"pkg{n}.core.Repo.save_base", "pkg.core.Repo.save", "pkg.query.QuerySet.create"],
                     "oracle_required_context": [], "root_imports": [], "mask": {"symbols": [], "mode": "none"},
                     "exclude_paths": []},
        "gold": {"source": "runtime_trace", "direction": "upstream",
                 "affected_symbols": ["pkg.core.Repo.save", "pkg.query.QuerySet.create"],
                 "side_effects": ["DB_READ", "DB_WRITE"] if django else None, "tables": ["basic_article"] if django else None,
                 "trace_manifest": "reports/layer4/manifests/x.json", "min_count": 1},
        "validity": {"trace_exit_code": 0, "async_window": False,
                     "contamination": {"arm0_score": None, "flagged": None, "seeds": []}},
        "provenance": {"tracer": "trace_django" if django else "trace_py", "sce_commit": "b7bcab6", "authored_at": "2026-10-17"},
    }


def backward(corpus="trpc", n=1):
    q = f"error{n}.TRPCError.getTRPCErrorFromUnknown"
    return {
        "schema_version": "layer4.task.v1", "task_id": f"l4_bwd_{corpus}_{n:02d}", "family": "backward_adaptation",
        "corpus": corpus, "pinned_commit": SHA,
        "focal": {"qname": q, "file": "packages/server/src/error/TRPCError.ts", "line": 4,
                  "index_file": "error/TRPCError.ts", "language": "typescript",
                  "signature": "export function getTRPCErrorFromUnknown(cause: unknown): TRPCError {"},
        "prompt": "Implement getTRPCErrorFromUnknown so that its callers' expectations hold.",
        "answer_contract": "backward_code_v1",
        "arm_view": {"seed_symbol": q, "task_type": "debug", "oracle_symbols": [q, "error.TRPCError.TRPCError.constructor"],
                     "oracle_required_context": [], "root_imports": [], "mask": {"symbols": [q], "mode": "body"},
                     "exclude_paths": ["packages/tests/server/TRPCError.test.ts"]},
        "gold": {"source": "test_execution", "test_command": ["node", "vitest.mjs", "--run", "--no-threads", "server/TRPCError.test.ts"],
                 "cwd": "packages/tests", "timeout_s": 900, "stub_message": "STUB_IMPLEMENTATION"},
        "validity": {"trace_exit_code": 0, "async_window": False, "positive_gate": {"passed": True},
                     "negative_gate": {"passed": False, "stub_seen": True},
                     "contamination": {"arm0_score": None, "flagged": None, "seeds": []}},
        "provenance": {"tracer": "trace_node", "sce_commit": "b7bcab6", "authored_at": "2026-10-17"},
    }


def suite():
    return [f(c, n) for c in T.CORPORA for f in (forward, backward) for n in (1, 2)]


def test_valid_tasks():
    assert T.validate_task(forward()) == []
    assert T.validate_task(backward()) == []
    assert T.validate_task(forward("express")) == []


@pytest.mark.parametrize("edit, fragment", [
    (lambda t: t["arm_view"]["mask"].update(mode="body"), "mask"),  # forward never masks
    (lambda t: t.update(task_id="l4_bwd_django_01"), "task_id"),
    (lambda t: t["gold"].update(affected_symbols=[f"a.b{i}" for i in range(16)]), "gold"),
    (lambda t: t.pop("change"), "change"),
])
def test_schema_rejects(edit, fragment):
    t = forward()
    edit(t)
    errors = T.validate_task(t)
    assert errors and any(fragment in e for e in errors), errors


def test_tables_only_for_django():
    t = forward("express")
    t["gold"]["tables"] = ["x"]
    assert T.validate_task(t)


def test_semantic_rules():
    t = forward()
    t["arm_view"]["seed_symbol"] = "other"
    t["arm_view"]["oracle_symbols"] = [t["focal"]["qname"]]
    errors = T.validate_task(t)
    assert any("seed_symbol" in e for e in errors) and any("sanity ceiling" in e for e in errors)
    b = backward()
    b["arm_view"]["mask"]["symbols"] = []
    b["validity"]["negative_gate"]["stub_seen"] = False
    errors = T.validate_task(b)
    assert any("mask the focal body" in e for e in errors) and any("STUB_IMPLEMENTATION" in e for e in errors)
    d = forward()
    d["gold"]["tables"] = None
    assert any("tables" in e for e in T.validate_task(d))
    assert any("unknown to the Prism index" in e for e in T.validate_task(forward(), known={"pkg1.core.Repo.save_base"}))


def test_suite_composition():
    tasks = suite()
    assert len(tasks) == 16 and T.validate_suite(tasks) == []
    assert T.validate_suite(tasks[:-1]) and T.validate_suite(tasks + [copy.deepcopy(tasks[0])])


def test_every_arm_gets_an_evaluation_task():
    et = T.to_evaluation_task(backward())
    assert et.seed_symbol == backward()["focal"]["qname"] and et.task_type == "debug"
    assert et.adjudicated.pipeline_symbols == backward()["arm_view"]["oracle_symbols"]
    assert T.to_evaluation_task(forward()).task_type == "blast"
    assert "JSON" in T.answer_contract(forward()) and "fenced code block" in T.answer_contract(backward())


DEF = '''def total(items):
    """Sum of price x quantity."""
    subtotal = sum(i["price"] * i["qty"] for i in items)
    return round(subtotal, 2)
'''


def test_masking_and_leak_check():
    masked = T.masked_definition(DEF, "python")
    assert "Sum of price" in masked and "subtotal" not in masked and masked.startswith("def total(items):")
    task = backward()
    task["focal"]["signature"] = "def total(items):"
    ctx = f"<node>\n{DEF}</node>\nsee packages/tests/server/TRPCError.test.ts"
    leak = T.leak_check(ctx, task, DEF)
    assert leak == {"focal_body_lines": 2, "test_files": ["packages/tests/server/TRPCError.test.ts"]}
    clean = T.mask_context(ctx, DEF, masked)
    assert T.leak_check(clean, task, DEF)["focal_body_lines"] == 0


def test_gold_from_trace_and_sql():
    m = {"ancestors": {"a.save": 140, "a.create": 100, "a.rare": 1, "x.outside": 9},
         "descendants": {"a.save_table": 140}}
    assert T.gold_from_manifest(m, "upstream", min_count=2, known={"a.save", "a.create", "a.rare", "a.save_table"}) == \
        ["a.save", "a.create"]
    assert T.gold_from_manifest(m, "both", known={"a.save", "a.save_table"}) == ["a.save", "a.save_table"]
    sql = {"basic_article": {"INSERT": 50, "SELECT": 3}, "_transaction": {"SAVEPOINT": 2}}
    assert T.side_effects_from_sql(sql) == ["DB_READ", "DB_TRANSACTION", "DB_WRITE"]
    assert T.tables_from_sql(sql) == ["basic_article"]


KNOWN = {"django.db.models.base.Model.save", "django.db.models.query.QuerySet.create",
         "django.db.models.query.QuerySet.get_or_create", "django.db.models.base.Model.save_base"}


def test_name_resolution():
    assert T.resolve_name("django.db.models.base.Model.save", KNOWN) == "django.db.models.base.Model.save"
    assert T.resolve_name("Model.save()", KNOWN) == "django.db.models.base.Model.save"
    # pytest-style path: the unique `QuerySet.create` suffix decides
    assert T.resolve_name("django/db/models/query.py::QuerySet.create", KNOWN) == "django.db.models.query.QuerySet.create"
    assert T.resolve_name("QuerySet.create", KNOWN) == "django.db.models.query.QuerySet.create"
    assert T.resolve_name("save", KNOWN) is None  # one component: never guessed
    assert T.resolve_name("Foo.save", KNOWN) is None


def test_forward_scoring():
    gold = {"affected_symbols": ["django.db.models.base.Model.save", "django.db.models.query.QuerySet.create"],
            "side_effects": ["DB_READ", "DB_WRITE"], "tables": ["basic_article"]}
    reply = 'Here:\n```json\n{"affected_symbols": ["Model.save", "QuerySet.get_or_create", "Nope.x"], ' \
            '"side_effects": ["db_write", "DB_READ"], "tables": ["basic_article"]}\n```'
    s = T.score_forward(reply, gold, KNOWN)
    assert s["parsed"] and s["precision"] == round(1 / 3, 4) and s["recall"] == 0.5
    assert s["f1"] == round(2 * (1 / 3) * 0.5 / (1 / 3 + 0.5), 4) and s["unknown_names"] == ["Nope.x"]
    assert s["side_effects_exact"] is True and s["tables_exact"] is True
    many = {"affected_symbols": ["Model.save"] + [f"X.y{i}" for i in range(20)]}
    s = T.score_forward(__import__("json").dumps(many), dict(gold, side_effects=None, tables=None), KNOWN)
    assert s["truncated"] and s["side_effects_exact"] is None and s["tables_exact"] is None
    assert T.score_forward("no json here", gold, KNOWN)["f1"] == 0.0


def test_summary_and_contamination():
    tasks = {"l4_bwd_trpc_01": backward(), "l4_fwd_django_01": forward()}

    def cell(arm, tid, seed, score, status="ok", ctx=1000):
        fam = tasks[tid]["family"]
        return {"schema_version": "layer4.cell.v1", "task_id": tid, "family": fam, "corpus": tasks[tid]["corpus"],
                "arm": arm, "engine_id": arm, "seed": seed, "model": "m", "status": status,
                "tokens": {"context": ctx, "prompt": ctx + 200, "completion": 300, "llm_calls": 1},
                "leak": {"focal_body_lines": 0, "test_files": []}, "score": score}
    passed = lambda p: {"kind": "backward", "passed": p, "exit_code": 0 if p else 1, "mode": "body", "seconds": 1.0}  # noqa: E731
    fwd = lambda f: {"kind": "forward", "parsed": True, "precision": f, "recall": f, "f1": f,  # noqa: E731
                     "side_effects_exact": True, "tables_exact": False}
    cells = [cell("arm0_no_retrieval", "l4_bwd_trpc_01", s, passed(s < 3), ctx=0) for s in range(4)]
    cells += [cell("prism_ask", "l4_bwd_trpc_01", s, passed(True)) for s in range(4)]
    cells += [cell("prism_ask", "l4_bwd_trpc_01", 9, None, status="leak")]
    cells += [cell("arm0_no_retrieval", "l4_fwd_django_01", s, fwd(0.1), ctx=0) for s in range(2)]
    cells += [cell("prism_full", "l4_fwd_django_01", s, fwd(0.8)) for s in range(2)]
    for c in cells:
        assert T.schema_errors(c, T.CELL_SCHEMA) == []
    out = T.summarize(cells, tasks)
    assert out["contamination"]["l4_bwd_trpc_01"] == {"arm0_score": 0.75, "flagged": True, "seeds": 4}
    assert out["flagged_tasks"] == ["l4_bwd_trpc_01"]
    ask = out["by_family"]["backward_adaptation"]["prism_ask"]
    assert ask["excluded"]["leak"] == 1 and ask["all"]["pass_rate"] == 1.0 and "unflagged" not in ask
    assert ask["all"]["tokens_per_pass"] == 1500.0
    pf = out["by_family"]["forward_blast"]["prism_full"]["unflagged"]
    assert pf["f1"] == 0.8 and pf["side_effects_exact"] == 1.0 and pf["tables_exact"] == 0.0
