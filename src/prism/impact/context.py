"""The change context for one anchor function: everything an AI needs before changing it, in one package.

    1. Codebase      the largest groups, how they call each other, entry points            (layer 5)
    2. Group         the anchor's directory: API, dependencies, conventions                (layer 4)
    3. Anchor        its full code and its digest
    4. Who depends on it   per caller: result use, errors caught or not, arguments, await  (layer 3);
                           then the other uses: imports, `__all__`, type use, isinstance/except, values, decorators
    5. What it depends on  its callees' errors, empty returns and state changes            (layer 3)
    6. Style         conventions of its group and the full code of its closest siblings
    7. Callers' code full code of the direct callers that fit the budget, digests for the rest
    8. Wider impact  transitive callers by hop, and the tests among them

With the anchor's proposed new code, a first section lists the calls that change breaks (`prism.impact.breaks`);
when it finds none the section is left out.

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
#: Uses other than calls listed in section 4 (one line each); the rest are counted.
MAX_REFERENCES = 40
#: Shares of the budget: who depends on the anchor (section 4) and the anchor's own code and digest (section 3).
#: Past them, section 4 is grouped by file (`prism.impact.dependents`) and the anchor's code is given as an outline,
#: so a widely used class's dependents are not cut off by its own long code (scrapy's `Request`: 3,400 tokens of
#: code and digest, then 2,200 of dependents, in a 4,000-token answer).
DEPENDENTS_SHARE = 0.40
ANCHOR_SHARE = 0.30


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
    #: code the reader already has open (`visible`): `(qname, file, (lo, hi))` sent as a pointer, not again
    in_view: list[tuple[str, str, tuple[int, int]]] = field(default_factory=list)
    anchor_in_view: bool = False
    #: with `new_code`: the calls the proposed change breaks (`prism.impact.breaks`); None: not checked
    breaks: list | None = None
    #: uses other than calls (`prism.graph.references.references_to`): imports, type use, values, ...
    references: list = field(default_factory=list)
    references_total: int = 0
    #: section 4 grouped by file (over its budget share); None: listed one by one
    dependents_grouped: list | None = None
    #: the anchor's code given as an outline (over its budget share): its full length in lines
    anchor_outlined_lines: int = 0

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

    def _visible(self, visible) -> dict[str, list[tuple[int, int]]]:
        """`{repo-relative file: [(lo, hi), ...]}` from `{path: [(lo, hi)] | None}` (None: the whole file)."""
        out: dict[str, list[tuple[int, int]]] = {}
        for path, ranges in (visible or {}).items():
            rel = _rel(os.path.join(self.root, path) if not os.path.isabs(path) else path, self.root)
            out.setdefault(rel, []).extend(ranges or [(1, 10 ** 9)])
        return out

    def _fit_anchor(self, ctx: "ChangeContext", anchor: str, budget: int) -> None:
        """An anchor whose code and digest exceed `ANCHOR_SHARE` of the budget is given as an outline, its digest cut
        to the rest of that share (both say so, and where the full text is)."""
        share = int(budget * ANCHOR_SHARE)
        if not ctx.anchor_code or count_tokens(ctx.anchor_code) + count_tokens(ctx.anchor_digest) <= share:
            return
        ctx.anchor_outlined_lines = ctx.anchor_code.count("\n") + 1
        ctx.anchor_code = _outline(self.b, anchor, ctx.anchor_code)
        left = share - count_tokens(ctx.anchor_code)
        dl = ctx.anchor_digest.split("\n")
        keep, used = [], 0
        for ln in dl:
            c = count_tokens(ln) + 1
            if used + c > max(left, 200):
                break
            keep.append(ln)
            used += c
        if len(keep) < len(dl):
            keep.append(f"... {len(dl) - len(keep)} more digest lines (prism_digest gives all)")
        ctx.anchor_digest = "\n".join(keep)

    def _fit_dependents(self, ctx: "ChangeContext", refs: list, budget: int) -> None:
        """Section 4 grouped by file when its one-by-one listing exceeds `DEPENDENTS_SHARE` of the budget."""
        from prism.impact.dependents import Row, listing
        rows = []
        for r in ctx.callers:
            full = [render_caller(r)] + [f"  - L{d.line} {d.aspect}: {d.text}" for d in r.dependencies]
            rows.append(Row(r.caller, r.file, ["calls" if r.certainty == CERTAIN else "calls (possible)"], full,
                            is_test=r.is_test))
        for r in refs:
            rows.append(Row(r["source"], r["file"], list(r["kinds"]), [render_reference(r)], is_test=r["is_test"],
                            is_module=r["is_module"]))
        share = int(budget * DEPENDENTS_SHARE)
        if len(refs) > MAX_REFERENCES:                    # one by one, the list would stop at MAX_REFERENCES
            from prism.impact.dependents import grouped
            ctx.dependents_grouped = grouped(rows, ctx.file, share, count_tokens, GROUPED_INTRO)
            return
        lines, was_grouped = listing(rows, ctx.file, share, count_tokens, GROUPED_INTRO)
        ctx.dependents_grouped = lines if was_grouped else None

    def build(self, anchor: str, budget_tokens: int = DEFAULT_BUDGET, include_tests: bool = True,
              visible=None, new_code: str | None = None) -> ChangeContext:
        """The change context for `anchor`. `visible` (`{file: [(first, last line), ...]}`, None for a whole
        file) names code the reader already has open - an editor's file, a prompt's excerpt: code entirely
        inside it is not sent again (a pointer to its lines is), and the freed budget goes to other code.
        `new_code` (the anchor's proposed new code) adds the calls it breaks, checked call site by call site,
        at the top; it is outside the budget (it never displaces callers' code).
        Without `visible` and `new_code` the package is unchanged (version 1)."""
        b = self.b
        sym = b.symbol_table.get(anchor)
        if sym is None or b.def_node(anchor) is None:
            raise KeyError(anchor)
        view = self._visible(visible)

        def seen(qname: str) -> bool:
            s = b.symbol_table.get(qname)
            if s is None:
                return False
            lo, hi = s.line_range
            return any(a <= lo and hi <= z for a, z in view.get(_rel(s.file, self.root), ()))
        group = self.groups.summary(self.groups.of(anchor), self.digests)
        callers = [r for r in self.deps.callers(anchor) if include_tests or not r.is_test]
        ctx = ChangeContext(
            anchor=anchor, file=_rel(sym.file, self.root), lines=tuple(sym.line_range), language=str(sym.language_id),
            codebase=self.groups.codebase(), group=group,
            anchor_code=source_of(b, anchor), anchor_digest=self.digests.digest(anchor).render(header=False),
            callers=callers, callees=self.deps.callees(anchor), conventions=group.conventions,
            siblings=[Sibling(s.qname, _rel(s.file, self.root), s.lines, s.reason, s.code)
                      for s in siblings(b, anchor, self.groups.of, exclude=self._neighbours(anchor),
                                        k=2 if not view else 12) if not seen(s.qname)][:2])
        if view:
            ctx.anchor_in_view = seen(anchor)
            if ctx.anchor_in_view:
                ctx.anchor_code = ""
                ctx.in_view.append((anchor, ctx.file, ctx.lines))
        walked = upstream_callers_by_hop(b, anchor, include_tests=include_tests)
        by_hop: dict[int, int] = {}
        for _q, hop in walked.items():
            by_hop[hop] = by_hop.get(hop, 0) + 1
        tests = sorted((q for q in walked if (s := b.symbol_table.get(q)) is not None and b._in_test_file(s.file)),
                       key=lambda q: (walked[q], q))  # the most direct tests first
        ctx.impact = {"transitive_callers": len(walked), "by_hop": dict(sorted(by_hop.items())),
                      "tests": tests[:20], "tests_total": len(tests),
                      "entry_points_reached": sorted(q for q in walked if q in self.entries)[:10]}
        from prism.graph.references import references_to
        refs = references_to(b, anchor, include_tests=include_tests, skip={r.caller for r in callers})
        ctx.references_total = len(refs)
        all_refs = [{**r, "file": _rel(r["file"], self.root) if r["file"] else None} for r in refs]
        ctx.references = all_refs[:MAX_REFERENCES]
        self._fit_anchor(ctx, anchor, budget_tokens)
        self._fit_dependents(ctx, all_refs, budget_tokens)
        # callers' code under the budget: certain callers first, nearest code first
        spent = count_tokens(render(ctx))
        omitted = 0
        for report in callers:
            if view and seen(report.caller):
                s = b.symbol_table.get(report.caller)
                ctx.in_view.append((report.caller, report.file, tuple(s.line_range)))
                continue
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
        if new_code is not None:
            from prism.impact.breaks import BreakChecker
            ctx.breaks = BreakChecker(self.deps).check(anchor, new_code, include_tests=include_tests)
        ctx.token_count = count_tokens(render(ctx))
        return ctx


def _outline(builder, anchor: str, code: str) -> str:
    """The anchor's first line and the first line of each definition inside it, with line numbers."""
    sym = builder.symbol_table.get(anchor)
    lo = sym.line_range[0]
    lines = code.split("\n")
    keep = {lo}
    prefix = anchor + "."
    for q in builder._def_nodes:
        if q.startswith(prefix):
            s = builder.symbol_table.get(q)
            if s is None or s.file != sym.file or s.kind not in ("function", "method", "class"):
                continue
            first, last = s.line_range
            n = first
            while n < last and 0 <= n - lo < len(lines) and lines[n - lo].lstrip().startswith("@"):
                n += 1                                      # its own line, not its decorators'
            keep.add(n)
    return "\n".join(f"{n}: {lines[n - lo]}" for n in sorted(keep) if 0 <= n - lo < len(lines))


def parse_visible(specs) -> dict[str, list[tuple[int, int]] | None]:
    """`["a.py", "b.py:10-80"]` -> `{"a.py": None, "b.py": [(10, 80)]}` (None: the whole file is open)."""
    out: dict[str, list[tuple[int, int]] | None] = {}
    for spec in specs or ():
        path, sep, span = spec.rpartition(":")
        if sep and "-" in span and span.replace("-", "").isdigit():
            lo, hi = (int(x) for x in span.split("-", 1))
            if out.get(path, []) is not None:
                out.setdefault(path, []).append((lo, hi))
        else:
            out[spec] = None
    return out


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


GROUPED_INTRO = ("{n} definitions and modules depend on it, in {files} files - more than this answer holds one by "
                 "one: the nearest in full, the rest by file. Each place breaks if it is renamed, moved or removed.")


def render_caller(r) -> str:
    tag = " (test)" if r.is_test else ""
    cert = "" if r.certainty == CERTAIN else " - linked by name only, possible"
    return f"- `{r.caller}`{tag} ({r.file}){cert}"


def render_reference(r: dict) -> str:
    """One use other than a call: `- `pkg.mod` (module, pkg/mod.py:3, 12): imports it; lists it in `__all__``."""
    from prism.graph.references import describe_kinds
    where = r["file"] or "?"
    if r["lines"]:
        where += ":" + ", ".join(str(n) for n in r["lines"]) + (" ..." if r["count"] > len(r["lines"]) else "")
    tag = "module, " if r["is_module"] else ""
    test = " (test)" if r["is_test"] else ""
    return f"- `{r['source']}`{test} ({tag}{where}): {describe_kinds(r['kinds'])}"


def _fence(lang: str) -> str:
    return {"python": "python", "javascript": "javascript", "typescript": "typescript", "tsx": "tsx",
            "java": "java", "csharp": "csharp", "go": "go"}.get(lang.lower().rsplit(".", 1)[-1], "")


def render(ctx: ChangeContext) -> str:
    fence = _fence(ctx.language)
    out = [f"# Change context: `{ctx.anchor}`", f"{ctx.file}, lines {ctx.lines[0]}-{ctx.lines[1]}", ""]
    if ctx.breaks:  # nothing found: no section (a clean check is not an all-clear)
        from prism.impact.breaks import render_breaks
        out += render_breaks(ctx.breaks).rstrip("\n").split("\n") + [""]

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

    out += ["", "## 3. Anchor"]
    if ctx.anchor_in_view:
        out.append(f"Code: already in view ({ctx.file}, lines {ctx.lines[0]}-{ctx.lines[1]}).")
    elif ctx.anchor_outlined_lines:
        out += [f"Code: {ctx.anchor_outlined_lines} lines, too long for this answer; the first line of it and of each "
                f"definition inside it follow (prism_read_code `{ctx.anchor}` gives the full code).",
                f"```{fence}", ctx.anchor_code, "```"]
    else:
        out += [f"```{fence}", ctx.anchor_code, "```"]
    out += ["", "Digest (what its code does, line by line):", "```", ctx.anchor_digest, "```"]

    out += ["", "## 4. Who depends on it"]
    if ctx.dependents_grouped is not None:
        out += ctx.dependents_grouped
    elif not ctx.callers:
        out.append("No callers in the index (an entry point, a callback, or unused)." if not ctx.references
                   else "No calls to it in the index; its other uses are listed below.")
    for r in ctx.callers if ctx.dependents_grouped is None else ():
        out.append(render_caller(r))
        for d in r.dependencies:
            out.append(f"  - L{d.line} {d.aspect}: {d.text}")
    if ctx.references and ctx.dependents_grouped is None:
        out += ["", "Other uses (not calls) - each place breaks if it is renamed, moved or removed:"]
        out += [render_reference(r) for r in ctx.references]
        if ctx.references_total > len(ctx.references):
            out.append(f"- ... {ctx.references_total - len(ctx.references)} more")

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

    callers_in_view = [(q, f, lines) for q, f, lines in ctx.in_view if q != ctx.anchor]
    if ctx.caller_code or ctx.caller_digests or callers_in_view:
        out += ["", "## 7. Callers' code"]
        for q, f, lines in callers_in_view:
            out.append(f"`{q}`: already in view ({f}, lines {lines[0]}-{lines[1]}).")
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
