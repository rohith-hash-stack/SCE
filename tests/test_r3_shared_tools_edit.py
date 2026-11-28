"""Regression test for the qwen notice check's edit loop (2026-11-06, werkzeug `Headers.get`).

The model read lines 159-163, took the indentation of line 163 from what `read_file` showed, and sent `edit_file` with
it. With the old gutter (`f"{i:>5}  {line}"`) the two separator spaces were read as indentation: 14 spaces where the
file has 12, so every edit was "not found". With a tab between the number and the line the same reading gives 12.
"""
from __future__ import annotations

import re

import pytest

from harness.experiments.r3.shared_tools import SharedTools, ToolCallError

FILE = "headers.py"


def fixture_text() -> str:
    """The shape of werkzeug's headers.py at 2adaad1a: `return default` on line 163, a second `except ValueError:`."""
    head = ["class Headers:"] + [f"    # filler {i}" for i in range(2, 159)] + [
        "    def get(self, key, default=None, type=None):",   # 159
        "        try:",                                         # 160
        "            return type(rv)",                          # 161
        "        except ValueError:",                           # 162
        "            return default",                           # 163
        "",                                                     # 164
        "    def getlist(self, key, type=None):",               # 165
        "        for v in self._list:",                         # 166
        "            try:",                                     # 167
        "                rv.append(type(v))",                   # 168
        "            except ValueError:",                       # 169
        "                continue",                             # 170
    ]
    assert head[162] == "            return default"
    return "\n".join(head) + "\n"


def old_gutter(path: str, lines: list[str], start: int, end: int) -> str:
    """`read_file`'s rendering before the fix."""
    body = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(start, end + 1))
    return f"{path} (lines {start}-{end} of {len(lines)})\n{body}"


def indent_as_read(rendered: str, lineno: int) -> int:
    """The indentation of `lineno` as the model read it.

    Drop the line number, and one tab if a tab follows it. Every space after that counts as indentation: with a space
    gutter nothing marks where the prefix ends.
    """
    row = next(r for r in rendered.split("\n")[1:] if r.strip().split()[:1] == [str(lineno)])
    after = re.sub(r"^\s*\d+", "", row, count=1)
    if after.startswith("\t"):
        after = after[1:]
    return len(after) - len(after.lstrip(" "))


@pytest.fixture()
def tools(tmp_path):
    (tmp_path / FILE).write_text(fixture_text())
    return SharedTools(str(tmp_path), None, [], None)


def edit_from_reading(tools, rendered: str) -> str:
    ind = " " * indent_as_read(rendered, 163)
    return tools.call("edit_file", path=FILE, old=f"except ValueError:\n{ind}return default",
                      new=f"except (ValueError, TypeError):\n{ind}return default")


def test_read_file_separates_the_number_with_a_tab(tools):
    out = tools.call("read_file", path=FILE, start=159, end=163)
    assert out.split("\n")[-1] == "163\t            return default"


def test_the_edit_from_the_tab_rendering_succeeds(tools, tmp_path):
    rendered = tools.call("read_file", path=FILE, start=159, end=163)
    assert indent_as_read(rendered, 163) == 12
    out = edit_from_reading(tools, rendered)
    assert out.startswith("edited headers.py at line 162")
    text = (tmp_path / FILE).read_text()
    assert "        except (ValueError, TypeError):\n            return default\n" in text
    assert "            except ValueError:\n                continue" in text      # the other occurrence untouched


def test_the_same_edit_from_the_old_gutter_fails(tools, tmp_path):
    lines = (tmp_path / FILE).read_text().splitlines()
    rendered = old_gutter(FILE, lines, 159, 163)
    assert indent_as_read(rendered, 163) == 14                                 # what the model sent on Kaggle
    out = edit_from_reading(tools, rendered)
    assert out.startswith("error: the text to replace was not found")
    assert "except ValueError:" in (tmp_path / FILE).read_text().split("\n")[161]


def test_a_whitespace_mismatch_names_the_line_and_the_prefix(tools):
    out = tools.call("edit_file", path=FILE, old="except ValueError:\n              return default",
                     new="except (ValueError, TypeError):\n              return default")
    assert "matches once apart from whitespace, at line 162" in out
    assert "line-number prefix" in out and "not part of the file" in out


def test_the_first_ambiguous_edit_is_still_refused(tools):
    out = tools.call("edit_file", path=FILE, old="except ValueError:", new="except (ValueError, TypeError):")
    assert "occurs 2 times" in out


def test_no_hint_when_the_text_is_absent_or_ambiguous_apart_from_whitespace(tools):
    out = tools.call("edit_file", path=FILE, old="except KeyError:\n  return None", new="x")
    assert out == "error: the text to replace was not found in headers.py"
    out = tools.call("edit_file", path=FILE, old="  except   ValueError:", new="x")   # two whitespace matches
    assert out == "error: the text to replace was not found in headers.py"


def test_tool_errors_are_returned_not_raised(tools):
    with pytest.raises(ToolCallError):
        tools._edit_file(FILE, "not here", "x")


def test_built_output_is_rebuilt_only_when_the_tree_changed(tmp_path):
    """JS/TS repositories whose tests use built output: run_tests and scoring see the agent's edits."""
    import subprocess
    from harness.experiments.r3.jsbuild import Env
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "src.js").write_text("a\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=x@y", "-c", "user.name=x", "commit", "-qm", "x"],
                   check=True)
    env = Env.__new__(Env)
    env.work, env.built, builds = str(tmp_path), False, []

    def fake_build():
        builds.append(1)
        env.built = True
        env._built_tree = env._tree_fingerprint()
    env.build = fake_build
    env.ensure_current_build()
    assert builds == []                                  # tests run on the source: nothing to rebuild
    env.build()
    env.ensure_current_build()
    assert len(builds) == 1                              # unchanged since the build
    (tmp_path / "src.js").write_text("b\n")
    env.ensure_current_build()
    assert len(builds) == 2                              # an edit: rebuilt
    (tmp_path / "new.js").write_text("c\n")
    env.ensure_current_build()
    assert len(builds) == 3                              # a new file: rebuilt
    env.ensure_current_build()
    assert len(builds) == 3


def test_overlapping_occurrences_are_not_unique(tmp_path):
    (tmp_path / "f.txt").write_text("ab\nab\nab\n")
    t = SharedTools(str(tmp_path), None, [], None)
    out = t.call("edit_file", path="f.txt", old="ab\nab\n", new="X\n")      # at line 1 and, overlapping, line 2
    assert "occurs 2 times" in out and (tmp_path / "f.txt").read_text() == "ab\nab\nab\n"
