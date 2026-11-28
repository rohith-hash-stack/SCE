"""How code is written around an anchor function, so new code can follow it.

- `siblings`: the closest functions to the anchor (same class, then file, then directory), ranked by the
  index callees they share, name words, decorators and length. Their full code is the style example.
- `conventions`: patterns counted over a group's functions, each reported with its evidence (how many of
  how many): the exception types raised, the shared helpers, the logging calls, the naming style, async
  use, the decorators. A pattern is reported only when it is common enough to be a convention.
"""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass

from prism.impact.dependencies import raised_names
from prism.summaries.digest import CERTAIN, DigestBuilder

_LOG_LEAVES = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical", "log", "trace",
                         "Debug", "Info", "Warn", "Warning", "Error", "Printf", "Println", "LogDebug",
                         "LogInformation", "LogWarning", "LogError"})
_LOG_ROOTS = re.compile(r"^(self\.|this\.)?(_?log(ger)?|LOG(GER)?|logging|console|log\.[A-Za-z]+|Log|_logger)$", re.I)
MIN_EVIDENCE = 3


@dataclass(frozen=True)
class Sibling:
    qname: str
    file: str
    lines: tuple[int, int]
    reason: str
    code: str


@dataclass(frozen=True)
class Convention:
    text: str
    count: int
    total: int


def _words(name: str) -> set[str]:
    leaf = name.rsplit(".", 1)[-1].split("#", 1)[0]
    parts = re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", leaf)
    return {p.lower() for p in parts if len(p) > 1}


def _callees(builder, qname: str) -> set[str]:
    if qname not in builder.graph:
        return set()
    return {t for _u, t, d in builder.graph.out_edges(qname, data=True)
            if d.get("relation", "CALLS") == "CALLS" and t in builder.symbol_table}


def _functions(builder) -> list[str]:
    return [q for q in builder._def_nodes
            if (s := builder.symbol_table.get(q)) is not None and s.kind in ("function", "method")]


def source_of(builder, qname: str) -> str:
    sym = builder.symbol_table.get(qname)
    parsed = builder.parsed_file(sym.file) if sym is not None else None
    if parsed is None:
        return ""
    lines = parsed.source.decode("utf8", "replace").splitlines()
    lo, hi = sym.line_range
    return "\n".join(lines[lo - 1:hi])


def siblings(builder, anchor: str, group_of, k: int = 2, max_lines: int = 60, exclude=frozenset()) -> list[Sibling]:
    """The `k` closest functions to `anchor` (never test code unless the anchor is a test; never one in
    `exclude`, such as the callers whose code is shown elsewhere)."""
    sym = builder.symbol_table.get(anchor)
    if sym is None:
        return []
    anchor_is_test = bool(builder._in_test_file(sym.file))
    anchor_group = group_of(anchor)
    anchor_callees = _callees(builder, anchor)
    anchor_words = _words(anchor)
    anchor_len = sym.line_range[1] - sym.line_range[0] + 1
    anchor_decorators = _decorators(builder, anchor)
    scored = []
    for q in _functions(builder):
        if q == anchor or q in exclude:
            continue
        s = builder.symbol_table.get(q)
        if s.language_id != sym.language_id or (not anchor_is_test and builder._in_test_file(s.file)):
            continue
        same_class = sym.enclosing_class is not None and s.enclosing_class == sym.enclosing_class
        same_file = s.file == sym.file
        if not (same_class or same_file or group_of(q) == anchor_group):
            continue
        length = s.line_range[1] - s.line_range[0] + 1
        if length < 3:
            continue
        shared = anchor_callees & _callees(builder, q)
        union = anchor_callees | _callees(builder, q)
        words = anchor_words & _words(q)
        decorators = anchor_decorators & _decorators(builder, q)
        score = (3.0 * (len(shared) / len(union) if union else 0) + 2.0 * same_class + 1.0 * same_file
                 + 0.5 * len(words) + 0.5 * bool(decorators) - 0.3 * abs(length - anchor_len) / max(anchor_len, 10))
        reason = ", ".join(x for x in (
            "same class" if same_class else ("same file" if same_file else "same directory"),
            f"shares {len(shared)} callee{'s' if len(shared) != 1 else ''}" if shared else "",
            ("name words: " + ", ".join(sorted(words))) if words else "",
            ("same decorators: " + ", ".join(sorted(decorators))) if decorators else "") if x)
        scored.append((score, q, reason))
    scored.sort(key=lambda t: (-t[0], t[1]))
    out = []
    for _score, q, reason in scored[:k]:
        s = builder.symbol_table.get(q)
        code = source_of(builder, q).splitlines()
        if len(code) > max_lines:
            code = code[:max_lines] + [f"... ({len(code) - max_lines} more lines)"]
        out.append(Sibling(q, s.file, tuple(s.line_range), reason, "\n".join(code)))
    return out


def _decorators(builder, qname: str) -> set[str]:
    from prism.parser.lang_config import collect_annotation_texts, collect_decorator_texts
    node = builder.def_node(qname)
    sym = builder.symbol_table.get(qname)
    parsed = builder.parsed_file(sym.file) if sym is not None else None
    if node is None or parsed is None:
        return set()
    texts = collect_decorator_texts(node, parsed) or collect_annotation_texts(node, parsed)
    return {t.lstrip("@[").split("(", 1)[0].rstrip("]").strip() for t in texts}


def _top(counter: collections.Counter, k: int) -> list[tuple[str, int]]:
    """The k most common, ties broken by name (a Counter's own order follows set order, which varies by run)."""
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:k]


def conventions(builder, functions: list[str], digests: DigestBuilder, cap: int = 600) -> list[Convention]:
    """Conventions of a set of functions (a group), most common first, each with its evidence."""
    functions = sorted(functions)[:cap]
    total = len(functions)
    if total < MIN_EVIDENCE:
        return []
    raised: collections.Counter = collections.Counter()
    raise_total = 0
    helpers: collections.Counter = collections.Counter()
    loggers: collections.Counter = collections.Counter()
    decorators: collections.Counter = collections.Counter()
    snake = camel = is_async = 0
    for q in functions:
        d = digests.digest(q)
        names = raised_names(d)
        raise_total += len(names)
        raised.update(names)
        for t in {t for f in d.facts if f.kind in ("call", "calls") and f.certainty == CERTAIN
                  for t in f.targets if t in builder.symbol_table}:  # name-only matches are not evidence
            helpers[t] += 1
        for f in d.facts:
            if f.kind == "call":
                m = re.match(r"(?:await )?([\w$.]+)\.(\w+)\(", f.text)
                if m and m.group(2) in _LOG_LEAVES and _LOG_ROOTS.match(m.group(1)):
                    loggers[f"{m.group(1)}.{m.group(2)}"] += 1
        decorators.update(_decorators(builder, q))
        leaf = q.rsplit(".", 1)[-1].split("#", 1)[0].lstrip("_")
        if "_" in leaf and leaf.lower() == leaf:
            snake += 1
        elif re.match(r"^[a-z]+[A-Z]", leaf):
            camel += 1
        node = builder.def_node(q)
        if node is not None and any(c.type == "async" for c in node.children):
            is_async += 1
    out: list[Convention] = []
    for name, n in _top(raised, 3):
        if n >= MIN_EVIDENCE and n / max(raise_total, 1) >= 0.25:
            out.append(Convention(f"errors are raised as `{name}` ({n} of {raise_total} raises here)", n, raise_total))
    for target, n in _top(helpers, 5):
        if n >= MIN_EVIDENCE and n / total >= 0.05:
            out.append(Convention(f"`{target}` is a shared helper here: called by {n} of {total} functions", n, total))
    log_total = sum(loggers.values())
    if log_total >= MIN_EVIDENCE:
        top = ", ".join(f"`{k}`" for k, _ in _top(loggers, 3))
        out.append(Convention(f"logging goes through {top} ({log_total} calls)", log_total, log_total))
    named = snake + camel
    if named >= MIN_EVIDENCE:
        if snake / named >= 0.8:
            out.append(Convention(f"function names are snake_case ({snake} of {named} multi-word names)", snake, named))
        elif camel / named >= 0.8:
            out.append(Convention(f"function names are camelCase ({camel} of {named} multi-word names)", camel, named))
    if is_async >= MIN_EVIDENCE:
        out.append(Convention(f"{is_async} of {total} functions are async", is_async, total))
    for name, n in _top(decorators, 3):
        if n >= MIN_EVIDENCE and n / total >= 0.1:
            out.append(Convention(f"`@{name}` is used on {n} of {total} functions", n, total))
    return out
