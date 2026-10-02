"""ast_splitter: compilable chunks, nested class naming, first-level-only
splitting, preambles, decorators/docstrings, full coverage of a real file."""
import os

import pytest

from harness.ast_splitter import PythonSplitter, module_name

QWEN = os.environ.get("HARNESS_TOKENIZER_PATH", "/home/user/models/qwen2-tokenizer")
FASTAPI = "/home/user/SCE/.benchmarks/corpora/fastapi"


class Words:
    name = "words"
    def count(self, t): return len(t.split())


SRC = b'''"""Module doc."""
import os

X = 1


@decorator
def small(a):
    """Small doc."""
    return a


class Outer(Base):
    """Outer doc."""
    flag = True

    def method(self):
        return 1

    class Inner:
        size = 3

        @property
        def deep(self):
            return self.size


def big(n):
    """Big doc."""
    total = 0
    for i in range(n):
        total += i
        if i > 2:
            total -= 1
    with open("f") as fh:
        data = fh.read()
    try:
        x = 1
    except Exception:
        x = 2
    return total
'''


def _chunks(cap=800):
    return {c.qualified_name + (f"#{c.part}" if c.n_parts > 1 else "") + ":" + c.kind: c
            for c in PythonSplitter(Words(), cap).split_source(SRC, "pkg/mod.py")}


def test_module_name():
    assert module_name("fastapi/dependencies/utils.py") == "fastapi.dependencies.utils"
    assert module_name("fastapi/__init__.py") == "fastapi"


def test_kinds_names_prefixes_and_compilation():
    ch = _chunks()
    assert set(ch) == {
        "pkg.mod:module_block", "pkg.mod.small:function", "pkg.mod.Outer:class_preamble",
        "pkg.mod.Outer.method:method", "pkg.mod.Outer.Inner:class_preamble",
        "pkg.mod.Outer.Inner.deep:method", "pkg.mod.big:function"}
    deep = ch["pkg.mod.Outer.Inner.deep:method"]
    assert deep.content.splitlines()[:2] == ["class Outer(Base):", "    class Inner:"]   # parent_prefix threaded
    assert deep.prefix_lines == 2 and "@property" in deep.content
    assert '"""Small doc."""' in ch["pkg.mod.small:function"].content
    assert ch["pkg.mod.small:function"].content.startswith("@decorator")
    pre = ch["pkg.mod.Outer:class_preamble"].content
    assert '"""Outer doc."""' in pre and "flag = True" in pre and "def method" not in pre
    for c in ch.values():
        compile(c.content, c.qualified_name, "exec")


def test_oversized_function_splits_at_first_level_only():
    parts = [c for c in PythonSplitter(Words(), 25).split_source(SRC, "pkg/mod.py") if c.qualified_name == "pkg.mod.big"]
    assert len(parts) > 1 and all(p.kind == "function_part" for p in parts)
    for p in parts:
        compile(p.content, "part", "exec")
        assert "def big(n):" in p.content
    # the nested `if` stays inside its `for`: never split deeper
    owner = [p for p in parts if "for i in range(n):" in p.content]
    assert len(owner) == 1 and "if i > 2:" in owner[0].content
    assert '"""Big doc."""' in parts[0].content and parts[0].part == 1


def test_unsplittable_falls_back_whole():
    # `inner`'s nonlocal binding (`x = 0`) is at the end: any split separates them
    src = b"def outer():\n    def inner():\n        nonlocal x\n        x += 1\n" + b"    y = 1\n" * 40 + b"    x = 0\n"
    ch = PythonSplitter(Words(), 20).split_source(src, "m.py")
    assert len(ch) == 1 and ch[0].oversized and "unsplittable" in ch[0].notes[0]


@pytest.mark.skipif(not (os.path.isdir(FASTAPI) and os.path.exists(QWEN)), reason="FastAPI checkout / Qwen tokenizer missing")
def test_real_fastapi_file_reassembles_and_compiles():
    from harness.tokenizer import HuggingFaceTokenizer
    sp = PythonSplitter(HuggingFaceTokenizer(QWEN), 800)
    path = os.path.join(FASTAPI, "fastapi/dependencies/utils.py")
    chunks = sp.split_file(path, FASTAPI)
    lines = open(path).read().split("\n")
    covered = set().union(*(c.source_rows for c in chunks))
    missing = [i for i, ln in enumerate(lines) if ln.strip() and i not in covered]
    assert missing == []                                  # every non-blank line reaches some chunk
    errors = []
    for c in chunks:
        try:
            compile(c.content, c.source_id, "exec")
        except SyntaxError as e:
            errors.append((c.source_id, str(e)))
    assert errors == []
    assert all(c.token_count <= 800 or c.oversized for c in chunks)
    assert any(c.qualified_name == "fastapi.dependencies.utils.get_dependant" for c in chunks)
