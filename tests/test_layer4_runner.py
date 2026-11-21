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


# ---------------------------------------------------------------- tournament report
def test_report_merges_corpora_and_renders_the_tables(tmp_path):
    from harness.experiments.layer4 import report as P
    tasks = R.load_tasks()

    def cell(tid, arm, seed, score, status="ok", ctx=100):
        t = tasks[tid]
        return {"schema_version": "layer4.cell.v1", "task_id": tid, "family": t["family"], "corpus": t["corpus"],
                "arm": arm, "engine_id": arm, "seed": seed, "model": "m", "status": status,
                "tokens": {"context": ctx, "prompt": ctx + 50, "completion": 50, "llm_calls": 1},
                "leak": {"focal_body_lines": 0, "test_files": []}, "score": score}
    fwd = lambda f, te=None: {"kind": "forward", "parsed": True, "precision": f, "recall": f, "f1": f,  # noqa: E731
                              "tables_exact": te, "side_effects_exact": te,
                              "tables_prf": {"f1": 1.0} if te else None, "side_effects_prf": None}
    bwd = lambda p: {"kind": "backward", "passed": p, "syntax_ok": True}  # noqa: E731
    lanes = {"express": [cell("l4_bwd_express_01", "arm0_no_retrieval", s, bwd(True)) for s in (1, 2)]
             + [cell("l4_bwd_express_01", "prism_ask", 1, bwd(True)), cell("l4_fwd_express_01", "prism_ask", 1, fwd(0.5))],
             "django": [cell("l4_fwd_django_01", "normal_oracle", 1, fwd(0.8, True)),
                        cell("l4_fwd_django_01", "prism_full", 1, None, status="leak")]}
    for corpus, rows in lanes.items():
        (tmp_path / "corpora" / corpus).mkdir(parents=True)
        (tmp_path / "corpora" / corpus / "cells.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = tmp_path / "tournament"
    assert P.merge(tmp_path / "corpora", out) == 6
    md = P.write(out, tasks, "header line")
    assert (out / "summary.json").exists() and (out / "contamination.json").exists()
    assert json.loads((out / "contamination.json").read_text())["flagged"] == ["l4_bwd_express_01"]
    assert "| prism_full | 0 | 1 | 0 | 0 | 0 |" in md  # the leak cell is counted, not scored
    assert "| normal_oracle | all | 1 | 0.800 | 0.800 | 0.800 | 1.000 | 1.000 | 1.000 |" in md
    assert "| prism_ask | all | 1 | 1.000 | 1.000 | 100 | 200 | 5.000 |" in md and "| prism_ask | unflagged |" not in md.split("## Backward")[1].split("## Contamination")[0]
    assert "| l4_bwd_express_01 | `lib.response.location` | 1.000 | 2 | **yes** |" in md


def test_sigterm_mid_splice_restores_the_file(tmp_path):
    import os
    import signal
    import sys
    import threading
    (tmp_path / "m.py").write_text("def f(x):\n    return x + 1\n")
    (tmp_path / "slow_test.py").write_text("import time\ntime.sleep(30)\n")
    task = S.SpliceTask("t", str(tmp_path), "m.py", 1, [sys.executable, "slow_test.py"], name="f", timeout=60)
    previous = signal.getsignal(signal.SIGTERM)
    R.install_sigterm_exit()
    threading.Timer(1.0, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    try:
        with pytest.raises(SystemExit):
            S.evaluate(task, "```python\nreturn 0\n```")
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert (tmp_path / "m.py").read_text() == "def f(x):\n    return x + 1\n"


def test_eval_timeout_caps_the_task_timeout():
    t = TASKS["l4_bwd_express_02"]
    st = R.splice_task(t, NS(clone_root="/c"), {}, "node", timeout=300)
    assert t["gold"]["timeout_s"] == 1800 and st.timeout == 300 and "--exit" in st.command


def test_a_seed_stopped_mid_cell_reruns_alone_and_finished_seeds_are_skipped(tmp_path):
    tasks = {"l4_bwd_express_01": TASKS["l4_bwd_express_01"]}
    ran = []

    def evaluate(splice_task, reply):
        if len(ran) == 2 and stop["armed"]:  # the third seed is killed during its evaluation
            raise SystemExit(143)
        return {"passed": True, "exit_code": 0, "seconds": 0.1, "mode": "body", "syntax_ok": True}
    stop = {"armed": True}
    original = R.run_cell

    def cell(corpus, task, arm, seed, *a, **k):
        rec = original(corpus, task, arm, seed, *a, evaluate=evaluate, build=fake_build(), **k)
        ran.append(seed)
        return rec
    R.run_cell = cell
    load = lambda name: corpus_for(TASKS["l4_bwd_express_01"])
    try:
        with pytest.raises(SystemExit):
            R.run(tasks, ["arm0_no_retrieval"], [1, 2, 3, 4], tmp_path, Cfg(), Client("```js\nreturn 1\n```"), {},
                  "node", load_corpus=load)
        assert sorted(c["seed"] for c in R.load_cells(tmp_path / "cells.jsonl").values()) == [1, 2]
        with (tmp_path / "cells.jsonl").open("a") as fh:  # and a kill mid-append cut a line short
            fh.write('{"task_id": "l4_bwd_express_01", "arm": "arm0_no')
        stop["armed"], ran[:] = False, []
        R.run(tasks, ["arm0_no_retrieval"], [1, 2, 3, 4], tmp_path, Cfg(), Client("```js\nreturn 1\n```"), {},
              "node", load_corpus=load)
    finally:
        R.run_cell = original
    assert ran == [3, 4]  # seeds 1 and 2 are not run again; the stopped seed 3 is
    cells = R.load_cells(tmp_path / "cells.jsonl")
    assert sorted(c["seed"] for c in cells.values()) == [1, 2, 3, 4] and all(c["status"] == "ok" for c in cells.values())


def test_leak_cells_from_an_older_check_are_rerun_ok_cells_kept():
    v = X.LEAK_CHECK_VERSION
    assert R.is_done({"status": "ok", "detail": {}})
    assert not R.is_done({"status": "leak", "detail": {}})  # v1 leaks: re-evaluated on resume
    assert not R.is_done({"status": "leak", "detail": {"leak_check": v - 1}})
    assert R.is_done({"status": "leak", "detail": {"leak_check": v}})
    assert not R.is_done({"status": "error", "detail": {"leak_check": v}})
    assert R.is_done({"status": "dry_run", "detail": {}}, dry_run=True) and not R.is_done({"status": "dry_run"})


def test_common_body_lines_are_those_the_repository_carries_elsewhere(tmp_path):
    import subprocess
    definition = "def f(x):\n    unique_line_of_the_focal_body = x\n    shared_line_used_elsewhere_too = 1\n"
    (tmp_path / "m.py").write_text(definition + "\n\ndef g():\n    shared_line_used_elsewhere_too = 1\n")
    (tmp_path / "n.py").write_text("unrelated = True\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    task = {"task_id": "t", "focal": {"language": "python", "signature": "def f(x):"}}
    assert X.common_body_lines(task, str(tmp_path), definition) == frozenset({"shared_line_used_elsewhere_too = 1"})


@pytest.mark.parametrize("task_id", sorted(k for k in TASKS if k.startswith("l4_bwd_")))
def test_leak_check_v2_controls_on_the_real_focal_definitions(task_id):
    """The masked definition never leaks (v1 flagged docstrings and
    multi-line signatures); the unmasked body always does, on enough lines
    unique to it."""
    task = TASKS[task_id]
    try:
        from benchmarks.corpora.resolver import resolve
        root = X.clone_root(task["corpus"], str(resolve(task["corpus"])))
        definition = X.focal_definition(task, root)
    except Exception as exc:  # corpus not checked out here
        pytest.skip(f"{task['corpus']}: {exc}")
    common = X.common_body_lines(task, root, definition)
    masked = T.masked_definition(definition, task["focal"]["language"])
    assert not X.leaked(X.context_leaks(f"<node>{masked}</node>", task, definition, common))
    unmasked = X.context_leaks(f"<node>{definition}</node>", task, definition, common)
    assert X.leaked(unmasked) and unmasked["focal_body_lines"] >= 3


def test_nodes_nested_in_the_focal_are_dropped_with_its_body():
    task = bwd_task()
    task["focal"]["index_file"] = "pkg/cart.py"
    inner = "def helper(i):\n        return i['price'] * i['qty'] * 1.2"
    definition = f"def total(items):\n    {inner}\n    return round(sum(map(helper, items)), 2)\n"
    pkg = Pkg([Node("pkg.cart.total", "pkg/cart.py", definition), Node("pkg.cart.helper", "pkg/cart.py", inner),
               Node("pkg.other.helper", "pkg/other.py", inner)], [Edge("pkg.cart.total", "pkg.cart.helper")])
    out, report = X.mask_package(pkg, task, definition, lambda f: False)
    assert report["dropped_nested_nodes"] == ["pkg.cart.helper"]  # same text in another file is not part of the body
    assert [n.id for n in out.nodes] == ["pkg.cart.total", "pkg.other.helper"] and out.edges == []
    assert "1.2" not in out.nodes[0].body
    skeleton = dataclasses.make_dataclass("Located", [("line", int), ("end_line", int)], bases=(Node,), frozen=True)
    pkg = Pkg([Node("pkg.cart.total", "pkg/cart.py", definition),
               skeleton("pkg.cart.helper", "pkg/cart.py", "def helper(i):\n    ...", 11, 12),  # compressed: not source text
               skeleton("pkg.cart.after", "pkg/cart.py", "def after():\n    ...", 14, 15)], [])
    out, report = X.mask_package(pkg, task, definition, lambda f: False, span=(10, 12))
    assert report["dropped_nested_nodes"] == ["pkg.cart.helper"] and [n.id for n in out.nodes] == ["pkg.cart.total", "pkg.cart.after"]


def test_backward_code_is_extracted_from_unterminated_and_multi_block_replies():
    fn = "res.location = function location(url) {\n  return this.set('Location', url);\n};"
    assert S.extract_code(f"```javascript\n{fn}") == fn  # closing fence eaten by a stop sequence
    assert S.extract_code(f"```javascript\n{fn}\n```\nUse it like:\n```js\nres.location('/x')\n```",
                          "javascript", "location") == fn  # the defining block, not the usage example
    assert S.extract_code(f"```js\n{fn}\n```\nthen\n```js\nres.location('/x')") == "res.location('/x')"
    assert S.extract_code("```python\nreturn 1\n```\n\nDone.") == "return 1"
    assert S.extract_code("return 1") == "return 1"


def test_backward_answers_send_no_stop_sequences_forward_keep_stage2s():
    seen = []

    class Recording(Client):
        def complete(self, *a, **k):
            seen.append(k.get("stop", "default"))
            return super().complete(*a, **k)
    R.run_cell(corpus_for(TASKS["l4_bwd_express_01"]), TASKS["l4_bwd_express_01"], "arm0_no_retrieval", 1, Cfg(),
               Recording("```js\nreturn 1\n```"), {}, "node",
               evaluate=lambda t, r: {"passed": False, "exit_code": 1, "seconds": 0, "mode": "body", "syntax_ok": True},
               build=fake_build())
    rec = R.run_cell(corpus_for(TASKS["l4_fwd_express_01"]), TASKS["l4_fwd_express_01"], "arm0_no_retrieval", 1, Cfg(),
                     Recording('{"affected_symbols": []}'), {}, "node", build=fake_build())
    assert seen == [None, "default"] and rec["status"] == "ok" and "backward_answer" not in rec["detail"]
    assert not R.is_done({"status": "ok", "family": "backward_adaptation", "detail": {}})  # v1 backward: rerun
    assert R.is_done({"status": "ok", "family": "backward_adaptation", "detail": {"backward_answer": 2}})
    assert R.is_done({"status": "ok", "family": "forward_blast", "detail": {}})


def test_client_stop_default_is_stage2s_and_none_sends_none(monkeypatch):
    from benchmarks.tsr import client as C
    sent = []

    class Completions:
        def create(self, **kw):
            sent.append(kw.get("stop"))
            raise RuntimeError("stop here")
    c = C.OpenAICompatibleClient.__new__(C.OpenAICompatibleClient)
    c._client = NS(chat=NS(completions=Completions()))
    c.model = "m"
    for kw in ({}, {"stop": None}):
        with pytest.raises(Exception):
            c.complete("m", "s", "u", **kw)
    assert sent == [list(C.STOP_SEQUENCES), None]


def test_a_full_definition_reply_does_not_repeat_the_export_or_decorators_kept_outside_the_span(tmp_path):
    import sys
    from harness.experiments.layer4.source_edit import locate
    ts = tmp_path / "share.ts"
    ts.write_text("import { x } from './x';\n\nexport function share(a: number): number {\n  return a + 1;\n}\n")
    task = S.SpliceTask("t", str(tmp_path), "share.ts", 3, [sys.executable, "-c", "pass"], name="share", timeout=30)
    result = S.evaluate(task, "```typescript\nexport function share(a: number): number {\n  return a + 2;\n}\n```")
    assert result["mode"] == "function" and result["syntax_ok"] is True  # v1: `export export function share`
    span = locate(str(ts), 3)
    assert S.drop_kept_prefix("export default function share() {}", span, b"export function share() {}") == \
        "export default function share() {}"  # only the prefix the file itself keeps is dropped
    py = tmp_path / "m.py"
    py.write_text("class C:\n    @property\n    @cached\n    def total(self):\n        return 1\n")
    pspan = locate(str(py), 4)
    reply = "@property\n@cached\ndef total(self):\n    return 2"
    assert S.drop_kept_prefix(reply, pspan, py.read_bytes()) == "def total(self):\n    return 2"
    assert S.drop_kept_prefix("@other\ndef total(self):\n    return 2", pspan, py.read_bytes()).startswith("@other")
    assert S.PREFIX_SENSITIVE.search("```ts\nexport function share() {}") and not S.PREFIX_SENSITIVE.search("```js\nreturn 1")


def test_cells_of_the_old_splice_are_rescored_from_their_stored_reply_without_a_model_call(tmp_path):
    task = TASKS["l4_bwd_express_01"]
    old = R.run_cell(corpus_for(task), task, "arm0_no_retrieval", 1, Cfg(), Client("```js\nexport function x() {}\n```"),
                     {}, "node", evaluate=lambda t, r: {"passed": False, "exit_code": 1, "seconds": 0, "mode": "body",
                                                         "syntax_ok": False}, build=fake_build())
    old["detail"]["backward_splice"] = 1
    assert R.needs_resplice(old) and not R.is_done(old)
    (tmp_path / "cells.jsonl").write_text(json.dumps(old) + "\n")

    class NoModel(Client):
        def complete(self, *a, **k):
            raise AssertionError("re-scoring must not call the model")
    original = R.reevaluate_cell
    R.reevaluate_cell = lambda corpus, t, rec, py, node: original(
        corpus, t, rec, py, node, evaluate=lambda st, reply: {"passed": True, "exit_code": 0, "seconds": 0, "mode": "function",
                                                              "syntax_ok": True})
    try:
        written = R.run({task["task_id"]: task}, ["arm0_no_retrieval"], [1], tmp_path, Cfg(), NoModel(""), {}, "node",
                        load_corpus=lambda name: corpus_for(task))
    finally:
        R.reevaluate_cell = original
    rec = written[0]
    assert rec["score"]["passed"] and rec["reply"] == old["reply"] and rec["tokens"] == old["tokens"]
    assert rec["detail"]["backward_splice"] == 2 and rec["detail"]["rescored_from_stored_reply"] and R.is_done(rec)
    plain = dict(old, reply="```js\nreturn 1\n```")
    assert not R.needs_resplice(plain) and R.is_done(plain)  # a reply v2 splices the same way is kept as it is


def test_forward_cells_of_the_refreshed_arms_rerun_on_a_new_forward_context_and_nothing_else():
    v = R.FORWARD_CONTEXT_VERSION
    old = {"status": "ok", "family": "forward_blast", "arm": "prism_full", "detail": {}}
    assert not R.is_done(old) and R.is_done({**old, "detail": {"forward_context": v}})
    assert R.is_done({**old, "arm": "prism_ask"})  # other arms keep their cells
    assert R.is_done({"status": "ok", "family": "backward_adaptation", "arm": "prism_full",
                      "detail": {"backward_answer": R.BACKWARD_ANSWER_VERSION, "backward_splice": R.BACKWARD_SPLICE_VERSION}})
    rec = R.run_cell(corpus_for(TASKS["l4_fwd_express_01"]), TASKS["l4_fwd_express_01"], "prism_full", 1, Cfg(),
                     Client('{"affected_symbols": []}'), {}, "node", build=fake_build())
    assert rec["detail"]["forward_context"] == v and R.is_done(rec)
