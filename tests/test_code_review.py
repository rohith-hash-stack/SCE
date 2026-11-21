"""The code-review score (`harness/experiments/r4/code_review.py`): deterministic, descriptive checks of a run's final
code against the start state and the developer's change."""
import os
import subprocess

import pytest

pytest.importorskip("pyflakes")

from harness.experiments.r4.code_review import calls, checks, is_escape, review  # noqa: E402

START = {
    "pkg/__init__.py": "",
    "pkg/render.py": "from html import escape\n\n\ndef plain_text(exc):\n    return str(exc)\n\n\n"
                     "def page(exc):\n    return '<p>' + escape(str(exc)) + '</p>'\n",
    "pkg/ui.js": "const a = 1;\n",
    "tests/test_render.py": "def test_x():\n    pass\n",
}
GOLD = {
    "pkg/render.py": START["pkg/render.py"].replace(
        "    return '<p>' + escape(str(exc)) + '</p>'\n",
        "    text = plain_text(exc)\n    return '<p>' + escape(str(exc)) + '</p><pre>' + escape(text) + '</pre>'\n"),
    "pkg/ui.js": "const a = 1;\n\nfunction copyText() {\n  navigator.clipboard.writeText(a);\n}\n",
}


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _write(root, files):
    for p, text in files.items():
        os.makedirs(os.path.dirname(os.path.join(root, p)) or root, exist_ok=True)
        open(os.path.join(root, p), "w").write(text)


@pytest.fixture
def repo(tmp_path):
    root = str(tmp_path / "r")
    os.makedirs(root)
    _git(root, "init", "-q")
    _write(root, START)
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "start")
    parent = _git(root, "rev-parse", "HEAD")
    _write(root, GOLD)
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "gold")
    commit = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "-q", parent)
    task = {"parent": parent, "commit": commit, "gold_files": ["pkg/render.py", "pkg/ui.js"],
            "test_files": ["tests/test_render.py"], "new_functions": ["pkg/ui.js::copyText"]}
    start_tree = _git(root, "rev-parse", f"{parent}^{{tree}}")
    return root, task, start_tree


def _agent(root, render_body, js):
    src = START["pkg/render.py"].replace("    return '<p>' + escape(str(exc)) + '</p>'\n", render_body)
    _write(root, {"pkg/render.py": src, "pkg/ui.js": js, "tests/test_render.py": "def test_y():\n    pass\n"})


def test_the_developers_own_change_scores_full(repo):
    root, task, start = repo
    _write(root, GOLD)
    rv = review(task, root, start)
    assert rv["source_files"] == ["pkg/render.py", "pkg/ui.js"]          # the test file is left out
    assert rv["helpers"] == ["plain_text"] and rv["helper_reuse"] == 1.0
    assert rv["escaping_expected"] == ["escape"] and rv["escaping_kept"] is True
    assert rv["apis"] == ["navigator.clipboard.writeText"] and rv["api_match"] == 1.0   # copyText: its own function
    c = checks(rv, {"pass_to_pass_broken": 0})
    assert c["review_score"] == 1.0 and c["no_new_undefined_names"] == 1.0


def test_missing_import_no_reuse_no_escape(repo):
    root, task, start = repo
    _agent(root, "    text = ''.join(traceback.format_exception(exc))\n    return '<pre>' + text + '</pre>'\n",
           "const a = 1;\nfunction copyText() {\n  document.execCommand('copy');\n}\n")
    rv = review(task, root, start)
    assert rv["new_undefined_names"] == ["pkg/render.py: traceback"]
    assert rv["helper_reuse"] == 0.0 and rv["api_match"] == 0.0
    assert rv["escaping_kept"] is False         # only the agent's added lines count, not the file's untouched ones
    c = checks(rv, {"pass_to_pass_broken": 1})
    assert c["no_regressions"] == 0.0 and c["review_score"] < 0.5


def test_syntax_errors_python_and_js(repo):
    root, task, start = repo
    _agent(root, "    return '<p>' + escape(str(exc)\n", "const a = 1;\nfunction copyText( {\n")
    rv = review(task, root, start)
    assert rv["new_syntax_errors"] == ["pkg/render.py", "pkg/ui.js"]
    assert rv["new_undefined_names"] == []      # an unparsable file is reported as a syntax error only


def test_no_source_change_is_not_reviewed(repo):
    root, task, start = repo
    _write(root, {"tests/test_render.py": "def test_z():\n    pass\n"})
    rv = review(task, root, start)
    assert rv == {"source_files": []}
    assert set(checks(rv, {"pass_to_pass_broken": 0}).values()) == {None}


def test_new_file_counts_as_added(repo):
    root, task, start = repo
    _write(root, {"pkg/extra.js": "export function copyText() {\n  navigator.clipboard.writeText('x');\n}\n"})
    rv = review(task, root, start)
    assert "pkg/extra.js" in rv["source_files"] and rv["api_match"] == 1.0


def test_call_extraction():
    assert calls("x = self.helper(a)\nif (y) { this.go(1); }\nreturn foo.bar.baz(2)") == {"helper", "go",
                                                                                          "foo.bar.baz"}
    assert is_escape("html.escape") and is_escape("sanitizeHtml") and not is_escape("format")
