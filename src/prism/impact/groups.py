"""Layers 4 and 5: the group an anchor belongs to, and the codebase it sits in - computed, not written.

A group is a source directory (`prism.graph.subsystems.module_of`), the partition PRISM already uses for
subsystem profiles. For each group: its size, entry points, the functions other groups call (its API),
the groups it calls and is called by, and its conventions. The codebase summary lists the largest
groups and the strongest group-to-group dependencies.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field

from prism.graph.subsystems import module_of


@dataclass
class GroupSummary:
    name: str
    files: int
    functions: int
    classes: int
    entry_points: list[str] = field(default_factory=list)
    api: list[tuple[str, int]] = field(default_factory=list)            # (function, callers from other groups)
    depends_on: list[tuple[str, int]] = field(default_factory=list)     # (group, call edges)
    used_by: list[tuple[str, int]] = field(default_factory=list)
    conventions: list = field(default_factory=list)


class GroupIndex:
    def __init__(self, builder, repo_root: str, entries=None):
        self.b, self.repo_root = builder, repo_root
        self.entries = entries if entries is not None else {}
        self.group: dict[str, str] = {}
        self.members: dict[str, list[str]] = collections.defaultdict(list)
        self.classes: collections.Counter = collections.Counter()
        self.files: dict[str, set[str]] = collections.defaultdict(set)
        for q in builder._def_nodes:
            sym = builder.symbol_table.get(q)
            if sym is None or builder._in_test_file(sym.file):
                continue
            g = module_of(sym.file, repo_root)
            self.group[q] = g
            self.files[g].add(sym.file)
            if sym.kind in ("function", "method"):
                self.members[g].append(q)
            elif sym.kind in ("class", "interface"):
                self.classes[g] += 1
        self.edges: collections.Counter = collections.Counter()
        self.external_callers: collections.Counter = collections.Counter()
        for u, v, d in builder.graph.edges(data=True):
            if d.get("relation", "CALLS") not in ("CALLS", "INSTANTIATES"):
                continue
            gu, gv = self.group.get(u), self.group.get(v)
            if gu and gv and gu != gv:
                self.edges[(gu, gv)] += 1
                self.external_callers[v] += 1

    def of(self, qname: str) -> str:
        if qname in self.group:
            return self.group[qname]
        sym = self.b.symbol_table.get(qname)
        return module_of(sym.file, self.repo_root) if sym is not None else "?"

    def summary(self, name: str, digests=None) -> GroupSummary:
        from prism.impact.style import conventions
        members = self.members.get(name, [])
        s = GroupSummary(name, len(self.files.get(name, ())), len(members), self.classes.get(name, 0))
        s.entry_points = sorted(q for q in members if q in self.entries)[:10]
        s.api = sorted(((q, self.external_callers[q]) for q in members if self.external_callers[q]),
                       key=lambda t: (-t[1], t[0]))[:8]
        s.depends_on = sorted(((v, n) for (u, v), n in self.edges.items() if u == name), key=lambda t: (-t[1], t[0]))[:6]
        s.used_by = sorted(((u, n) for (u, v), n in self.edges.items() if v == name), key=lambda t: (-t[1], t[0]))[:6]
        if digests is not None:
            s.conventions = conventions(self.b, members, digests)
        return s

    def codebase(self, top: int = 12) -> dict:
        langs = collections.Counter(self.b.symbol_table.get(q).language_id for g in self.members.values() for q in g)
        groups = sorted(self.members, key=lambda g: (-len(self.members[g]), g))
        return {
            "functions": sum(len(v) for v in self.members.values()),
            "groups": len(self.members),
            "languages": dict(langs.most_common()),
            "largest": [(g, len(self.members[g]), sum(1 for q in self.members[g] if q in self.entries))
                        for g in groups[:top]],
            "flows": [(u, v, n) for (u, v), n in self.edges.most_common(top)],
            "entry_points": sum(1 for g in self.members.values() for q in g if q in self.entries),
        }
