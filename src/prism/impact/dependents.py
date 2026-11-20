"""Who depends on a symbol, listed under a token budget (Phase 2, after B10; `harness/experiments/graph_coverage/
PROGRESS.md`, "Why a surfaced use is not shown").

A widely used definition (a `Request` class) has hundreds of callers and other uses, more than one tool answer holds:
listed one by one, the list was cut at the answer's cap and the uses past it were never seen. Here:

- **When the full listing fits its budget, it is given unchanged** (`full` lines, exactly as before).
- **When it does not:** the nearest uses are given in full (the same lines), and every other one grouped by file,
  with the kinds of use and the names of the definitions or modules that use it, the rest counted. A file past the
  budget is counted in a last line, so every use is accounted for. Of the settings tried (rows in full, names per
  file), the one that names the most uses within the budget is kept.

"Nearest" means production code before tests, then the files closest to the symbol's own file in the directory tree
(a sibling module before a distant package), then the most uses.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

#: Short labels for the grouped lines (the full lines use `prism.graph.references.KIND_LABELS`).
SHORT_LABELS = {
    "calls": "calls it", "calls (possible)": "may call it", "import": "imports it", "export": "`__all__`",
    "base": "subclasses it", "isinstance": "isinstance", "except": "except", "annotation": "type annotation",
    "generic": "type parameter", "decorator": "decorator", "call": "calls it (module level)", "value": "value use",
    "attribute": "attribute read",
}
#: Settings tried in order, most detail first: (rows in full, names per file).
_STEPS = ((10, 6), (6, 4), (4, 3), (3, 2), (2, 1), (1, 1), (0, 1), (0, 0))


@dataclass
class Row:
    source: str
    file: str | None
    kinds: list[str]                       # SHORT_LABELS keys
    full: list[str] = field(default_factory=list)  # its line(s) in the full listing
    is_test: bool = False
    is_module: bool = False
    rank: tuple = ()                       # extra sort key ahead of distance (the blast radius's hop)


def _distance(a: str | None, b: str | None) -> int:
    """Steps between two files in the directory tree (0: the same file)."""
    if not a or not b:
        return 99
    if a == b:
        return 0
    pa, pb = os.path.dirname(a).split("/"), os.path.dirname(b).split("/")
    common = 0
    for x, y in zip(pa, pb):
        if x != y:
            break
        common += 1
    return (len(pa) - common) + (len(pb) - common) + 1


def nearest_first(rows: list[Row], anchor_file: str | None) -> list[Row]:
    return sorted(rows, key=lambda r: (r.is_test, *r.rank, _distance(anchor_file, r.file), r.source))


def _file_line(path: str, rows: list[Row], names: int) -> str:
    by_kind: dict[str, list[str]] = {}
    for r in rows:
        for k in r.kinds:
            by_kind.setdefault(k, []).append(r.source)
    parts = []
    for k, srcs in sorted(by_kind.items(), key=lambda x: (-len(x[1]), x[0])):
        label = SHORT_LABELS.get(k, k)
        shown = srcs[:names]
        part = f"{label}" + (f" ({len(srcs)})" if len(srcs) > 1 else "")
        if shown:
            part += ": " + ", ".join(f"`{s}`" for s in shown) + (f" +{len(srcs) - len(shown)}" if len(srcs) > len(shown) else "")
        parts.append(part)
    test = " (test)" if all(r.is_test for r in rows) else ""
    return f"- {path}{test} ({len(rows)}): " + "; ".join(parts)


def grouped(rows: list[Row], anchor_file: str | None, budget: int, count, intro: str) -> list[str]:
    """The nearest rows in full and the rest grouped by file, in at most `budget` tokens (`count` counts them)."""
    rows = nearest_first(rows, anchor_file)
    files = sorted({r.file or "?" for r in rows})
    head = [intro.format(n=len(rows), files=len(files))]
    best: tuple[int, list[str]] | None = None
    for n_full, names in _STEPS:
        lines = list(head)
        for r in rows[:n_full]:
            lines += r.full
        rest = rows[n_full:]
        if rest:
            order: list[str] = []
            by_file: dict[str, list[Row]] = {}
            for r in rest:                                  # files in the order of their nearest row
                f = r.file or "?"
                if f not in by_file:
                    order.append(f)
                by_file.setdefault(f, []).append(r)
            lines.append("By file (the rest; " + ("names" if names else "counts") + "):")
            used = count("\n".join(lines))
            left = []
            for i, f in enumerate(order):
                ln = _file_line(f, by_file[f], names)
                cost = count(ln) + 1
                if used + cost > budget - 30:               # room for the closing count
                    left = order[i:]
                    break
                lines.append(ln)
                used += cost
            if left:
                n_uses = sum(len(by_file[f]) for f in left)
                lines.append(f"- ... {len(left)} more files ({n_uses} more): "
                             + ", ".join(left[:8]) + (" ..." if len(left) > 8 else ""))
        if not rest or (count("\n".join(lines)) <= budget and not lines[-1].startswith("- ... ")):
            if n_full == _STEPS[0][0] and names == _STEPS[0][1] or not rest:
                return lines                                # the most detailed setting fits whole
        if count("\n".join(lines)) > budget:
            continue
        named = sum(ln.count("`") for ln in lines) // 2   # uses named (in full or in a file line)
        if best is None or named > best[0]:
            best = (named, lines)
    return best[1] if best else head


def listing(rows: list[Row], anchor_file: str | None, budget: int, count, intro: str) -> tuple[list[str], bool]:
    """`(lines, grouped?)`: every row's full lines when they fit in `budget`, else `grouped(...)`."""
    full = [ln for r in rows for ln in r.full]
    if count("\n".join(full)) <= budget:
        return full, False
    return grouped(rows, anchor_file, budget, count, intro), True
