"""The breadth study's edit-fix rule (`--edit-fix`, every arm alike; `shared_tools.whitespace_fix`).

It must be exactly rule v2 of `r4/phase3b_edit_replay.py`, the version validated offline on Phase 3b's refused edits.
With the switch off, the edit tool must behave exactly as before.
"""
import itertools
import random

import pytest

from harness.experiments.r3.shared_tools import SharedTools, whitespace_fix
from harness.experiments.r4.phase3b_edit_replay import proposed

SRC = ("def cookie_session_middleware(\n"
       "    cookie_session_backend: ClientSideSessionBackend, mock_asgi_app: ASGIApp\n"
       ") -> SessionMiddleware[ClientSideSessionBackend]:\n"
       "    return SessionMiddleware(app=mock_asgi_app, backend=cookie_session_backend)\n")
LINE = ") -> SessionMiddleware[ClientSideSessionBackend]:"
NEW = ") -> SessionMiddlewareImpl[ClientSideSessionBackend]:"


def _tools(tmp_path, edit_fix, text=SRC):
    (tmp_path / "conftest.py").write_text(text)
    return SharedTools(str(tmp_path), None, [], explicit=True, guards=True, feedback=False, edit_fix=edit_fix)


def _edit(t, old, new, **kw):
    return t.call("edit_file", path="conftest.py", old=old, new=new, **kw)


# ------------------------------------------------------------------------------------------- off: nothing changes
@pytest.mark.parametrize("old,new", [
    ("    " + LINE, "    " + NEW), ("\t" + LINE, "\t" + NEW), (LINE, NEW),
    ("    return SessionMiddleware(", "    return SessionMiddlewareImpl("), ("nothing like it", "x"),
    ("  " + LINE + "\n    return", "x"),
])
def test_off_is_byte_identical_to_the_tool_without_the_switch(tmp_path, old, new):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "conftest.py").write_text(SRC)
    (b / "conftest.py").write_text(SRC)
    old_tool = SharedTools(str(a), None, [], explicit=True, guards=True, feedback=False)
    off_tool = SharedTools(str(b), None, [], explicit=True, guards=True, feedback=False, edit_fix=False)
    assert _edit(old_tool, old, new) == _edit(off_tool, old, new)
    assert (a / "conftest.py").read_text() == (b / "conftest.py").read_text()
    assert off_tool.edit_fixes == 0


# --------------------------------------------------------------------------------------------- on: line 181's cases
@pytest.mark.parametrize("lead", ["    ", "\t", "        ", " \t "])
def test_on_fixes_wrong_leading_whitespace_on_an_unindented_line(tmp_path, lead):
    t = _tools(tmp_path, True)
    out = _edit(t, lead + LINE, lead + NEW)
    assert out.startswith("edited conftest.py at line 3: replaced 1 lines with 1.")
    assert "except for its leading whitespace" in out
    assert (tmp_path / "conftest.py").read_text() == SRC.replace(LINE, NEW)
    assert t.edit_fixes == 1


def test_on_gives_an_indented_line_its_own_indentation(tmp_path):
    t = _tools(tmp_path, True)
    _edit(t, "return SessionMiddleware(app=mock_asgi_app, backend=cookie_session_backend)",
          "return SessionMiddlewareImpl(app=mock_asgi_app, backend=cookie_session_backend)")
    assert "    return SessionMiddlewareImpl(app=" in (tmp_path / "conftest.py").read_text()


def test_on_an_exact_match_is_untouched_by_the_rule(tmp_path):
    t = _tools(tmp_path, True)
    out = _edit(t, LINE, NEW)
    assert out == "edited conftest.py at line 3: replaced 1 lines with 1"
    assert t.edit_fixes == 0


def test_on_ambiguous_partial_and_multi_line_are_refused_as_before(tmp_path):
    twice = SRC + "\n" + LINE + "\n"
    for text, old, new in [(twice, "  " + LINE, "  " + NEW),                       # two matching lines
                           (SRC, "\treturn SessionMiddleware(", "\treturn X("),     # part of a line, not exact
                           (SRC, "    " + LINE + "\n    return", "x\ny"),            # several lines
                           (SRC, "    ) -> SessionMiddleware[Other]:", "x")]:        # more than whitespace differs
        a, b = tmp_path / "on", tmp_path / "off"
        for d in (a, b):
            d.mkdir(exist_ok=True)
            (d / "conftest.py").write_text(text)
        on = SharedTools(str(a), None, [], explicit=True, guards=True, edit_fix=True)
        off = SharedTools(str(b), None, [], explicit=True, guards=True)
        assert _edit(on, old, new) == _edit(off, old, new)
        assert (a / "conftest.py").read_text() == text
        assert on.edit_fixes == 0


def test_on_an_empty_new_deletes_the_line(tmp_path):
    t = _tools(tmp_path, True)
    out = _edit(t, "  " + LINE + "\n", "")
    assert "replaced 1 lines with 0" in out and "removed it" in out
    assert (tmp_path / "conftest.py").read_text() == SRC.replace(LINE + "\n", "")


# --------------------------------------------------------------------------- the same rule as the validated replay
def _v2(text, old, new):
    r = proposed(text, old, new)
    return r["text_after"] if r["decision"] == "applied" else None


def test_whitespace_fix_equals_rule_v2_on_generated_cases():
    rng = random.Random(7)
    lines = ["def f(", "    a: int,", ") -> X[Y]:", "    return X(a)", "", "\tx = 1", "  y = X  ", ") -> X[Y]:"]
    leads = ["", " ", "    ", "\t", "\t ", "        "]
    n = 0
    for _ in range(4000):
        text = "\n".join(rng.choice(lines) for _ in range(rng.randint(1, 8))) + rng.choice(["", "\n"])
        body = rng.choice([ln.strip() for ln in lines] + ["X", "a: int,", "nothing"])
        old = rng.choice(leads) + body + rng.choice(["", "\n", "\n\n"])
        new = rng.choice(["", rng.choice(leads) + body.replace("X", "Z") + rng.choice(["", "\n"])])
        if not old.strip("\n"):
            continue
        got = whitespace_fix(text, old, new)
        assert (got["text"] if got else None) == _v2(text, old, new), (text, old, new)
        n += got is not None
    assert n > 200                                   # the generator does exercise the applied branch


@pytest.mark.parametrize("old,new", list(itertools.product(["  " + LINE, "\t" + LINE, LINE + "\n", "x"],
                                                           [NEW, "", "\t" + NEW + "\n"])))
def test_whitespace_fix_equals_rule_v2_on_line_181(old, new):
    got = whitespace_fix(SRC, old, new)
    assert (got["text"] if got else None) == _v2(SRC, old, new)


# ------------------------------------------------------------------------------------------------- the plumbing
def test_runner_and_pilot_run_accept_the_switch():
    import inspect
    from harness.experiments.r3 import pilot_run, runner
    assert "edit_fix" in inspect.signature(runner.run_task).parameters
    assert "--edit-fix" in inspect.getsource(runner.main) and "--edit-fix" in inspect.getsource(pilot_run.main)
