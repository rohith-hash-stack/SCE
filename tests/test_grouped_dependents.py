"""Who depends on a widely used symbol, under the answer's budget (`prism.impact.dependents`; Phase 2, after B10).

On a fixture with a class used from 60 modules (imports, annotations, calls) and itself long:
- `prism_change_context` gives the class's code as an outline (its first line and each method's), with the pointer to
  `prism_read_code`, and groups section 4 by file, nearest first, every use named or counted;
- `prism_blast_radius` groups its list the same way, inside its share of the cap;
- a symbol with few dependents is listed one by one, exactly as before (`test_b10_surfacing.py`, the golden outputs).
"""
import os
import re
import subprocess

import pytest

N_USERS = 60


def _files():
    methods = "".join(f"    def method_{i}(self, x: int) -> int:\n" + "".join(
        f"        x = x + {j}  # step {j} of method {i}, padding the class body\n" for j in range(6)) + "        return x\n\n"
        for i in range(30))
    files = {"pkg/__init__.py": "", "pkg/core/__init__.py": "",
             "pkg/core/hub.py": "class Hub:\n    \"\"\"A widely used class.\"\"\"\n\n" + methods,
             "pkg/core/near.py": "from pkg.core.hub import Hub\n\n\ndef near(h: Hub) -> Hub:\n    return Hub()\n"}
    for i in range(N_USERS):
        files[f"pkg/far/sub{i % 6}/__init__.py"] = ""
        files[f"pkg/far/sub{i % 6}/user_{i}.py"] = (
            "from pkg.core.hub import Hub\n\n\n"
            f"def make_{i}() -> Hub:\n    return Hub()\n\n\n"
            f"def take_{i}(h: Hub) -> int:\n    return 1 if isinstance(h, Hub) else 0\n")
    return files


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    from harness.experiments.r3.runner import TOKENIZER
    from harness.experiments.r3.tools import ToolLayer
    from harness.experiments.r3.working_index import WorkingIndex
    root = tmp_path_factory.mktemp("grouped")
    work = str(root / "work")
    for p, text in _files().items():
        os.makedirs(os.path.dirname(os.path.join(work, p)), exist_ok=True)
        open(os.path.join(work, p), "w").write(text)
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"], cwd=work, check=True)
    wi = WorkingIndex(work, str(root / "shadow"))
    lay = ToolLayer(wi, TOKENIZER if os.path.exists(TOKENIZER) else None)
    yield lay
    lay.close()
    wi.close()


def _section4(text):
    return text[text.index("## 4."):text.index("## 5.")]


def _accounted(listing: str) -> int:
    """Uses a grouped listing accounts for: rows in full, names in file lines, '+N' and the closing 'N more'."""
    n = 0
    for ln in listing.splitlines():
        if ln.startswith("- `"):
            n += 1
        elif ln.startswith("- ... "):
            n += int(re.search(r"\((\d+) more\)", ln).group(1))
        elif ln.startswith("- "):
            n += int(re.match(r"- [^ ]+(?: \(test\))? \((\d+)\)", ln).group(1))
    return n


def test_long_anchor_is_an_outline_with_a_pointer(layer):
    out = layer.call("prism_change_context", symbol="pkg.core.hub.Hub")
    s3 = out[out.index("## 3. Anchor"):out.index("## 4.")]
    assert "too long for this answer" in s3 and "prism_read_code `pkg.core.hub.Hub` gives the full code" in s3
    assert "1: class Hub:" in s3 and "    def method_0(self, x: int) -> int:" in s3 and "step 3 of method" not in s3


def test_change_context_groups_dependents_nearest_first(layer):
    out = layer.call("prism_change_context", symbol="pkg.core.hub.Hub")
    s4 = _section4(out)
    assert "more than this answer holds one by one: the nearest in full, the rest by file" in s4
    first = [ln for ln in s4.splitlines() if ln.startswith("- `")]
    assert first and first[0].startswith("- `pkg.core.near")                      # the sibling module first
    assert "By file (the rest;" in s4 and "pkg/far/sub" in s4
    assert "... [cut at" not in out[:out.index("## 5.")]                          # section 4 is not cut off
    # every dependent is accounted for: 1 near module + its function, 60 user modules, 120 functions
    assert _accounted(s4[s4.index("\n") + 1:]) == 2 + N_USERS * 3


def test_blast_radius_groups_inside_its_share(layer):
    out = layer.call("prism_blast_radius", symbol="pkg.core.hub.Hub")
    head = out.split("\n\n<", 1)[0]
    assert head.startswith("Blast radius of `pkg.core.hub.Hub`:") and "By file (the rest;" in head
    from prism.slicer.tokenizer import count_tokens
    from harness.experiments.r3.tools import BLAST_LIST_SHARE, CAP_TOKENS
    assert count_tokens(head) <= CAP_TOKENS * BLAST_LIST_SHARE + 80


def test_few_dependents_are_listed_one_by_one(layer):
    s4 = _section4(layer.call("prism_change_context", symbol="pkg.core.near.near"))
    assert "By file" not in s4 and "more than this answer holds" not in s4


def test_grouped_unit():
    from prism.impact.dependents import Row, grouped, listing
    count = lambda t: len(t.split())
    rows = [Row(f"m{i}.f", f"a/b{i % 3}/m{i}.py", ["calls"], [f"- `m{i}.f` (a/b{i % 3}/m{i}.py)"]) for i in range(30)]
    rows.append(Row("a.x.near", "a/x/n.py", ["import"], ["- `a.x.near` (module)"], is_module=True))
    lines, was = listing(rows, "a/x/hub.py", 1000, count, "{n} in {files} files")
    assert not was and len(lines) == 31                                            # fits: unchanged
    lines, was = listing(rows, "a/x/hub.py", 60, count, "{n} in {files} files")
    assert was and lines[0] == "31 in 31 files" and lines[1] == "- `a.x.near` (module)"    # nearest first
    assert _accounted("\n".join(lines[1:])) == 31
    assert count("\n".join(grouped(rows, "a/x/hub.py", 60, count, "{n} in {files} files"))) <= 60
