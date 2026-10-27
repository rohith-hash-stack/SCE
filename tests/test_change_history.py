"""Change-propagation sanity run (harness/experiments/change_history): mining helpers, the because-of-the-
anchor filter, prompts and scoring - no model calls."""
import json

from harness.experiments.change_history import mine, run, select

TASK = {
    "id": "ch01", "repo": "toy", "tier": "medium", "commit": "c1", "parent": "p1", "subject": "s",
    "anchor": "pkg.ports.normalize_port", "anchor_file": "pkg/ports.py", "anchor_lines": [1, 4],
    "anchor_old": "def normalize_port(port):\n    return int(port)\n",
    "anchor_new": "def normalize_port(port, scheme):\n    return None if port == 80 else int(port)\n",
    "anchor_file_text": "def normalize_port(port):\n    return int(port)\n",
    "changed_callers": [
        {"qname": "pkg.urls.URL.port", "file": "pkg/urls.py", "name": "URL.port",
         "old": "def port(self):\n    return normalize_port(self._port)\n",
         "new": "def port(self):\n    return normalize_port(self._port, self.scheme)\n"},
    ],
    "unchanged_callers": ["pkg.cli.show"],
    "prism_context": "# Change context: `pkg.ports.normalize_port`\n## 4. Who depends on it\n",
}


def test_functions_and_changed_functions_ignore_comments(tmp_path):
    src = b"class A:\n    def f(self):\n        # note\n        return 1\n\n\ndef g():\n    return 2\n"
    names = mine.functions("m.py", src)
    assert set(names) == {"A.f", "g"} and names["A.f"]["start"] == 2
    assert mine._normalized("x = 1  # a\n", "python") == mine._normalized("x  =  1\n", "python")


def test_tiers():
    assert mine.tier("a.py", [{"file": "a.py"}]) == "easy"
    assert mine.tier("a.py", [{"file": "b.py"}]) == "medium"
    assert mine.tier("a.py", [{"file": "b.py"}, {"file": "c.py"}]) == "hard"
    assert mine.tier("a.py", [{"file": "a.py"}] * 3) == "hard"


def test_caller_counts_only_when_its_change_involves_the_anchor():
    same_params = {**TASK, "anchor_new": "def normalize_port(port):\n    return int(port) or None\n"}
    related = TASK["changed_callers"][0]
    unrelated = {"old": "def port(self):\n    x = normalize_port(self._port)\n    return x\n",
                 "new": "def port(self):\n    x = normalize_port(self._port)\n    return x or 0\n"}
    assert select.because_of_anchor(same_params, related)
    assert not select.because_of_anchor(same_params, unrelated)
    assert select.because_of_anchor(TASK, unrelated)  # the anchor's parameters changed


def test_prompts_differ_only_by_the_prism_block():
    file_prompt, prism_prompt = run.prompt(TASK, "FILE"), run.prompt(TASK, "PRISM")
    assert prism_prompt.startswith(file_prompt) and "PRISM change context" in prism_prompt
    assert "PRISM" not in file_prompt and "def normalize_port(port, scheme)" in file_prompt


def test_scoring_against_the_commit():
    answer = json.dumps({"changes": [
        {"function": "normalize_port", "file": "pkg/ports.py", "new_code": "ignored: the anchor"},
        {"function": "URL.port", "file": "pkg/urls.py",
         "new_code": "def port(self):\n    return normalize_port(self._port, self.scheme)\n"},
        {"function": "show", "file": "pkg/cli.py", "new_code": "def show(:"},
    ]})
    s = run.score_task(TASK, answer)
    assert s["found"] == ["pkg.urls.URL.port"] and s["missed"] == []
    assert s["extra"] == ["show"] and s["extra_unchanged_callers"] == ["show"]
    assert s["edits"] == [{"caller": "pkg.urls.URL.port", "added_kept": 1.0, "removed_gone": 1.0, "parses": True}]
    miss = run.score_task(TASK, "not json at all")
    assert miss["found"] == [] and miss["missed"] == ["pkg.urls.URL.port"]


def test_wrong_class_is_not_a_match():
    answer = json.dumps({"changes": [{"function": "Other.port", "new_code": "def port(self): pass"}]})
    assert run.score_task(TASK, answer)["found"] == []


def test_a_same_named_caller_in_another_file_is_not_the_anchor():
    task = {**TASK, "anchor": "pkg.app.App.redirect", "anchor_file": "pkg/app.py",
            "changed_callers": [{"qname": "pkg.helpers.redirect", "file": "pkg/helpers.py", "name": "redirect",
                                 "old": "def redirect(code=302):\n    pass\n", "new": "def redirect(code=303):\n    pass\n"}]}
    other_file = json.dumps({"changes": [{"function": "redirect", "file": "pkg/helpers.py",
                                          "new_code": "def redirect(code=303):\n    pass\n"}]})
    assert run.score_task(task, other_file)["found"] == ["pkg.helpers.redirect"]
    same_file = json.dumps({"changes": [{"function": "redirect", "file": "pkg/app.py", "new_code": "x"}]})
    assert run.score_task(task, same_file)["found"] == []  # the model restating the anchor itself


def test_prism_map_prompt_adds_only_the_map_and_the_second_ring():
    plan = {"repo": "toy", "readme": "", "folders": [["pkg", 3]], "folder_files": {"pkg": ["pkg/ports.py"]},
            "files": ["pkg/ports.py"], "outlines": {"pkg/ports.py": "function normalize_port"},
            "ring": [{"qname": "pkg.cli.main", "relation": "caller of a caller", "via": "pkg.urls.URL.port",
                      "file": "pkg/cli.py", "line": 3, "card": "Qualified Name: pkg.cli.main"}]}
    task = {**TASK, "overview_plan": plan}
    text = {"repo": "A toy.", "folders": {"pkg": "Ports."}, "files": {"pkg/ports.py": "Port parsing."},
            "ring": {"pkg.cli.main": "Runs the command line."}}
    assert run.arms_for([task]) == ("FILE", "PRISM", "PRISM_MAP") and run.arms_for([TASK]) == ("FILE", "PRISM")
    with_map = run.prompt(task, "PRISM_MAP", text)
    assert with_map.startswith(run.prompt(task, "FILE"))
    assert TASK["prism_context"] in with_map and "## 0. Map" in with_map and "Runs the command line." in with_map
    assert "## 0. Map" not in run.prompt(task, "PRISM")
