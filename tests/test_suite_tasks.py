"""Task suite (`harness.experiments.suite`): miners' filters, prompts and scorers, without a model."""
from __future__ import annotations

import json

from harness.experiments.suite import bugs, newfn
from harness.experiments.suite.mine_bugs import mask
from harness.experiments.suite.mine_newfn import jsdoc_before, split_signature


def test_bug_description_masks_answer_names_issue_refs_and_trailers():
    text = mask("Fix FileStorage.__iter__ looping forever (#123)\n\nSee https://x.y/z\nCo-authored-by: A <a@b>",
                {"FileStorage", "__iter__", "datastructures"})
    assert "FileStorage" not in text and "#123" not in text and "https" not in text and "Co-authored" not in text
    assert text.startswith("Fix <name>.<name> looping forever")


def test_bug_answers_match_by_leaf_class_and_file():
    gold = {"file": "src/werkzeug/datastructures.py", "name": "FileStorage.__iter__"}
    assert bugs.matches({"function": "FileStorage.__iter__", "file": "src/werkzeug/datastructures.py"}, gold)
    assert bugs.matches({"function": "__iter__()"}, gold)
    assert not bugs.matches({"function": "OtherClass.__iter__"}, gold)
    assert not bugs.matches({"function": "FileStorage.__iter__", "file": "src/werkzeug/wsgi.py"}, gold)
    task = {"gold": [gold, {"file": "a.py", "name": "helper"}]}
    raw = json.dumps({"functions": [{"function": "helper", "file": "a.py"}, {"function": "FileStorage.__iter__"}]})
    got = bugs.score_task(task, raw)
    assert got["top1"] and got["recall5"] == 1.0 and bugs.score_task(task, "not json")["answers"] == 0


def test_newfn_signature_docstring_and_jsdoc():
    sig, doc, body = split_signature('    def f(self, a):\n        """Do a thing to a."""\n        return g(a)\n', "python")
    assert sig.strip() == "def f(self, a):" and doc == "Do a thing to a." and "return g(a)" in body
    src = "const a = 1\n/**\n * Merge two configs into one.\n * @param {Object} x\n */\nfunction merge(x) {\n}\n"
    assert jsdoc_before(src, 6) == "Merge two configs into one."
    assert jsdoc_before("x\nfunction merge(x) {}\n", 2) == ""


def test_newfn_existing_helpers_and_scores():
    code = "def go(self, key):\n    k = normalize_key(key)\n    v = self.get_entry(k)\n    data.update(v)\n    return format(v)\n"
    helpers = newfn.existing_helpers(code, {"normalize_key", "update", "unused"}, {"get_entry"})
    assert helpers == ["get_entry", "normalize_key"]  # `update` is generic, `format` a builtin
    task = {"existing_helpers": helpers, "class_methods": ["get_entry"], "class_qname": "m.Cache", "file": "m.py",
            "repo_leaves": ["normalize_key", "get_entry"], "file_imports": ["os"]}
    good = newfn.score_task(task, json.dumps({"code": code}))
    assert good["reuse"] == 1.0 and good["invented"] == 0 and good["parses"]
    bad = "def go(self, key):\n    v = self.fetch_entry(key)\n    return make_key(v)\n"
    got = newfn.score_task(task, json.dumps({"code": bad}))
    assert got["reuse"] == 0.0 and got["invented"] == 2
    assert newfn.score_task(task, "{}")["answered"] is False


def test_preregistered_statistics():
    from harness.experiments.suite import stats
    m = stats.mcnemar([True] * 5 + [False] * 15, [True] * 5 + [True] * 12 + [False] * 3)
    assert (m["only_a"], m["only_b"]) == (0, 12) and m["p"] < 0.001
    assert stats.mcnemar([True, False], [True, False])["p"] == 1.0
    assert stats.wilcoxon([0.1, 0.2], [0.1, 0.2]) is None
    w = stats.wilcoxon([0.0] * 10, [0.5, 0.4, 0.6, 0.7, 0.2, 0.3, 0.8, 0.9, 0.1, 0.5])
    assert w["p"] < 0.01
    b = stats.bootstrap_diff([0, 0, 1, 1], [1, 1, 1, 1], groups=["t1", "t1", "t2", "t2"], resamples=2000)
    assert b["tasks"] == 2 and b["mean"] == 1.0 and b["low"] <= b["mean"] <= b["high"]
    h = stats.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert h == {"a": 0.03, "c": 0.06, "b": 0.06}


def test_testgen_salvage_strips_fences_and_keeps_complete_tests_of_a_cut_answer():
    from harness.experiments.suite.testgen import answer_code
    fenced = json.dumps({"code": "```python\nimport x\n\ndef test_a():\n    assert x.f() == 1\n```"})
    assert answer_code(fenced) .startswith("```") and answer_code(fenced, salvage=True).strip().startswith("import x")
    cut = '{"code": "import x\\n\\ndef test_a():\\n    assert 1\\n\\ndef test_b():\\n    assert x.g(1,'
    assert answer_code(cut) == ""
    got = answer_code(cut, salvage=True)
    assert "def test_a" in got and "test_b" not in got


def test_outer_fence_only_the_wrapper_goes():
    from harness.experiments.suite.fences import strip_outer_fence
    code = "def f():\n    return 1"
    assert strip_outer_fence(code) == code                                   # no fence: unchanged
    assert strip_outer_fence(f"```\n{code}\n```") == code
    assert strip_outer_fence(f"```python\n{code}\n```\n") == code
    assert strip_outer_fence(f"```python\n{code}") == code                   # cut off: no closing fence
    md = 'def test_render():\n    text = """\n```python\nx = 1\n```\n"""\n    assert render(text)'
    assert strip_outer_fence(md) == md                                       # fences inside a string: code
    assert strip_outer_fence(f"```python\n{md}\n```") == md
    split = f"```python\n{code}\n```\nand then\n```python\n{code}\n```"
    assert "```" in strip_outer_fence(split)                                 # several blocks: still broken
    assert strip_outer_fence(strip_outer_fence(f"```\n{code}\n```")) == code  # once is enough


def test_newfn_outer_fence_scoring_is_opt_in():
    task = {"existing_helpers": ["helper"], "class_methods": [], "repo_leaves": ["helper"], "file": "m.py"}
    raw = json.dumps({"code": "```python\ndef new():\n    return helper()\n```"})
    assert newfn.score_task(task, raw)["parses"] is False
    assert newfn.score_task(task, raw, outer_fence=True)["parses"] is True
    assert newfn.score_task(task, "```json\n" + raw + "\n```", outer_fence=True)["reuse"] == 1.0
