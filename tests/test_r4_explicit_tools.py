"""R4-SANITY-3: the explicit tool messages (`r3/shared_tools.py`, `explicit=True`); off, the messages are R3a's."""
import os

from harness.experiments.r3.shared_tools import SharedTools

TOK = "/home/user/models/qwen2-tokenizer/tokenizer.json"


def _tools(tmp_path, files, explicit=True, tok=None):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return SharedTools(str(tmp_path), None, [], tok, explicit=explicit)


SRC = "class A:\n    def f(self, x):\n        return x\n\n    def g(self):\n        return self.f(1)\n\n\ndef h():\n    return A().f(1)\n"


def test_not_found_shows_the_closest_lines(tmp_path):
    t = _tools(tmp_path, {"m.py": SRC})
    out = t.call("edit_file", path="m.py", old="def f(self, x, y):\n    return x", new="")
    assert "not found" in out and "closest lines in the file, from line 2" in out and "2\t    def f(self, x):" in out
    out = _tools(tmp_path, {"m.py": SRC}, explicit=False).call("edit_file", path="m.py", old="def f(self, x, y):", new="")
    assert out == "error: the text to replace was not found in m.py"                       # R3a's message


def test_duplicates_are_located_and_an_empty_old_on_an_existing_file_is_explained(tmp_path):
    t = _tools(tmp_path, {"m.py": SRC})
    out = t.call("edit_file", path="m.py", old="f(1)", new="f(2)")
    assert "occurs 2 times in m.py, at lines 6, 10" in out
    out = t.call("edit_file", path="m.py", old="", new="x = 1\n")
    assert "already exists, so `old` cannot be empty" in out and open(tmp_path / "m.py").read() == SRC


def test_a_cut_read_says_what_it_showed_and_where_to_continue(tmp_path):
    if not os.path.exists(TOK):
        import pytest
        pytest.skip("no tokenizer")
    big = "".join(f"value_{i} = compute_something_long({i}, 'padding padding padding')\n" for i in range(3000))
    out = _tools(tmp_path, {"big.py": big}, tok=TOK).call("read_file", path="big.py")
    head = out.split("\n", 1)[0]
    assert "of 3000 shown (cut at 4,000 tokens); continue with start=" in head and "[cut at 4,000 tokens]\n" not in out
    last = int(head.split("lines 1-", 1)[1].split(" ", 1)[0])
    assert out.rstrip().endswith(f"continue with start={last + 1}]")
    small = _tools(tmp_path, {"s.py": "a = 1\n"}, tok=TOK).call("read_file", path="s.py")
    assert small.split("\n", 1)[0] == "s.py (lines 1-1 of 1)"
