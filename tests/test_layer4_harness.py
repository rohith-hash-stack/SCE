"""Layer 4 harness: source edits, the splice harness and its validity gates,
contamination flagging, and the Node tracer (`harness/experiments/layer4/`)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
from pathlib import Path

import pytest

from harness.experiments.layer4 import splice as S
from harness.experiments.layer4.source_edit import edited, instrument_js, locate, stub
from harness.experiments.layer4.trace_node import RUNTIME, run_traced

PY_LIB = '''
def helper(x):
    return x


class Cart:
    @staticmethod
    def total(items):
        """Sum of price * qty."""
        return sum(helper(i["price"]) * i["qty"] for i in items)
'''
PY_TEST = '''
from shop.cart import Cart


def test_total():
    assert Cart.total([{"price": 2, "qty": 3}, {"price": 1, "qty": 1}]) == 7


def test_empty():
    assert Cart.total([]) == 0
'''
JS_LIB = '''
function total(items) {
  return items.reduce((s, i) => s + i.price * i.qty, 0);
}
module.exports = { total };
'''
JS_TEST = '''
const test = require('node:test');
const assert = require('node:assert');
const { total } = require('../lib/cart');
test('total', () => { assert.strictEqual(total([{ price: 2, qty: 3 }]), 6); });
'''


@pytest.fixture
def py_task(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "__init__.py").write_text("")
    (tmp_path / "shop" / "cart.py").write_text(textwrap.dedent(PY_LIB))
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_cart.py").write_text(textwrap.dedent(PY_TEST))
    return S.SpliceTask("py", str(tmp_path), "shop/cart.py", 8,
                        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], name="total",
                        env={"PYTHONPATH": str(tmp_path)})


@pytest.fixture
def js_task(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "cart.js").write_text(textwrap.dedent(JS_LIB))
    (tmp_path / "test").mkdir()
    (tmp_path / "test" / "cart.test.js").write_text(textwrap.dedent(JS_TEST))
    return S.SpliceTask("js", str(tmp_path), "lib/cart.js", 2, ["node", "--test", "test/cart.test.js"], name="total")


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def test_python_gates_and_candidates(py_task):
    original = Path(py_task.path).read_bytes()
    g = S.gates(py_task)
    assert g["valid"] and g["positive"]["passed"] and not g["negative"]["passed"]
    good_full = "```python\ndef total(items):\n    return sum(i['price'] * i['qty'] for i in items)\n```"
    good_body = "return sum(i['price'] * i['qty'] for i in items)"
    wrong = "def total(items):\n    return len(items)"
    broken = "def total(items:\n    return 1"
    r = S.evaluate(py_task, good_full)
    assert r["passed"] and r["mode"] == "function"
    r = S.evaluate(py_task, good_body)
    assert r["passed"] and r["mode"] == "body"
    assert not S.evaluate(py_task, wrong)["passed"]
    assert "syntax error" in S.evaluate(py_task, broken)["output_tail"]
    assert Path(py_task.path).read_bytes() == original  # restored after every run


def test_a_task_whose_tests_do_not_exercise_the_target_is_invalid(py_task):
    py_task.command = [sys.executable, "-c", "pass"]  # a "test subset" that never calls the target
    g = S.gates(py_task)
    assert g["positive"]["passed"] and g["negative"]["passed"] and not g["valid"]


def test_contamination_flag(py_task):
    replies = {0: "return sum(i['price'] * i['qty'] for i in items)", 1: "return 0", 2: "return sum(i['qty'] for i in items)"}
    res = S.contamination(py_task, lambda seed: replies[seed], [0, 1, 2], threshold=0.5)
    assert res["passed"] == [True, False, False] and not res["flagged"]
    assert S.contamination(py_task, lambda seed: replies[0], [0, 1], threshold=0.5)["flagged"]


@needs_node
def test_javascript_gates_and_candidates(js_task):
    original = Path(js_task.path).read_bytes()
    assert S.gates(js_task)["valid"]
    assert S.evaluate(js_task, "```js\nfunction total(items) {\n  let s = 0;\n  for (const i of items) s += i.price * i.qty;\n  return s;\n}\n```")["passed"]
    assert S.evaluate(js_task, "return items.length;")["passed"] is False
    assert Path(js_task.path).read_bytes() == original


def test_edits_are_restored_even_on_error(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("def f():\n    return 1\n")
    with pytest.raises(RuntimeError, match="boom"):
        with edited(str(f)):
            stub(locate(str(f), 1))
            raise RuntimeError("boom")
    assert f.read_text() == "def f():\n    return 1\n"


@needs_node
def test_node_tracer_records_callers_and_callees(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "core.js").write_text(textwrap.dedent('''
        function leaf(x) { return x + 1; }
        function focal(x) {
          return [1, 2].map((v) => leaf(v + x));
        }
        function api(x) { return focal(x); }
        function unrelated() { return 0; }
        module.exports = { api, unrelated };
    '''))
    (tmp_path / "main.js").write_text("const c = require('./lib/core');\nc.unrelated();\nc.api(1);\nc.api(2);\n")
    out = tmp_path / "raw.json"
    raw = run_traced(str(tmp_path), "lib/core.js", 3, ["node", "--require", str(RUNTIME), "main.js"], str(out))
    assert raw["exit_code"] == 0 and raw["focal_calls"] == 2
    # main.js's module-level code is a caller too (`Object.<anonymous>`); mapping drops non-functions
    assert {k.split(":")[2] for k in raw["ancestors"]} == {"api", "<anonymous>"}
    names = {k.split(":")[2] for k in raw["descendants"]}
    assert "leaf" in names and "unrelated" not in names and "focal" not in names
    assert "focal(x) {\n  return" in (tmp_path / "lib" / "core.js").read_text()  # restored
    assert json.loads(out.read_text())["focal_file"] == "lib/core.js"


def test_instrumentation_keeps_line_numbers(tmp_path):
    f = tmp_path / "a.ts"
    f.write_text("export const f = async (x: number) =>\n  x + 1;\nfunction g() { return 2; }\n")
    before = f.read_text().count("\n")
    with edited(str(f)):
        instrument_js(locate(str(f), 1), "E()", "X")
        assert f.read_text().count("\n") == before
        assert "function g()" in f.read_text().splitlines()[2]
    os.remove(f)
