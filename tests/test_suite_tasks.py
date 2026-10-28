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
