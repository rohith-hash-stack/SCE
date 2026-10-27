"""The change context for one anchor function: everything an AI needs before changing it, in one package.

    1. Codebase      the largest groups, how they call each other, entry points            (layer 5)
    2. Group         the anchor's directory: API, dependencies, conventions                (layer 4)
    3. Anchor        its full code and its digest
    4. Who depends on it   per caller: result use, errors caught or not, arguments, await  (layer 3)
    5. What it depends on  its callees' errors, empty returns and state changes            (layer 3)
    6. Style         conventions of its group and the full code of its closest siblings
    7. Callers' code full code of the direct callers that fit the budget, digests for the rest
    8. Wider impact  transitive callers by hop, and the tests among them

The AI always sees real code (the anchor, its siblings, its callers as far as the budget allows); digests
and statements add what is outside the code it is shown. Sections are filled in priority order under
`budget_tokens`; what does not fit is counted, not dropped silently.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from prism.graph.entry_points import find_entry_points
from prism.impact.dependencies import CallerReport, Dependency, DependencyAnalyzer
from prism.impact.groups import GroupIndex, GroupSummary
from prism.impact.style import Convention, Sibling, siblings, source_of
from prism.packer.candidate_index import upstream_callers_by_hop
from prism.slicer.tokenizer import count_tokens
from prism.summaries.digest import CERTAIN, DigestBuilder

DEFAULT_BUDGET = 12000
MAX_CALLER_CODE_LINES = 80


@dataclass
class ChangeContext:
    anchor: str
    file: str
    lines: tuple[int, int]
    language: str
    codebase: dict
    group: GroupSummary
    anchor_code: str
    anchor_digest: str
    callers: list[CallerReport]
    callees: list[Dependency]
    conventions: list[Convention]
    siblings: list[Sibling]
    caller_code: list[tuple[str, str, str]] = field(default_factory=list)   # (caller, file, code)
    caller_digests: list[tuple[str, str]] = field(default_factory=list)     # (caller, digest)
    impact: dict = field(default_factory=dict)
    omitted: dict = field(default_factory=dict)
    token_count: int = 0

    def to_dict(self) -> dict:
        from dataclasses import asdict
        d = asdict(self)
        d["group"] = asdict(self.group)
        return d


def _rel(path: str, root: str) -> str:
    try:
        return os.path.relpath(path, root)
    except ValueError:
        return path


class ChangeContextBuilder:
    """Change contexts for anchors of one index (sharing digests, groups and entry points)."""

    def __init__(self, builder, repo_root: str, entries=None):
        self.b, self.root = builder, os.path.abspath(repo_root)
        self.entries = entries if entries is not None else find_entry_points(builder)
        self.digests = DigestBuilder(builder, self.entries)
        self.groups = GroupIndex(builder, self.root, self.entries)
        self.deps = DependencyAnalyzer(builder, self.digests, self.root)

    def _neighbours(self, anchor: str) -> set[str]:
        graph = self.b.graph
        if anchor not in graph:
            return set()
        return {u for u, _v in graph.in_edges(anchor)} | {v for _u, v in graph.out_edges(anchor)}

    def build(self, anchor: str, budget_tokens: int = DEFAULT_BUDGET, include_tests: bool = True) -> ChangeContext:
        b = self.b
        sym = b.symbol_table.get(anchor)
        if sym is None or b.def_node(anchor) is None:
            raise KeyError(anchor)
        group = self.groups.summary(self.groups.of(anchor), self.digests)
        callers = [r for r in self.deps.callers(anchor) if include_tests or not r.is_test]
        ctx = ChangeContext(
            anchor=anchor, file=_rel(sym.file, self.root), lines=tuple(sym.line_range), language=str(sym.language_id),
            codebase=self.groups.codebase(), group=group,
            anchor_code=source_of(b, anchor), anchor_digest=self.digests.digest(anchor).render(header=False),
            callers=callers, callees=self.deps.callees(anchor), conventions=group.conventions,
            siblings=[Sibling(s.qname, _rel(s.file, self.root), s.lines, s.reason, s.code)
                      for s in siblings(b, anchor, self.groups.of, exclude=self._neighbours(anchor))])
        walked = upstream_callers_by_hop(b, anchor, include_tests=include_tests)
        by_hop: dict[int, int] = {}
        for _q, hop in walked.items():
            by_hop[hop] = by_hop.get(hop, 0) + 1
        tests = sorted((q for q in walked if (s := b.symbol_table.get(q)) is not None and b._in_test_file(s.file)),
                       key=lambda q: (walked[q], q))  # the most direct tests first
        ctx.impact = {"transitive_callers": len(walked), "by_hop": dict(sorted(by_hop.items())),
                      "tests": tests[:20], "tests_total": len(tests),
                      "entry_points_reached": sorted(q for q in walked if q in self.entries)[:10]}
        # callers' code under the budget: certain callers first, nearest code first
        spent = count_tokens(render(ctx))
        omitted = 0
        for report in callers:
            text = caller_excerpt(b, report)
            cost = count_tokens(text) + 20
            if spent + cost <= budget_tokens:
                ctx.caller_code.append((report.caller, report.file, text))
                spent += cost
                continue
            digest = self.digests.digest(report.caller).render(header=False)
            cost = count_tokens(digest) + 20
            if spent + cost <= budget_tokens:
                ctx.caller_digests.append((report.caller, digest))
                spent += cost
            else:
                omitted += 1
        if omitted:
            ctx.omitted["callers without code or digest (over budget)"] = omitted
        ctx.token_count = count_tokens(render(ctx))
        return ctx


def caller_excerpt(builder, report: CallerReport, context: int = 8) -> str:
    """A caller's code: all of it when short, else its first lines and a window around each call site."""
    sym = builder.symbol_table.get(report.caller)
    lines = source_of(builder, report.caller).splitlines()
    if len(lines) <= MAX_CALLER_CODE_LINES:
        return "\n".join(lines)
    start = sym.line_range[0]
    keep = set(range(0, min(6, len(lines))))
    for d in report.dependencies:
        i = d.line - start
        keep.update(range(max(0, i - context), min(len(lines), i + context + 1)))
    out, previous = [], -1
    for i in sorted(keep):
        if i != previous + 1:
            out.append(f"    # ... lines {start + previous + 1}-{start + i - 1} not shown ...")
        out.append(lines[i])
        previous = i
    if previous < len(lines) - 1:
        out.append(f"    # ... lines {start + previous + 1}-{start + len(lines) - 1} not shown ...")
    return "\n".join(out)


def _fence(lang: str) -> str:
    return {"python": "python", "javascript": "javascript", "typescript": "typescript", "tsx": "tsx",
            "java": "java", "csharp": "csharp", "go": "go"}.get(lang.lower().rsplit(".", 1)[-1], "")


def render(ctx: ChangeContext) -> str:
    fence = _fence(ctx.language)
    out = [f"# Change context: `{ctx.anchor}`", f"{ctx.file}, lines {ctx.lines[0]}-{ctx.lines[1]}", ""]

    cb = ctx.codebase
    out += ["## 1. Codebase", f"{cb['functions']} functions in {cb['groups']} directories; "
            f"{cb['entry_points']} registered entry points; languages: "
            + ", ".join(f"{k} {v}" for k, v in cb["languages"].items()), "",
            "Largest directories (functions, entry points):"]
    out += [f"- `{g}`: {n} functions" + (f", {e} entry points" if e else "") for g, n, e in cb["largest"]]
    if cb["flows"]:
        out += ["", "Strongest calls between directories:"]
        out += [f"- `{u}` -> `{v}`: {n} calls" for u, v, n in cb["flows"]]

    g = ctx.group
    out += ["", f"## 2. Group `{g.name}`", f"{g.functions} functions, {g.classes} classes in {g.files} files."]
    if g.entry_points:
        out.append("Entry points: " + ", ".join(f"`{q}`" for q in g.entry_points))
    if g.api:
        out.append("Called from other directories (its API): " + ", ".join(f"`{q}` ({n})" for q, n in g.api))
    if g.depends_on:
        out.append("Depends on: " + ", ".join(f"`{q}` ({n} calls)" for q, n in g.depends_on))
    if g.used_by:
        out.append("Used by: " + ", ".join(f"`{q}` ({n} calls)" for q, n in g.used_by))

    out += ["", "## 3. Anchor", f"```{fence}", ctx.anchor_code, "```", "", "Digest (what its code does, line by line):",
            "```", ctx.anchor_digest, "```"]

    out += ["", "## 4. Who depends on it"]
    if not ctx.callers:
        out.append("No callers in the index (an entry point, a callback, or unused).")
    for r in ctx.callers:
        tag = " (test)" if r.is_test else ""
        cert = "" if r.certainty == CERTAIN else " - linked by name only, possible"
        out.append(f"- `{r.caller}`{tag} ({r.file}){cert}")
        for d in r.dependencies:
            out.append(f"  - L{d.line} {d.aspect}: {d.text}")

    out += ["", "## 5. What it depends on"]
    if not ctx.callees:
        out.append("No resolved callee raises, returns nothing or changes state.")
    for d in ctx.callees:
        cert = "" if d.certainty == CERTAIN else " (possible target)"
        out.append(f"- L{d.line} {d.aspect}: {d.text}{cert}")

    out += ["", "## 6. Style to follow"]
    if ctx.conventions:
        out.append(f"Conventions in `{g.name}`:")
        out += [f"- {c.text}" for c in ctx.conventions]
    for s in ctx.siblings:
        out += ["", f"Sibling `{s.qname}` ({s.file}:{s.lines[0]}; {s.reason}):", f"```{fence}", s.code, "```"]

    if ctx.caller_code or ctx.caller_digests:
        out += ["", "## 7. Callers' code"]
        for q, f, code in ctx.caller_code:
            out += [f"`{q}` ({f}):", f"```{fence}", code, "```"]
        for q, digest in ctx.caller_digests:
            out += [f"`{q}` (digest only, over the code budget):", "```", digest, "```"]

    imp = ctx.impact
    if imp:
        out += ["", "## 8. Wider impact",
                f"{imp['transitive_callers']} transitive callers; by distance: "
                + ", ".join(f"{h} hop{'s' if h != 1 else ''}: {n}" for h, n in imp["by_hop"].items())]
        if imp["entry_points_reached"]:
            out.append("Entry points reached: " + ", ".join(f"`{q}`" for q in imp["entry_points_reached"]))
        if imp["tests_total"]:
            out.append(f"Tests that reach it ({imp['tests_total']}): " + ", ".join(f"`{q}`" for q in imp["tests"])
                       + (" ..." if imp["tests_total"] > len(imp["tests"]) else ""))
    if ctx.omitted:
        out += ["", "Omitted: " + ", ".join(f"{n} {k}" for k, n in ctx.omitted.items())]
    return "\n".join(out) + "\n"
