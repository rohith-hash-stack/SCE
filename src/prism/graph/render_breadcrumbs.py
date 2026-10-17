"""Breadcrumbs: how a request (or command, task, ...) reaches a slice of
context, shown next to it without ever re-describing it.

For a slice `S` (the functions in a context), each **root** of `S` - a
member no other member calls - gets at most one path
`[entry, outside..., root]`: the nearest entry point (`prism.graph.
entry_points`) above the root, found without stepping through any other
member of `S`. Rendered:

    [Entry: POST /users] -> register_user() -> create_account() => enters at insert_user()

Invariants (`validate`), enforced on every breadcrumb:

1. every hop but the last lies outside `S`; the last is the root;
2. the root is a root of `S` (no other member of `S` calls it);
3. the head is a registered entry point and each hop calls the next
   through a call the code makes (never an override fan-out or callback
   guess - `prism.graph.entry_points._definite`);
4. one path per root, no duplicate text, and the slice's breadcrumbs fit
   `MAX_TOKENS` in total.

A violation drops the breadcrumb, or raises `BreadcrumbInvariantError` in
strict mode (`strict=True`, or `PRISM_BREADCRUMB_STRICT=1` - set by the
test suite). Rendering is inline (`inline`): an `<entry_context>` line as the
first child of the root's `<node>` in a rendered context, removable
byte-exactly with `strip`.
"""
from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass

from prism.graph.entry_points import _callers, _definite, format_entry_context

MAX_TOKENS = 80
_LINE = re.compile(r"^[ \t]*<entry_context>.*?</entry_context>\n", re.M)


class BreadcrumbInvariantError(AssertionError):
    pass


@dataclass(frozen=True)
class Breadcrumb:
    root: str
    chain: tuple[str, ...]  # (entry, ..., root)
    text: str
    tokens: int


def _strict(strict: bool | None) -> bool:
    return strict if strict is not None else os.environ.get("PRISM_BREADCRUMB_STRICT") == "1"


def _tokens(text: str) -> int:
    from prism.slicer.tokenizer import count_tokens

    return count_tokens(text)


def slice_members(index, symbols) -> list[str]:
    """The slice's functions and methods (classes carry no breadcrumbs), in order."""
    return [s for s in dict.fromkeys(symbols) if s in index.functions]


def slice_roots(index, symbols) -> list[str]:
    members = slice_members(index, symbols)
    inside = set(members)
    graph = index.builder.graph
    return [s for s in members if not any(c in inside and c != s for c in _callers(graph, s, index.functions))]


def validate(crumb: Breadcrumb, members: set[str], index, max_tokens: int = MAX_TOKENS) -> list[str]:
    """The invariants `crumb` breaks for the slice `members` (empty = valid)."""
    problems = []
    chain = list(crumb.chain)
    graph = index.builder.graph
    if not chain or chain[-1] != crumb.root or crumb.root not in members:
        problems.append("the path does not end at its root, or the root is not in the slice")
    if any(q in members for q in chain[:-1]):
        problems.append("a hop before the root lies inside the slice")
    if any(c in members and c != crumb.root for c in _callers(graph, crumb.root, index.functions)):
        problems.append("the path enters the slice mid-chain (the root has a caller in the slice)")
    if not chain or chain[0] not in index.entries:
        problems.append("the head is not a registered entry point")
    for a, b in zip(chain, chain[1:]):
        if a not in graph or not any(v == b and _definite(d) for _, v, d in graph.out_edges(a, data=True)):
            problems.append(f"no resolved call edge {a} -> {b}")
    if chain and chain[0] in index.entries and crumb.text != format_entry_context(chain, index.entries):
        problems.append("the text does not describe the path")
    if crumb.tokens > max_tokens:
        problems.append(f"{crumb.tokens} tokens > {max_tokens}")
    return problems


def slice_breadcrumbs(index, symbols, max_tokens: int = MAX_TOKENS, strict: bool | None = None) -> list[Breadcrumb]:
    """Valid breadcrumbs for the slice `symbols`, roots in slice order, at
    most one per root, `max_tokens` in total (later ones that do not fit are
    left out)."""
    strict = _strict(strict)
    members = set(slice_members(index, symbols))
    out: list[Breadcrumb] = []
    seen: set[str] = set()
    total = 0
    for root in slice_roots(index, symbols):
        paths = index.paths(root, blocked=members - {root})
        if not paths:
            continue
        chain = tuple(paths[0])
        text = format_entry_context(list(chain), index.entries)
        crumb = Breadcrumb(root, chain, text, _tokens(text))
        if text in seen or total + crumb.tokens > max_tokens:
            continue  # the budget is a packing decision, not a violation
        problems = validate(crumb, members, index, max_tokens)
        if problems:
            if strict:
                raise BreadcrumbInvariantError(f"{root}: {'; '.join(problems)}")
            continue
        seen.add(text)
        total += crumb.tokens
        out.append(crumb)
    return out


def inline(rendered: str, crumbs: list[Breadcrumb]) -> tuple[str, list[Breadcrumb]]:
    """`rendered` (a `<context>` document) with each breadcrumb as an
    `<entry_context>` line right after its root's `<node ...>` tag, and the
    breadcrumbs actually placed (a root not rendered places nothing)."""
    placed = []
    for crumb in crumbs:
        tag = re.compile(r'^([ \t]*)<node\b[^\n>]*\bid="' + re.escape(html.escape(crumb.root, quote=True)) + r'"[^\n>]*>\n', re.M)
        m = tag.search(rendered)
        if m is None:
            continue
        text = crumb.text.replace("&", "&amp;").replace("<", "&lt;")  # `>` is legal XML text: keep `->`/`=>` readable
        line = f"{m.group(1)}  <entry_context>{text}</entry_context>\n"
        rendered = rendered[:m.end()] + line + rendered[m.end():]
        placed.append(crumb)
    return rendered, placed


def strip(rendered: str) -> str:
    """`rendered` without any `<entry_context>` line - byte-identical to the
    context before `inline`."""
    return _LINE.sub("", rendered)
