"""Context for writing a NEW function: what already exists that it should reuse, and how code is written there.

The change context (`prism.impact.context`) needs an anchor that exists. A new function has none yet, so this
package starts from where it will live (its file and class) and what it is for (its name, signature and
purpose):

    1. Group     the directory's API and conventions (as in the change context)
    2. Class     when it is a method: the class's other methods (signatures) and the attributes set on `self`
    3. Helpers   existing functions that may do part of the work: ranked by PRISM's search over the name,
                 signature and purpose, then by nearness (same class, file, directory). Each is listed with
                 its signature and digest (what it does, returns and raises), not its whole code
    4. Style     the full code of the two closest functions (same class or file, sharing name words)

Everything comes from the index of the code as it is now. Nothing is inferred about the function being
written beyond the words it was given.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from prism.graph.subsystems import module_of
from prism.impact.groups import GroupIndex, GroupSummary
from prism.impact.style import _words, source_of
from prism.slicer.tokenizer import count_tokens
from prism.summaries.digest import DigestBuilder

DEFAULT_BUDGET = 6000
MAX_HELPERS = 12
POOL = 60
SIBLING_MAX_LINES = 60


@dataclass
class Helper:
    qname: str
    file: str
    line: int
    signature: str
    digest: str
    reason: str


@dataclass
class NewFunctionContext:
    file: str
    cls: str | None
    name: str
    group: GroupSummary
    class_methods: list[str] = field(default_factory=list)      # signatures
    class_attributes: list[str] = field(default_factory=list)
    helpers: list[Helper] = field(default_factory=list)
    siblings: list[tuple[str, str, str]] = field(default_factory=list)  # (qname, file, code)
    omitted: int = 0
    token_count: int = 0


def _rel(path: str, root: str) -> str:
    return os.path.relpath(path, root) if os.path.isabs(path) else path


def _signature(builder, qname: str) -> str:
    code = source_of(builder, qname)
    lines = [ln for ln in code.splitlines() if not ln.strip().startswith(("@", "#", "//", "*", "/*"))]
    out = []
    for ln in lines:
        out.append(ln.strip())
        if re.search(r"(\)\s*(->\s*[^:]+)?:\s*$)|(\{\s*$)|(=>\s*\{?\s*$)", ln) or len(out) >= 4:
            break
    return " ".join(out)[:200]


class NewFunctionContextBuilder:
    """New-function contexts in one index (sharing the search index, digests and groups)."""

    def __init__(self, builder, repo_root: str, entries=None):
        from prism.graph.entry_points import find_entry_points
        from prism.query.nlq import NLQIndex
        self.b, self.root = builder, os.path.abspath(repo_root)
        self.entries = entries if entries is not None else find_entry_points(builder)
        self.digests = DigestBuilder(builder, self.entries)
        self.groups = GroupIndex(builder, self.root, self.entries)
        self.search = NLQIndex(builder, self.root, summaries=None)

    def _functions_in(self, path: str) -> list[str]:
        return [q for q in self.b._def_nodes if (s := self.b.symbol_table.get(q)) is not None and s.file == path
                and s.kind in ("function", "method")]

    def build(self, file: str, name: str, signature: str = "", purpose: str = "", cls: str | None = None,
              budget_tokens: int = DEFAULT_BUDGET) -> NewFunctionContext:
        b = self.b
        path = os.path.join(self.root, file)
        group = module_of(path, self.root)
        ctx = NewFunctionContext(file, cls, name, self.groups.summary(group, self.digests))
        if cls and cls in b.symbol_table:
            members = sorted((q for q in b._def_nodes if q.startswith(cls + ".") and q.count(".") == cls.count(".") + 1
                              and (s := b.symbol_table.get(q)) is not None and s.kind in ("function", "method")),
                             key=lambda q: b.symbol_table.get(q).line_range[0])
            ctx.class_methods = [_signature(b, q) for q in members]
            attrs = set()
            for q in members:
                attrs |= set(re.findall(r"\bself\.([A-Za-z_]\w*)\s*=(?!=)", source_of(b, q)))
            ctx.class_attributes = sorted(attrs)
        # helpers: PRISM's search over the words given, then nearness
        words = " ".join(sorted(_words(name))) + " " + signature + " " + purpose
        ranked = [q for q in self.search.ranked(words)
                  if (s := b.symbol_table.get(q)) is not None and s.kind in ("function", "method")
                  and not b._in_test_file(s.file)][:POOL]
        def nearness(q: str) -> float:
            s = b.symbol_table.get(q)
            return (0.6 if cls and q.startswith(cls + ".") else 0) + (0.3 if s.file == path else 0) + \
                (0.2 if module_of(s.file, self.root) == group else 0)
        order = sorted(range(len(ranked)), key=lambda i: (-(1.0 / (i + 1) + nearness(ranked[i])), i))
        candidates = [ranked[i] for i in order][:MAX_HELPERS * 2]
        # siblings: the closest functions in the same class, then file, then group, sharing name words
        own = _words(name)
        local = [q for q in self._functions_in(path) if 3 <= (lambda s: s.line_range[1] - s.line_range[0] + 1)(
            b.symbol_table.get(q)) <= SIBLING_MAX_LINES]
        if len(local) < 2:
            local += [q for q in self.groups.members.get(group, []) if q not in local][:200]
        sib = sorted(local, key=lambda q: (-(2.0 * len(own & _words(q)) + (2 if cls and q.startswith(cls + ".") else 0)
                                             + (1 if b.symbol_table.get(q).file == path else 0)), q))[:2]
        spent = count_tokens(render(ctx))
        for q in sib:
            code = source_of(b, q)
            if spent + count_tokens(code) + 30 <= budget_tokens:
                ctx.siblings.append((q, _rel(b.symbol_table.get(q).file, self.root), code))
                spent += count_tokens(code) + 30
        for q in candidates:
            if len(ctx.helpers) >= MAX_HELPERS:
                break
            s = b.symbol_table.get(q)
            reason = ", ".join(x for x in (
                "same class" if cls and q.startswith(cls + ".") else "",
                "same file" if s.file == path else "",
                "same directory" if module_of(s.file, self.root) == group else "",
                ("name words: " + ", ".join(sorted(own & _words(q)))) if own & _words(q) else "") if x) \
                or "matches the purpose"
            h = Helper(q, _rel(s.file, self.root), s.line_range[0], _signature(b, q),
                       self.digests.digest(q).render(header=False), reason)
            cost = count_tokens(h.signature + h.digest) + 30
            if spent + cost > budget_tokens:
                ctx.omitted += 1
                continue
            ctx.helpers.append(h)
            spent += cost
        ctx.token_count = count_tokens(render(ctx))
        return ctx


def render(ctx: NewFunctionContext) -> str:
    where = f"in class `{ctx.cls}` " if ctx.cls else ""
    out = [f"# New function context: `{ctx.name}` {where}in {ctx.file}", ""]
    g = ctx.group
    out += [f"## 1. Group `{g.name}`", f"{g.functions} functions, {g.classes} classes in {g.files} files."]
    if g.api:
        out.append("Called from other directories (its API): " + ", ".join(f"`{q}` ({n})" for q, n in g.api))
    if g.conventions:
        out.append("Conventions:")
        out += [f"- {c.text}" for c in g.conventions]
    if ctx.cls:
        out += ["", f"## 2. Class `{ctx.cls}`"]
        if ctx.class_attributes:
            out.append("Attributes set on `self`: " + ", ".join(f"`{a}`" for a in ctx.class_attributes))
        if ctx.class_methods:
            out.append("Methods:")
            out += [f"- `{m}`" for m in ctx.class_methods]
    out += ["", "## 3. Existing functions that may do part of the work (reuse them rather than rewrite them)"]
    if not ctx.helpers:
        out.append("None found.")
    for h in ctx.helpers:
        out += [f"- `{h.qname}` ({h.file}:{h.line}; {h.reason}): `{h.signature}`", "  ```", "  " +
                h.digest.replace("\n", "\n  "), "  ```"]
    if ctx.siblings:
        out += ["", "## 4. Style to follow (the closest existing functions)"]
        for q, f, code in ctx.siblings:
            out += [f"`{q}` ({f}):", "```", code, "```"]
    if ctx.omitted:
        out += ["", f"Omitted: {ctx.omitted} helpers over the budget"]
    return "\n".join(out) + "\n"
