"""Layer 4 runner: prompt assembly, masking and leak checks, scoring with mock
model outputs, cell records, contamination (`harness/experiments/layer4/{context,runner}.py`)."""
from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from harness.experiments.layer4 import context as X
from harness.experiments.layer4 import runner as R
from harness.experiments.layer4 import splice as S
from harness.experiments.layer4 import tasks as T

pytest.importorskip("jsonschema")
TASKS = R.load_tasks()


@dataclasses.dataclass(frozen=True)
class Node:  # the NodeEntry fields mask_package uses
    id: str
    file: str
    body: str

    def model_copy(self, update):
        return dataclasses.replace(self, **update)


@dataclasses.dataclass(frozen=True)
class Edge:
    from_node: str
    to_node: str


@dataclasses.dataclass(frozen=True)
class Pkg:
    nodes: list
    edges: list

    def model_copy(self, update):
        return dataclasses.replace(self, **update)


DEF = 'def total(items):\n    subtotal = sum(i["price"] * i["qty"] for i in items)\n    return round(subtotal, 2)\n'


def bwd_task(**over):
    t = json.loads(json.dumps(TASKS["l4_bwd_fastapi_01"]))
    t["focal"].update(qname="pkg.cart.total", file="pkg/cart.py", language="python", signature="def total(items):")
    t["arm_view"]["exclude_paths"] = ["tests/test_cart.py"]
    t.update(over)
    return t


# ---------------------------------------------------------------- prompts
def test_prompt_assembly_per_family():
    fwd, bwd = TASKS["l4_fwd_express_01"], TASKS["l4_bwd_express_01"]
    system, user = X.build_prompt(fwd, "<context/>")
    assert system == X.FORWARD_SYSTEM and user.startswith("<context/>\n\nTask:\n" + fwd["prompt"])
    assert user.endswith(T.FORWARD_CONTRACT) and '"affected_symbols"' in user
    system, user = X.build_prompt(bwd, "<context/>")
    assert system == X.BACKWARD_SYSTEM and user.endswith(T.BACKWARD_CONTRACT) and "fenced code block" in user


@pytest.mark.parametrize("task_id", sorted(TASKS))
def test_no_committed_prompt_leaks_gold(task_id):
    task = TASKS[task_id]
    assert X.prompt_leaks(task["prompt"], task) == []


def test_prompt_leaks_match_whole_names_only():
    task = TASKS["l4_fwd_fastapi_02"]  # gold get_openapi is a prefix of the focal get_openapi_path
    assert "fastapi.openapi.utils.get_openapi" in task["gold"]["affected_symbols"]
    assert X.prompt_leaks("change fastapi.openapi.utils.get_openapi_path now", task) == []
    assert X.prompt_leaks("see fastapi.openapi.utils.get_openapi.", task) == ["fastapi.openapi.utils.get_openapi"]


def test_prompt_leak_detection():
    task = json.loads(json.dumps(TASKS["l4_fwd_express_01"]))
    task["prompt"] += f" Hint: look at {task['gold']['affected_symbols'][0]} (ancestor_depth 1)."
    assert set(X.prompt_leaks(task["prompt"], task)) == {task["gold"]["affected_symbols"][0], "ancestor_depth"}


# ---------------------------------------------------------------- masking + filtering
def test_mask_package_masks_the_focal_everywhere_and_drops_tests():
    task = bwd_task()
    pkg = Pkg([Node("pkg.cart.total", "pkg/cart.py", DEF),
               Node("pkg.cart", "pkg/cart.py", "import math\n\n" + DEF + "\nX = 1\n"),  # a module chunk embedding it
               Node("pkg.views.checkout", "pkg/views.py", "def checkout(items):\n    return total(items)\n"),
               Node("tests.test_cart.test_total", "tests/test_cart.py", "def test_total(): ..."),
               Node("pkg.tests_util.helper", "pkg/tests/util.py", "def helper(): ...")],
              [Edge("pkg.views.checkout", "pkg.cart.total"), Edge("tests.test_cart.test_total", "pkg.cart.total")])
    in_test = lambda f: "/tests/" in "/" + f or f.startswith("tests/")  # noqa: E731
    out, report = X.mask_package(pkg, task, DEF, in_test)
    ids = [n.id for n in out.nodes]
    assert ids == ["pkg.cart.total", "pkg.cart", "pkg.views.checkout"]
    assert report["masked_nodes"] == ["pkg.cart.total", "pkg.cart"]
    assert sorted(report["dropped_test_nodes"]) == ["pkg.tests_util.helper", "tests.test_cart.test_total"]
    for n in out.nodes[:2]:
        assert "subtotal" not in n.body and 'raise NotImplementedError("body withheld' in n.body
    assert out.edges == [Edge("pkg.views.checkout", "pkg.cart.total")]
    assert X.context_leaks("\n".join(n.body for n in out.nodes), task, DEF)["focal_body_lines"] == 0


def test_forward_tasks_are_not_masked_but_still_filtered():
    task = json.loads(json.dumps(TASKS["l4_fwd_fastapi_01"]))
    pkg = Pkg([Node(task["focal"]["qname"], "fastapi/dependencies/utils.py", "def get_dependant(): body"),
               Node("tests.test_x.t", "tests/test_x.py", "...")], [])
    out, report = X.mask_package(pkg, task, None, lambda f: f.startswith("tests/"))
    assert [n.body for n in out.nodes] == ["def get_dependant(): body"] and report["masked_nodes"] == []


def test_trpc_gating_files_are_matched_through_the_index_prefix():
    task = json.loads(json.dumps(TASKS["l4_bwd_trpc_02"]))
    gating = task["arm_view"]["exclude_paths"][0].removeprefix(X.TRPC_PREFIX + "/")  # index-relative
    assert X.is_excluded(gating, task, lambda f: False)
    assert not X.is_excluded("observable/operators/share.ts", task, lambda f: False)


def test_leak_check_sees_through_html_escaping_and_trace_keys():
    task = bwd_task()
    body = DEF.replace("<", "&lt;").replace("&", "&amp;")
    leak = X.context_leaks(f'<node id="pkg.cart.total">{body}</node> see tests/test_cart.py focal_calls', task, DEF)
    assert leak["focal_body_lines"] == 2 and leak["test_files"] == ["tests/test_cart.py"]
    assert leak["trace_markers"] == ["focal_calls"] and X.leaked(leak)


# ---------------------------------------------------------------- cells with mock model outputs
@dataclasses.dataclass
class Cfg:
    model: str = "m"
    temperature: float = 0.0
    budget: int = 8000
    token_ceiling: int = 8800
    max_tokens: int = 512
    local: bool = True
    num_ctx: int = 24576
    turn1_repeat_penalty: float = 1.15


class Client:
    def __init__(self, content):
        self.content, self.calls = content, []

    def complete(self, model, system, user, **kw):
        self.calls.append({"system": system, "user": user, **kw})
        return NS(content=self.content, model=model, response_model=model, system_fingerprint=None,
                  prompt_tokens=len(user) // 4, completion_tokens=50, latency_seconds=0.1, cost_usd=0.0)


def fake_build(leaked=False):
    def build(corpus, arm, task, seed, cfg, client, log, record):
        leak = {"focal_body_lines": 3 if leaked else 0, "test_files": [], "trace_markers": []}
        return {"rendered": "<context>ctx</context>", "selected_symbols": [task["focal"]["qname"]], "context_tokens": 7,
                "context_tokens_pre_ceiling": 7, "n_ceiling_dropped": 0, "leak": leak, "leaked": leaked,
                "engine_id": "pragmatic_oracle", "dropped_test_nodes": [], "masked_nodes": [], "focal_in_context": True}
    return build


def corpus_for(task):
    return NS(known=set(task["arm_view"]["oracle_symbols"]) | set(task["gold"].get("affected_symbols", [])),
              clone_root="/nowhere")


def test_forward_cell_scores_the_reply_against_the_gold():
    task = TASKS["l4_fwd_django_01"]
    gold = task["gold"]
    reply = json.dumps({"affected_symbols": gold["affected_symbols"][:3] + ["made.up.Name"],
                        "side_effects": gold["side_effects"], "tables": ["django_session", "auth_user"]})
    client = Client(reply)
    rec = R.run_cell(corpus_for(task), task, "normal_oracle", 42, Cfg(), client, {}, "node", build=fake_build())
    assert rec["status"] == "ok" and T.schema_errors(rec, T.CELL_SCHEMA) == []
    s = rec["score"]
    assert s["recall"] == round(3 / len(gold["affected_symbols"]), 4) and s["precision"] == 0.75
    assert s["unknown_names"] == ["made.up.Name"] and s["side_effects_exact"] is True and s["tables_exact"] is False
    assert s["tables_prf"] == {"precision": 0.5, "recall": 1.0, "f1": 0.6667}
    assert client.calls[0]["response_format"] == {"type": "json_object"}  # JSON mode for forward answers
    assert rec["tokens"]["llm_calls"] == 1 and rec["tokens"]["context"] == 7


def test_backward_cell_splices_the_reply():
    task = TASKS["l4_bwd_express_01"]
    seen = {}

    def evaluate(splice_task, reply):
        seen.update(cwd=splice_task.cwd, cmd=splice_task.command, name=splice_task.name, reply=reply)
        return {"passed": False, "exit_code": 1, "seconds": 0.4, "mode": "function", "syntax_ok": True,
                "output_tail": "  1 passing\n  2 failing\n  AssertionError: expected 'Location' header\n    at x.js:3"}
    client = Client("```js\nres.location = function location(url) { return this; };\n```")
    rec = R.run_cell(corpus_for(task), task, "prism_ask", 42, Cfg(), client, {}, "node", evaluate=evaluate,
                     build=fake_build())
    assert rec["status"] == "ok" and T.schema_errors(rec, T.CELL_SCHEMA) == []
    assert rec["score"]["passed"] is False and "AssertionError: expected 'Location' header" in rec["score"]["failure"]
    assert seen["name"] == "location" and seen["cmd"][:1] == ["node_modules/.bin/mocha"] and "fenced" not in seen["reply"]
    assert client.calls[0]["response_format"] is None  # code replies are never forced into JSON


def test_leaking_cells_are_never_sent_and_dry_runs_make_no_call():
    task = TASKS["l4_bwd_express_01"]
    client = Client("unused")
    rec = R.run_cell(corpus_for(task), task, "prism_ask", 42, Cfg(), client, {}, "node", build=fake_build(leaked=True))
    assert rec["status"] == "leak" and client.calls == [] and rec["score"] is None
    rec = R.run_cell(corpus_for(task), task, "prism_ask", 42, Cfg(), None, {}, "node", build=fake_build())
    assert rec["status"] == "dry_run" and rec["tokens"]["llm_calls"] == 0
    assert T.schema_errors(rec, T.CELL_SCHEMA) == []


def test_a_broken_cell_is_recorded_not_raised():
    def build(*a, **k):
        raise RuntimeError("index missing")
    task = TASKS["l4_fwd_express_01"]
    rec = R.run_cell(corpus_for(task), task, "prism_full", 42, Cfg(), Client("x"), {}, "node", build=build)
    assert rec["status"] == "error" and "index missing" in rec["error"] and T.schema_errors(rec, T.CELL_SCHEMA) == []


def test_unresolved_placeholders_fail_the_cell():
    task = TASKS["l4_bwd_fastapi_01"]  # {python} needs --python fastapi=...
    rec = R.run_cell(corpus_for(task), task, "normal_oracle", 42, Cfg(), Client("```python\nreturn 1\n```"), {}, "node",
                     build=fake_build())
    assert rec["status"] == "error" and "unresolved placeholder" in rec["error"]


# ---------------------------------------------------------------- the run, contamination, summary
def test_run_resumes_and_reports_contamination(tmp_path):
    tasks = {k: TASKS[k] for k in ("l4_bwd_express_01", "l4_fwd_express_01")}
    passes = iter([True, True, False])

    def evaluate(splice_task, reply):
        return {"passed": next(passes), "exit_code": 0, "seconds": 0.1, "mode": "body", "syntax_ok": True}
    calls = []

    def load(name):
        calls.append(name)
        return corpus_for(TASKS["l4_fwd_express_01"])
    client = Client(json.dumps({"affected_symbols": TASKS["l4_fwd_express_01"]["gold"]["affected_symbols"][:2]}))
    original = R.run_cell
    R.run_cell = lambda *a, **k: original(*a, evaluate=evaluate, build=fake_build())
    try:
        R.run(tasks, ["arm0_no_retrieval"], [1, 2, 3], tmp_path, Cfg(), client, {}, "node", load_corpus=load)
        again = R.run(tasks, ["arm0_no_retrieval"], [1, 2, 3], tmp_path, Cfg(), client, {}, "node", load_corpus=load)
    finally:
        R.run_cell = original
    assert again == [] and calls == ["express"]  # every cell done: the rerun loads nothing
    cells = R.load_cells(tmp_path / "cells.jsonl")
    assert len(cells) == 6 and all(T.schema_errors(c, T.CELL_SCHEMA) == [] for c in cells.values())
    report = R.contamination_report(tmp_path, tasks)
    assert report["tasks"]["l4_bwd_express_01"] == {"arm0_score": round(2 / 3, 4), "flagged": True, "seeds": 3}
    assert report["tasks"]["l4_fwd_express_01"]["flagged"] is False and report["flagged"] == ["l4_bwd_express_01"]
    s = R.summarize(tmp_path, tasks)
    assert s["status_counts"] == {"ok": 6} and s["flagged_tasks"] == ["l4_bwd_express_01"]
    assert s["by_family"]["backward_adaptation"]["arm0_no_retrieval"]["all"]["pass_rate"] == round(2 / 3, 4)


# ---------------------------------------------------------------- splice syntax check
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@needs_node
def test_splice_records_syntax_errors(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "test").mkdir()
    (tmp_path / "lib" / "m.js").write_text("function f(x) {\n  return x + 1;\n}\nmodule.exports = { f };\n")
    (tmp_path / "test" / "m.test.js").write_text(
        "const test = require('node:test');\nconst assert = require('node:assert');\n"
        "test('f', () => assert.strictEqual(require('../lib/m').f(1), 2));\n")
    task = S.SpliceTask("t", str(tmp_path), "lib/m.js", 1, ["node", "--test", "test/m.test.js"], name="f")
    good = S.evaluate(task, "```js\nreturn x + 1;\n```")
    bad = S.evaluate(task, "```js\nreturn (x + ;\n```")
    assert good["passed"] and good["syntax_ok"] is True
    assert not bad["passed"] and bad["syntax_ok"] is False
    assert T.score_backward(bad)["failure"]


# ---------------------------------------------------------------- real contexts (Express index, a few seconds)
@pytest.fixture(scope="module")
def express():
    try:
        return X.Corpus.load("express")
    except Exception as exc:  # no corpus checkout
        pytest.skip(f"express corpus unavailable: {exc}")


def stage2_args():
    from benchmarks.final_sweep.runner import CellRecord, SweepConfig, _CallLog
    cfg = SweepConfig(dry_run=True)
    return cfg, _CallLog(), CellRecord(repo="express", task_id="t", engine_id="e", seed=42, budget=cfg.budget,
                                       token_ceiling=cfg.token_ceiling, model_requested="m", temperature=0.0)


def test_real_oracle_context_masks_the_focal_body(express):
    task = TASKS["l4_bwd_express_01"]
    cfg, log, rec = stage2_args()
    ctx = X.build_context(express, "normal_oracle", task, 42, cfg, None, log, rec)
    assert ctx["focal_in_context"] and ctx["masked_nodes"] == [task["focal"]["qname"]] and not ctx["leaked"]
    assert "body withheld: implement it" in ctx["rendered"] and "encodeUrl(loc)" not in ctx["rendered"]


def test_real_context_without_masking_is_caught_by_the_leak_check(express, monkeypatch):
    task = TASKS["l4_bwd_express_01"]
    cfg, log, rec = stage2_args()
    monkeypatch.setattr(X, "mask_package", lambda pkg, *a: (pkg, {"dropped_test_nodes": [], "masked_nodes": []}))
    ctx = X.build_context(express, "normal_oracle", task, 42, cfg, None, log, rec)
    assert ctx["leaked"] and ctx["leak"]["focal_body_lines"] > 0  # the negative control: the check does fire


def test_real_retrieval_context_drops_test_files(express):
    task = TASKS["l4_fwd_express_02"]
    cfg, log, rec = stage2_args()
    ctx = X.build_context(express, "prism_ask", task, 42, cfg, None, log, rec)
    assert not ctx["leaked"] and all(not s.startswith("test.") for s in ctx["selected_symbols"])
