"""Context for writing tests for a function: what it does, who depends on it, and how its tests are written.

    1. Target        the function's code and digest (conditions, returns, raises), its callees' errors and
                     empty returns - what a test should exercise
    2. Its callers   how production code uses it (result use, errors caught, arguments): realistic calls
    3. Tests that reach it already (by name), and the full code of the two closest tests in the test file
                     the new tests go to (calling the target first, then sharing name words)
    4. Test conventions in that file, with counts: fixtures taken as parameters, `pytest.raises` /
                     `self.assertRaises`, `@pytest.mark.parametrize`, plain `assert` vs `self.assert*`,
                     test classes

Everything comes from the index of the code as it is now.
"""
from __future__ import annotations

import collections
import os
import re
from dataclasses import dataclass, field

from prism.impact.context import ChangeContextBuilder
from prism.impact.style import _words, source_of
from prism.slicer.tokenizer import count_tokens

DEFAULT_BUDGET = 6000
MAX_TEST_LINES = 60


@dataclass
class TestContext:
    __test__ = False  # not a pytest test class
    target: str
    test_file: str
    target_code: str
    target_digest: str
    callees: list[str] = field(default_factory=list)
    callers: list[str] = field(default_factory=list)
    tests_reaching: list[str] = field(default_factory=list)
    examples: list[tuple[str, str]] = field(default_factory=list)   # (qname, code)
    conventions: list[str] = field(default_factory=list)
    token_count: int = 0


def _rel(path: str, root: str) -> str:
    return os.path.relpath(path, root) if os.path.isabs(path) else path


def test_conventions(builder, tests: list[str]) -> list[str]:
    """Patterns over the test functions of one file, each with its count."""
    if not tests:
        return []
    params: collections.Counter = collections.Counter()
    counts = collections.Counter()
    for q in tests:
        code = source_of(builder, q)
        m = re.search(r"def\s+\w+\s*\(([^)]*)\)", code)
        for p in (m.group(1).split(",") if m else []):
            p = p.split(":", 1)[0].split("=", 1)[0].strip()
            if p and p not in ("self", "cls"):
                params[p] += 1
        counts["pytest.raises"] += bool(re.search(r"\bpytest\.raises\b", code))
        counts["self.assertRaises"] += bool(re.search(r"\bself\.assertRaises\w*\b", code))
        counts["parametrize"] += bool(re.search(r"@pytest\.mark\.parametrize", code))
        counts["assert"] += bool(re.search(r"(?m)^\s*assert\b", code))
        counts["self.assert"] += bool(re.search(r"\bself\.assert\w+\(", code))
        counts["async"] += bool(re.search(r"(?m)^\s*async\s+def\b", code))
        counts["class"] += "." in q.rsplit(".", 1)[0] and q.rsplit(".", 2)[-2][:1].isupper()
    n = len(tests)
    out = []
    if counts["assert"] >= counts["self.assert"] and counts["assert"]:
        out.append(f"plain `assert` statements ({counts['assert']} of {n} tests)")
    elif counts["self.assert"]:
        out.append(f"`self.assert*` methods (unittest style, {counts['self.assert']} of {n} tests)")
    for key, text in (("pytest.raises", "errors checked with `pytest.raises`"),
                      ("self.assertRaises", "errors checked with `self.assertRaises`"),
                      ("parametrize", "`@pytest.mark.parametrize` for several inputs"),
                      ("async", "async tests"), ("class", "tests grouped in test classes")):
        if counts[key]:
            out.append(f"{text} ({counts[key]} of {n} tests)")
    common = [f"`{p}` ({c})" for p, c in params.most_common(6) if c >= 2]
    if common:
        out.append("fixtures taken as parameters: " + ", ".join(common))
    return out


class TestContextBuilder:
    __test__ = False  # not a pytest test class

    def __init__(self, builder, repo_root: str):
        self.b, self.root = builder, os.path.abspath(repo_root)
        self.change = ChangeContextBuilder(builder, self.root)

    def build(self, target: str, test_file: str, budget_tokens: int = DEFAULT_BUDGET) -> TestContext:
        b = self.b
        cctx = self.change.build(target, budget_tokens=2000, include_tests=True)
        path = os.path.join(self.root, test_file)
        ctx = TestContext(target, test_file, cctx.anchor_code, cctx.anchor_digest)
        ctx.callees = [d.text for d in cctx.callees]
        ctx.callers = [f"`{r.caller}` ({r.file}): " + "; ".join(f"L{d.line} {d.aspect}: {d.text}" for d in r.dependencies[:3])
                       for r in cctx.callers if not r.is_test][:6]
        ctx.tests_reaching = cctx.impact.get("tests", [])[:12]
        in_file = sorted(q for q in b._def_nodes if (s := b.symbol_table.get(q)) is not None and s.file == path
                         and q.rsplit(".", 1)[-1].startswith("test"))
        ctx.conventions = test_conventions(b, in_file)
        leaf = target.rsplit(".", 1)[-1]
        own = _words(target)

        def closeness(q: str) -> tuple:
            code = source_of(b, q)
            calls_it = bool(re.search(rf"\b{re.escape(leaf)}\s*\(", code))
            n = len(code.splitlines())
            return (-int(calls_it), -len(own & _words(q)), n > MAX_TEST_LINES, q)
        spent = count_tokens(render(ctx))
        for q in sorted(in_file, key=closeness)[:2]:
            code = "\n".join(source_of(b, q).splitlines()[:MAX_TEST_LINES])
            cost = count_tokens(code) + 20
            if spent + cost <= budget_tokens:
                ctx.examples.append((q, code))
                spent += cost
        ctx.token_count = count_tokens(render(ctx))
        return ctx


def render(ctx: TestContext) -> str:
    out = [f"# Test context: `{ctx.target}` (tests go to {ctx.test_file})", "", "## 1. Target", "```",
           ctx.target_code, "```", "", "Digest (what a test should exercise):", "```", ctx.target_digest, "```"]
    if ctx.callees:
        out += ["", "What it relies on in its callees:"] + [f"- {t}" for t in ctx.callees]
    out += ["", "## 2. How production code calls it"]
    out += [f"- {c}" for c in ctx.callers] or ["No production callers in the index."]
    out += ["", "## 3. Existing tests"]
    if ctx.tests_reaching:
        out.append("Tests that already reach it: " + ", ".join(f"`{q}`" for q in ctx.tests_reaching))
    for q, code in ctx.examples:
        out += ["", f"`{q}` (in {ctx.test_file}):", "```python", code, "```"]
    if ctx.conventions:
        out += ["", f"## 4. Test conventions in {ctx.test_file}"] + [f"- {c}" for c in ctx.conventions]
    return "\n".join(out) + "\n"
