"""Phase 2, Phase D (`harness/experiments/r4/PHASE2_PLAN.md`): `prism_search`, `prism_read`, and the PRISM_ONLY arm.

- `prism_search`: `grep` over the working files, each hit labelled with what it is, the definition it is in, and its
  file's kind, from the current text (an edit's new line is found and labelled at once);
- `prism_read`: `read_file`'s exact output (any file, current bytes, numbered lines), then PRISM's one-line footer;
- PRISM_ONLY: PRISM's tools with these two in place of `grep` and `read_file`.
"""
import json
import os
import subprocess

import pytest

FILES = {
    "pkg/__init__.py": "",
    "pkg/core.py": (
        "import os\n"
        "from functools import wraps\n\n\n"
        "def helper(x):\n"
        "    # helper is the shared step\n"
        "    return x\n\n\n"
        "class Base:\n    pass\n\n\n"
        "class Boom(Exception):\n    pass\n\n\n"
        "def deco(f):\n    return f\n\n\n"
        "class Child(Base):\n"
        "    @deco\n"
        "    def run(self, v: Base) -> int:\n"
        "        if isinstance(v, Base):\n"
        "            pass\n"
        "        try:\n"
        "            pass\n"
        "        except Boom:\n"
        "            pass\n"
        "        label = \"helper\"\n"
        "        return helper(1)\n"
    ),
    "pkg/templates/page.html": "<p>{{ helper }}</p>\n",
    "pkg/settings.toml": "[tool]\nhelper = true\n",
    "web/a.ts": "import { helper } from './b';\n\nexport function useIt(): number {\n  return helper(2);\n}\n",
    "web/b.ts": "export function helper(x: number): number {\n  return x;\n}\n",
    "tests/test_core.py": "from pkg.core import helper\n\n\ndef test_helper():\n    assert helper(1) == 1\n",
}


class _Env:
    def run_test(self, rel, ids=None):
        return {}


@pytest.fixture()
def tools(tmp_path):
    from harness.experiments.r3.shared_tools import SharedTools
    from harness.experiments.r3.tools import ToolLayer
    from harness.experiments.r3.working_index import WorkingIndex
    work = str(tmp_path / "work")
    for p, text in FILES.items():
        os.makedirs(os.path.dirname(os.path.join(work, p)), exist_ok=True)
        open(os.path.join(work, p), "w").write(text)
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"], cwd=work, check=True)
    wi = WorkingIndex(work, str(tmp_path / "shadow"))
    shared = SharedTools(work, _Env(), [])
    layer = ToolLayer(wi, shared=shared)
    yield layer, shared
    layer.close()
    wi.close()


def _hit(out: str, where: str) -> str:
    return next(ln for ln in out.splitlines() if ln.startswith(where + ": "))


# ---------------------------------------------------------------------------------------------- D1
def test_search_labels_python_kinds_and_enclosing(tools):
    layer, _ = tools
    out = layer.call("prism_search", pattern="helper")
    assert out.startswith("prism_search 'helper':") and "hits in" in out
    assert "[definition; in `pkg.core.helper`]" in _hit(out, "pkg/core.py:5")
    assert "[comment; in `pkg.core.helper`]" in _hit(out, "pkg/core.py:6")
    assert "[string; in `pkg.core.Child.run`]" in _hit(out, "pkg/core.py:31")
    assert "[call; in `pkg.core.Child.run`]" in _hit(out, "pkg/core.py:32")
    assert "[test; import]" in _hit(out, "tests/test_core.py:1")
    assert "[test; call; in `tests.test_core.test_helper`]" in _hit(out, "tests/test_core.py:5")
    assert "[template]" in _hit(out, "pkg/templates/page.html:1")
    assert "[config]" in _hit(out, "pkg/settings.toml:2")
    assert "[import]" in _hit(out, "web/a.ts:1")
    assert "[call; in `web.a.useIt`]" in _hit(out, "web/a.ts:4")


def test_search_labels_type_use(tools):
    layer, _ = tools
    out = layer.call("prism_search", pattern=r"\bBase\b")
    assert "[base class; in `pkg.core.Child`]" in _hit(out, "pkg/core.py:22")
    assert "[type annotation; in `pkg.core.Child.run`]" in _hit(out, "pkg/core.py:24")
    assert "[isinstance; in `pkg.core.Child.run`]" in _hit(out, "pkg/core.py:25")
    assert "[except; in `pkg.core.Child.run`]" in _hit(layer.call("prism_search", pattern="except Boom"), "pkg/core.py:29")
    assert "[decorator; in `pkg.core.Child.run`]" in _hit(layer.call("prism_search", pattern="@deco"), "pkg/core.py:23")
    assert "[import]" in _hit(layer.call("prism_search", pattern="^from functools"), "pkg/core.py:2")


def test_search_kind_filter_and_no_match(tools):
    layer, _ = tools
    calls = layer.call("prism_search", pattern="helper", kind="call")
    assert all("call" in ln for ln in calls.splitlines()[1:]) and "pkg/core.py:32" in calls
    assert "pkg/core.py:5:" not in calls
    tests_only = layer.call("prism_search", pattern="helper", kind="test")
    assert {ln.split(":")[0] for ln in tests_only.splitlines()[1:]} == {"tests/test_core.py"}
    assert layer.call("prism_search", pattern="nothing_like_this").startswith("no matches for")


def test_search_sees_an_edit_at_once(tools):
    layer, shared = tools
    shared.call("edit_file", path="pkg/core.py", old="        return helper(1)\n",
                new="        total = helper(2)\n        return total\n")
    out = layer.call("prism_search", pattern=r"helper\(2\)")
    assert "[call; in `pkg.core.Child.run`]" in _hit(out, "pkg/core.py:32")


# ---------------------------------------------------------------------------------------------- D2
def test_read_is_read_file_plus_a_footer(tools):
    layer, shared = tools
    plain = shared.call("read_file", path="pkg/core.py", start=20, end=32)
    out = layer.call("prism_read", path="pkg/core.py", start=20, end=32)
    body, _, footer = out.rpartition("\n--- PRISM: ")
    assert body == plain
    assert footer.startswith("definitions in these lines: `pkg.core.Child` (22-32), `pkg.core.Child.run` (23-32)")


def test_read_any_file_and_current_text(tools):
    layer, shared = tools
    t = layer.call("prism_read", path="tests/test_core.py")
    assert t.startswith("tests/test_core.py (lines 1-5 of 5)") and "--- PRISM: test file; definitions" in t
    html = layer.call("prism_read", path="pkg/templates/page.html")
    assert "1\t<p>{{ helper }}</p>" in html and html.endswith("--- PRISM: template file")
    shared.call("edit_file", path="pkg/settings.toml", old="helper = true", new="helper = false")
    assert "2\thelper = false" in layer.call("prism_read", path="pkg/settings.toml")
    assert layer.call("prism_read", path="missing.py").startswith("error:")


# ---------------------------------------------------------------------------------------------- D3
def test_prism_only_arm_tool_list():
    from harness.experiments.r3 import loop as L
    docs = L.tool_docs("PRISM_ONLY")
    assert "- read_file" not in docs and "- grep" not in docs
    assert "- prism_search" in docs and "- prism_read" in docs and "- edit_file" in docs and "- run_tests" in docs
    names = {t["function"]["name"] for t in L.native_tools("PRISM_ONLY")}
    assert {"read_file", "grep"}.isdisjoint(names) and {"prism_search", "prism_read", "list_dir"} <= names
    # the other arms keep their tools; PRISM and INJECTED also get the two new PRISM tools
    assert "- grep" in L.tool_docs("PRISM") and "- prism_search" in L.tool_docs("PRISM")
    assert "- prism_search" not in L.tool_docs("BASELINE") and "- read_file" in L.tool_docs("BASELINE")


class _Shared:
    done_summary = None

    def call(self, tool, **args):
        return "done" if tool == "done" else f"{tool} ran"


class _Index:
    def call(self, tool, **args):
        return f"{tool} ran"


class _Scripted:
    def __init__(self, replies):
        self.replies = list(replies)

    def __call__(self, msgs, seed):
        return {"text": self.replies.pop(0), "thinking": None, "prompt_tokens": 10, "answer_tokens": 2,
                "done_reason": None, "seconds": 0.0}


def test_prism_only_refuses_grep_and_read_file(tmp_path):
    from harness.experiments.r3 import loop as L
    replies = ['{"tool": "grep", "args": {"pattern": "x"}}', '{"tool": "read_file", "args": {"path": "a.py"}}',
               '{"tool": "prism_search", "args": {"pattern": "x"}}', '{"tool": "done", "args": {"summary": "ok"}}']
    L.AgentRun({"id": "t", "prompt": "p"}, "PRISM_ONLY", 42, _Scripted(replies), _Shared(), _Index()).run(str(tmp_path))
    log = [json.loads(ln) for ln in open(tmp_path / "t__PRISM_ONLY__42.jsonl")]
    outs = [x.get("output_head", "") for x in log]
    assert outs[0].startswith("error: unknown tool 'grep'") and outs[1].startswith("error: unknown tool 'read_file'")
    assert outs[2].startswith("prism_search ran")


def test_compaction_treats_prism_read_as_a_read():
    from harness.experiments.r3.compaction import compact
    out = "pkg/a.py (lines 1-40 of 40)\n" + "\n".join(f"{i}\tx = {i}" for i in range(1, 41)) + "\n--- PRISM: x"
    hist = [{"role": "user", "content": out, "tool_output": True, "tool": "prism_read", "args": {"path": "pkg/a.py"},
             "turn": 1},
            {"role": "user", "content": out, "tool_output": True, "tool": "prism_read", "args": {"path": "pkg/a.py"},
             "turn": 2}]
    new, counts = compact(hist, lambda t: len(t) // 4, keep_recent=0)
    assert counts["superseded"] == 1 and new[0]["content"].startswith("(superseded: `pkg/a.py` lines 1-40")
