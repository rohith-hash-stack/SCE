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
from harness.experiments.layer4.trace_node import RUNTIME, map_manifest, merge, rebase, run_traced

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
    g = S.gates(js_task)
    assert g["valid"] and g["negative"]["stub_seen"]  # the failure is the stub's STUB_IMPLEMENTATION
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


_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def _vlq(*values: int) -> str:
    out = ""
    for n in values:
        v = (-n << 1) | 1 if n < 0 else n << 1
        while True:
            digit, v = v & 31, v >> 5
            out += _B64[digit | (32 if v else 0)]
            if not v:
                break
    return out


def _compiled(tmp_path, name: str, inline: bool) -> None:
    """dist/<name>.js as a bundler would emit it: `helper` on generated line
    2, mapped to src/<name>.ts line 7 by an inline or external source map."""
    import base64
    smap = json.dumps({"version": 3, "sources": [f"../src/{name}.ts"], "names": [],
                       "mappings": ";" + _vlq(0, 0, 6, 0) + ";"})
    ref = "data:application/json;base64," + base64.b64encode(smap.encode()).decode() if inline else f"{name}.js.map"
    (tmp_path / "dist").mkdir(exist_ok=True)
    (tmp_path / "dist" / f"{name}.js").write_text(
        f'"use strict";\nfunction helper(x) {{ return x * 2; }}\nexports.helper = helper;\n//# sourceMappingURL={ref}\n')
    if not inline:
        (tmp_path / "dist" / f"{name}.js.map").write_text(smap)


@needs_node
def test_node_tracer_maps_coverage_through_source_maps(tmp_path):
    _compiled(tmp_path, "inl", inline=True)
    _compiled(tmp_path, "ext", inline=False)
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "core.js").write_text(
        "const a = require('../dist/inl');\nconst b = require('../dist/ext');\n"
        "function focal(x) {\n  return a.helper(x) + b.helper(x);\n}\nmodule.exports = { focal };\n")
    (tmp_path / "main.js").write_text("require('./lib/core').focal(1);\n")
    raw = run_traced(str(tmp_path), "lib/core.js", 3, ["node", "--require", str(RUNTIME), "main.js"],
                     str(tmp_path / "raw.json"))
    assert raw["exit_code"] == 0 and raw["focal_calls"] == 1
    # the original .ts line, not dist/*.js line 2
    assert set(raw["descendants"]) == {"src/inl.ts:7:helper", "src/ext.ts:7:helper"}
    assert raw["source_mapping"]["mapped"] >= 2
    assert raw["raw_stack"] and raw["raw_stack"][0].endswith("lib/core.js:3:focal")


@needs_node
def test_node_tracer_drops_vite_node_export_getters(tmp_path):
    # what vite-node's SSR transform runs: the export as a generated live-binding getter
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "dep.js").write_text(
        "const __vi_ssr_exports__ = {};\nfunction helper(x) { return x + 1; }\n"
        'Object.defineProperty(__vi_ssr_exports__, "helper", { enumerable: true, configurable: true, get(){ return helper }});\n'
        "module.exports = __vi_ssr_exports__;\n")
    (tmp_path / "lib" / "core.js").write_text(
        "const dep = require('./dep');\nfunction focal(x) {\n  return dep.helper(x);\n}\nmodule.exports = { focal };\n")
    (tmp_path / "main.js").write_text("require('./lib/core').focal(1);\n")
    raw = run_traced(str(tmp_path), "lib/core.js", 2, ["node", "--require", str(RUNTIME), "main.js"],
                     str(tmp_path / "raw.json"))
    assert raw["exit_code"] == 0
    assert raw["descendants"] == {"lib/dep.js:2:helper": 1}  # the getter itself is not a callee
    assert raw["source_mapping"]["generated_getters"] == 1


@needs_node
def test_node_tracer_merges_forked_processes(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "core.js").write_text(
        "function leaf(x) { return x; }\nfunction focal(x) {\n  return leaf(x);\n}\nmodule.exports = { focal };\n")
    (tmp_path / "child.js").write_text("require('./lib/core').focal(1);\n")
    (tmp_path / "main.js").write_text(  # the parent never runs the focal function: no file from it
        "const { fork } = require('node:child_process');\n"
        "for (let i = 0; i < 2; i++) fork('./child.js').on('exit', (c) => { if (c) process.exit(c); });\n")
    env = {"NODE_OPTIONS": f"--require {RUNTIME}"}
    raw = run_traced(str(tmp_path), "lib/core.js", 2, ["node", "main.js"], str(tmp_path / "raw.json"), env=env, multi=True)
    assert raw["exit_code"] == 0 and raw["processes"] == 2 and raw["focal_calls"] == 2
    assert raw["descendants"] == {"lib/core.js:1:leaf": 2}
    assert merge([])["focal_calls"] == 0


def test_unmapped_build_output_resolves_by_name_to_the_package_source():
    from types import SimpleNamespace as NS
    fn = lambda q, f, a, b: NS(qualified_name=q, kind="function", file=f, line_range=(a, b))  # noqa: E731
    builder = NS(repo_root="/r", symbol_table=[
        fn("server.observable.observable.observable", "packages/server/src/observable/observable.ts", 10, 40),
        fn("server.observable.operators.map", "packages/server/src/observable/operators.ts", 1, 9),
        fn("client.operators.map", "packages/client/src/operators.ts", 1, 9),
        fn("client.links.createChain", "packages/client/src/links/createChain.ts", 10, 40)])
    raw = {"focal_calls": 1, "ancestors": {}, "descendants": {
        "packages/server/dist/observable/index.mjs:4:observable": 1,
        "packages/server/dist/observable/index.mjs:30:map": 2,  # one `map` in server/src: resolved
        "packages/server/dist/observable/index.mjs:50:<anonymous>": 1,
        "packages/client/src/links/createChain.ts:19:execute": 1}}  # nested: folds into the focal
    m = map_manifest(raw, builder, "client.links.createChain")
    assert m["descendants"] == {"server.observable.observable.observable": 1, "server.observable.operators.map": 2}
    assert m["descendants_unmapped"] == {"packages/server/dist/observable/index.mjs:50:<anonymous>": 1}
    assert set(m["resolved_by_name"]) == {"packages/server/dist/observable/index.mjs:4:observable",
                                          "packages/server/dist/observable/index.mjs:30:map"}


def test_rebase_to_a_corpus_indexed_below_the_clone_root():
    raw = {"ancestors": {"packages/server/src/a.ts:3:f": 1, "packages/client/src/b.ts:4:g": 2},
           "descendants": {"packages/server/dist/observable/index.mjs:9:observable": 1}}
    r = rebase(raw, "packages/server/src")
    assert r["ancestors"] == {"a.ts:3:f": 1} and r["outside_ancestors"] == {"packages/client/src/b.ts:4:g": 2}
    assert r["descendants"] == {"dist/observable/index.mjs:9:observable": 1}


def test_stub_message_is_stub_implementation(tmp_path):
    f = tmp_path / "a.ts"
    f.write_text("export function f(x: number): number {\n  return x + 1;\n}\n")
    with edited(str(f)):
        stub(locate(str(f), 1))
        assert 'throw new Error("STUB_IMPLEMENTATION");' in f.read_text()
    p = tmp_path / "m.py"
    p.write_text("def f():\n    return 1\n")
    with edited(str(p)):
        stub(locate(str(p), 1))
        assert "raise NotImplementedError('STUB_IMPLEMENTATION')" in p.read_text()


def test_instrumentation_keeps_line_numbers(tmp_path):
    f = tmp_path / "a.ts"
    f.write_text("export const f = async (x: number) =>\n  x + 1;\nfunction g() { return 2; }\n")
    before = f.read_text().count("\n")
    with edited(str(f)):
        instrument_js(locate(str(f), 1), "E()", "X")
        assert f.read_text().count("\n") == before
        assert "function g()" in f.read_text().splitlines()[2]
    os.remove(f)
