"""Layer 3: how code depends on one function (the anchor), read from the syntax at each call site.

For every direct caller of the anchor, each call site is read for what the caller relies on:

    result      what the caller does with the return value: ignores it, branches on it, returns it,
                passes it on, binds it to a name and then checks / reads / iterates it
    errors      which of the anchor's own `raise` / `throw` exceptions the caller catches around the call,
                and which it lets propagate to its own callers
    arguments   how many positional arguments and which keyword names it passes
    async       whether it awaits the call

For every function the anchor itself calls (resolved to an index symbol), the reverse: what that callee
can raise and whether the anchor catches it, when it returns nothing / `None`, and what state it changes.

Every statement cites its file and line and carries the certainty of the call edge it came from: a
caller linked only by a tentative (name-only) graph edge gives `possible` statements. Nothing is inferred
beyond the syntax at the call site.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from prism.parser.lang_config import CALL_NODE_TYPE, call_callee_segments, iter_scoped_nodes
from prism.parser.tree_sitter_loader import LanguageID
from prism.summaries.digest import CERTAIN, POSSIBLE, DigestBuilder, _strip_parens, catch_type

_TRY = frozenset({"try_statement"})
_CATCH = frozenset({"except_clause", "catch_clause"})
_AWAIT = frozenset({"await", "await_expression"})
_ASSIGN = frozenset({"assignment", "assignment_expression", "augmented_assignment", "assignment_statement"})
_DECLARE = frozenset({"variable_declarator", "short_var_declaration"})
_CONDITION_OWNERS = frozenset({"if_statement", "elif_clause", "while_statement", "conditional_expression",
                               "ternary_expression", "assert_statement", "do_statement"})
_STATEMENT_ENDS = frozenset({"expression_statement", "return_statement", "block", "statement_block",
                             "module", "program", "class_body"})
_ATTRS = frozenset({"attribute", "member_expression", "selector_expression", "field_access",
                    "member_access_expression"})
_SUBSCRIPTS = frozenset({"subscript", "subscript_expression", "index_expression", "element_access_expression"})
_LOOPS = frozenset({"for_statement", "for_in_statement", "enhanced_for_statement", "foreach_statement"})
#: catch clauses that catch everything
_CATCH_ALL = re.compile(r"^(any exception|any error.*|Exception|BaseException|Throwable|\(?Exception\)?)$")
_RAISED_NAME = re.compile(r"^(?:raise|throw)\s+(?:new\s+)?([A-Za-z_$][\w$.]*)")
CAP = 6  # names / attributes / conditions listed per statement


@dataclass(frozen=True)
class Dependency:
    """One way code depends on another function, cited at a call site."""
    owner: str          # the function that depends (a caller of the anchor, or the anchor itself)
    on: str             # the function depended on (the anchor, or one of its callees)
    aspect: str         # "result" | "errors" | "arguments" | "async"
    text: str
    file: str
    line: int
    certainty: str = CERTAIN


@dataclass
class CallerReport:
    caller: str
    file: str
    certainty: str
    is_test: bool
    dependencies: list[Dependency] = field(default_factory=list)


def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf8", "replace")


def _short(text: str, cap: int = 90) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def raised_names(digest) -> list[str]:
    """Exception names the function raises itself (`raise X(...)`, `throw new X(...)`), in order."""
    names = []
    for f in digest.facts:
        if f.kind == "raise":
            m = _RAISED_NAME.match(f.text)
            if m and m.group(1) not in names:
                names.append(m.group(1))
    return names


def _call_sites(builder, def_node, parsed, target: str) -> list:
    """Call nodes in `def_node` whose callee's last name is the target's."""
    lang, src = parsed.language_id, parsed.source
    leaf = target.rsplit(".", 1)[-1].split("#", 1)[0]
    call_type = CALL_NODE_TYPE.get(lang)
    if leaf in ("__init__", "constructor"):
        leaf = target.rsplit(".", 2)[-2]  # `Foo(...)` / `new Foo(...)` constructs it
    sites = []
    types = {call_type} if call_type else set()
    types |= {"new_expression", "object_creation_expression"}
    for call in iter_scoped_nodes(def_node, types, lang):
        if call.type == call_type:
            seg = call_callee_segments(call, src, lang)
            name = seg[-1] if seg else _callee_tail(call, src)
        else:
            ctor = call.child_by_field_name("constructor") or call.child_by_field_name("type")
            name = _callee_tail_text(_text(ctor, src)) if ctor is not None else ""
        if name == leaf:
            sites.append(call)
    return sites


def _callee_tail(call, src: bytes) -> str:
    func = call.child_by_field_name("function") or call.child_by_field_name("name")
    return _callee_tail_text(_text(func, src)) if func is not None else ""


def _callee_tail_text(text: str) -> str:
    m = re.search(r"([A-Za-z_$][\w$]*)\s*(<[^<>]*>)?\s*$", text.strip().rstrip("?!."))
    return m.group(1) if m else ""


def _expression_parent(call):
    """The node that consumes the call's value (skipping `await`, parentheses, casts)."""
    node, parent = call, call.parent
    while parent is not None and parent.type in _AWAIT | {"parenthesized_expression", "as_expression",
                                                           "non_null_expression", "cast_expression"}:
        node, parent = parent, parent.parent
    return node, parent


def _awaited(call) -> bool:
    parent = call.parent
    while parent is not None and parent.type == "parenthesized_expression":
        parent = parent.parent
    return parent is not None and parent.type in _AWAIT


def _condition_of(node, stop) -> str | None:
    """The condition text when `node` sits in an if / while / ternary / assert condition."""
    child, parent = node, node.parent
    while parent is not None and parent.id != stop.id:
        if parent.type in _CONDITION_OWNERS:
            cond = parent.child_by_field_name("condition")
            if parent.type == "assert_statement" and parent.named_children:
                cond = parent.named_children[0]
            if cond is not None and cond.start_byte <= child.start_byte and child.end_byte <= cond.end_byte:
                return cond
            return None
        if parent.type in _STATEMENT_ENDS:
            return None
        child, parent = parent, parent.parent
    return None


def _bound_name(consumer, parent, src: bytes) -> str | None:
    if parent is None:
        return None
    if parent.type in _ASSIGN:
        left = parent.child_by_field_name("left")
        right = parent.child_by_field_name("right")
        if right is not None and right.id == consumer.id and left is not None:
            if left.type == "expression_list" and left.named_children:  # Go `v, err := f()`
                left = left.named_children[0]
            return _text(left, src)
    if parent.type in _DECLARE:
        name = parent.child_by_field_name("name") or parent.child_by_field_name("left")
        if name is not None:
            if name.type == "expression_list" and name.named_children:
                name = name.named_children[0]
            return _text(name, src)
    if parent.type == "expression_list" and parent.parent is not None:  # Go: right-hand side list
        return _bound_name(parent, parent.parent, src)
    return None


def _uses_of(name: str, def_node, after_byte: int, lang, src: bytes) -> dict[str, list[str]]:
    """How a bound result is used later in the function: conditions, attributes, keys, loops, calls, returns."""
    uses: dict[str, list[str]] = {"checks": [], "reads": [], "keys": [], "iterates": [], "passes": [], "returns": []}
    ident_types = {"identifier"}
    stack, steps = [def_node], 0
    while stack and steps < 50000:
        steps += 1
        n = stack.pop()
        stack.extend(n.named_children)
        if n.type not in ident_types or n.start_byte < after_byte or _text(n, src) != name:
            continue
        parent = n.parent
        if parent is None:
            continue
        if parent.type in _ATTRS and parent.named_children and parent.named_children[0].id != n.id:
            continue  # `response.content`: the attribute name, not the variable `content`
        if parent.type in ("keyword_argument", "pair") and parent.child_by_field_name("name") is not None and \
                parent.child_by_field_name("name").id == n.id:
            continue  # `f(content=...)`: a keyword name
        cond = _condition_of(n, def_node)
        if cond is not None:
            uses["checks"].append(_short(_strip_parens(_text(cond, src)), 70))
        if parent.type in _ATTRS and parent.named_children and parent.named_children[0].id == n.id:
            prop = parent.child_by_field_name("attribute") or parent.child_by_field_name("property") or \
                parent.child_by_field_name("field") or parent.child_by_field_name("name")
            if prop is not None:
                uses["reads"].append("." + _text(prop, src))
        elif parent.type in _SUBSCRIPTS and parent.named_children and parent.named_children[0].id == n.id:
            key = parent.child_by_field_name("subscript") or parent.child_by_field_name("index")
            if key is not None:
                uses["keys"].append(f"[{_short(_text(key, src), 30)}]")
        elif parent.type in _LOOPS:
            uses["iterates"].append("loop")
        elif parent.type in ("argument_list", "arguments") and parent.parent is not None:
            callee = parent.parent.child_by_field_name("function") or parent.parent.child_by_field_name("name")
            if callee is not None:
                uses["passes"].append(_short(_text(callee, src), 40) + "()")
        elif parent.type == "return_statement" or (parent.type == "expression_list" and parent.parent is not None
                                                   and parent.parent.type == "return_statement"):
            uses["returns"].append("return")
    return {k: list(dict.fromkeys(v))[:CAP] for k, v in uses.items() if v}


def _result_statement(call, def_node, src: bytes, lang) -> str:
    consumer, parent = _expression_parent(call)
    cond = _condition_of(consumer, def_node)
    if cond is not None:
        return f"branches on the result: `{_short(_strip_parens(_text(cond, src)), 70)}`"
    if parent is None:
        return "uses the result"
    if parent.type in ("expression_statement",):
        return "ignores the return value"
    if parent.type == "return_statement" or (parent.type == "expression_list" and parent.parent is not None
                                             and parent.parent.type == "return_statement"):
        return "returns the result to its own callers"
    if parent.type in _ATTRS and parent.named_children and parent.named_children[0].id == consumer.id:
        prop = parent.child_by_field_name("attribute") or parent.child_by_field_name("property") or \
            parent.child_by_field_name("field") or parent.child_by_field_name("name")
        return f"reads `.{_text(prop, src)}` of the result" if prop is not None else "reads the result"
    if parent.type in _SUBSCRIPTS:
        return "indexes into the result"
    if parent.type in ("argument_list", "arguments"):
        outer = parent.parent
        callee = (outer.child_by_field_name("function") or outer.child_by_field_name("name")) if outer is not None else None
        return f"passes the result to `{_short(_text(callee, src), 40)}()`" if callee is not None else "passes the result on"
    if parent.type in _LOOPS:
        return "iterates over the result"
    name = _bound_name(consumer, parent, src)
    if name:
        uses = _uses_of(name.split(".")[-1] if "." not in name else name, def_node, call.end_byte, lang, src) \
            if re.fullmatch(r"[A-Za-z_$][\w$]*", name) else {}
        if not uses:
            return f"stores the result in `{_short(name, 40)}`"
        parts = []
        if "checks" in uses:
            parts.append("checks " + "; ".join(f"`{c}`" for c in uses["checks"]))
        if "reads" in uses:
            parts.append("reads " + ", ".join(uses["reads"]))
        if "keys" in uses:
            parts.append("indexes " + ", ".join(uses["keys"]))
        if "iterates" in uses:
            parts.append("iterates over it")
        if "passes" in uses:
            parts.append("passes it to " + ", ".join(f"`{p}`" for p in uses["passes"]))
        if "returns" in uses:
            parts.append("returns it")
        return f"stores the result in `{_short(name, 40)}`, then " + "; ".join(parts)
    return "uses the result in an expression"


def _enclosing_catches(call, def_node, src: bytes) -> list[str] | None:
    """What the try / catch blocks around the call catch (innermost first); None when it is not in a try body."""
    catches: list[str] = []
    child, parent = call, call.parent
    inside = False
    while parent is not None and parent.id != def_node.id:
        if parent.type in _TRY:
            body = parent.child_by_field_name("body")
            if body is not None and body.start_byte <= child.start_byte and child.end_byte <= body.end_byte:
                inside = True
                catches.extend(catch_type(c, src) for c in parent.named_children if c.type in _CATCH)
        child, parent = parent, parent.parent
    return catches if inside else None


def _catches(catches: list[str], exception: str) -> bool:
    leaf = exception.rsplit(".", 1)[-1]
    for c in catches:
        if _CATCH_ALL.match(c.strip()):
            return True
        if re.search(rf"\b{re.escape(leaf)}\b", c):
            return True
    return False


def _arguments_statement(call, src: bytes) -> str | None:
    args = call.child_by_field_name("arguments")
    if args is None:
        return None
    positional, keywords, spread = 0, [], False
    for a in args.named_children:
        if a.type == "comment":
            continue
        if a.type == "keyword_argument":
            name = a.child_by_field_name("name")
            if name is not None:
                keywords.append(_text(name, src))
        elif a.type in ("list_splat", "dictionary_splat", "spread_element"):
            spread = True
        else:
            positional += 1
    if not positional and not keywords and not spread:
        return None  # a call with no arguments depends on no parameter
    parts = [f"{positional} positional argument{'s' if positional != 1 else ''}"] if positional else []
    if keywords:
        parts.append("keywords " + ", ".join(f"`{k}`" for k in keywords[:CAP]))
    if spread:
        parts.append("spread / unpacked arguments")
    return "passes " + ", ".join(parts)


def _site_dependencies(owner: str, on: str, call, def_node, parsed, raised: list[str], certainty: str,
                       file: str) -> list[Dependency]:
    src, lang = parsed.source, parsed.language_id
    line = call.start_point[0] + 1
    deps = [Dependency(owner, on, "result", _result_statement(call, def_node, src, lang), file, line, certainty)]
    catches = _enclosing_catches(call, def_node, src)
    for exc in raised:
        if catches is not None and _catches(catches, exc):
            text = f"catches `{exc}` raised by it (around the call: {', '.join(catches)})"
        else:
            text = f"does not catch `{exc}` raised by it: the error propagates to this function's callers"
        deps.append(Dependency(owner, on, "errors", text, file, line, certainty))
    if catches is not None and not raised:
        deps.append(Dependency(owner, on, "errors", f"calls it inside a try block catching {', '.join(catches)}",
                               file, line, certainty))
    args = _arguments_statement(call, src)
    if args:
        deps.append(Dependency(owner, on, "arguments", args, file, line, certainty))
    if _awaited(call):
        deps.append(Dependency(owner, on, "async", "awaits it", file, line, certainty))
    return deps


def _is_async(node) -> bool:
    if node is None:
        return False
    return any(c.type == "async" for c in node.children)


class DependencyAnalyzer:
    """Caller and callee dependencies of anchors in one index (sharing one DigestBuilder)."""

    def __init__(self, builder, digests: DigestBuilder | None = None, repo_root: str | None = None):
        self.b = builder
        self.digests = digests or DigestBuilder(builder)
        self.repo_root = repo_root

    def _rel(self, path: str) -> str:
        if self.repo_root:
            try:
                return os.path.relpath(path, self.repo_root)
            except ValueError:
                return path
        return path

    def _callers(self, anchor: str) -> list[tuple[str, str]]:
        """`(caller, certainty)` for each direct caller, certain edges first."""
        graph = self.b.graph
        if anchor not in graph:
            return []
        found: dict[str, str] = {}
        for caller, _t, d in graph.in_edges(anchor, data=True):
            if caller.startswith("<") or d.get("relation", "CALLS") not in ("CALLS", "INSTANTIATES"):
                continue
            cert = POSSIBLE if d.get("kind") == "TENTATIVE_CALL" else CERTAIN
            if found.get(caller) != CERTAIN:
                found[caller] = cert
        def is_test(q: str) -> bool:
            s = self.b.symbol_table.get(q)
            return s is not None and bool(self.b._in_test_file(s.file))

        return sorted(found.items(), key=lambda kv: (is_test(kv[0]), kv[1] != CERTAIN, kv[0]))  # production first

    def _digest_certain(self, caller: str, anchor: str) -> bool:
        """Whether the caller's digest names the anchor as a certain call target (see the digest's receiver
        check: a graph edge matched by method name alone is only possible there)."""
        return any(f.certainty == CERTAIN and anchor in f.targets for f in self.digests.digest(caller).facts
                   if f.kind in ("call", "calls", "dynamic"))

    def callers(self, anchor: str) -> list[CallerReport]:
        raised = raised_names(self.digests.digest(anchor))
        anchor_node = self.b.def_node(anchor)
        anchor_async = _is_async(anchor_node)
        reports = []
        for caller, cert in self._callers(anchor):
            if cert == CERTAIN and not self._digest_certain(caller, anchor):
                cert = POSSIBLE  # the graph linked it, but the call's receiver does not establish the target
            sym = self.b.symbol_table.get(caller)
            node = self.b.def_node(caller)
            parsed = self.b.parsed_file(sym.file) if sym is not None else None
            if node is None or parsed is None:
                continue
            file = self._rel(sym.file)
            report = CallerReport(caller, file, cert, bool(self.b._in_test_file(sym.file)))
            for call in _call_sites(self.b, node, parsed, anchor):
                report.dependencies.extend(_site_dependencies(caller, anchor, call, node, parsed, raised, cert, file))
                if anchor_async and not _awaited(call) and parsed.language_id != LanguageID.GO:
                    report.dependencies.append(Dependency(
                        caller, anchor, "async", "calls it without awaiting: it receives a coroutine / promise",
                        file, call.start_point[0] + 1, cert))
            if not report.dependencies:
                report.dependencies.append(Dependency(
                    caller, anchor, "result", "linked to it by the call graph, but no call site by its name was "
                    "found (passed as a callback, aliased, or called dynamically)", file, sym.line_range[0], POSSIBLE))
            reports.append(report)
        return reports

    def callees(self, anchor: str) -> list[Dependency]:
        """What the anchor relies on in the functions it calls: their errors, empty returns and state changes."""
        sym = self.b.symbol_table.get(anchor)
        node = self.b.def_node(anchor)
        parsed = self.b.parsed_file(sym.file) if sym is not None else None
        if node is None or parsed is None:
            return []
        digest = self.digests.digest(anchor)
        targets: dict[str, str] = {}
        for f in digest.facts:
            if f.kind not in ("call", "calls", "dynamic"):
                continue
            for t in f.targets:
                if t in self.b.symbol_table and t != anchor:
                    cert = f.certainty if f.certainty in (CERTAIN, POSSIBLE) else POSSIBLE
                    if targets.get(t) != CERTAIN:
                        targets[t] = cert
        file = self._rel(sym.file)
        out: list[Dependency] = []
        for callee, cert in sorted(targets.items(), key=lambda kv: (kv[1] != CERTAIN, kv[0])):
            cd = self.digests.digest(callee)
            sites = _call_sites(self.b, node, parsed, callee)
            line = sites[0].start_point[0] + 1 if sites else sym.line_range[0]
            for exc in raised_names(cd):
                caught = any((c := _enclosing_catches(s, node, parsed.source)) is not None and _catches(c, exc)
                             for s in sites)
                out.append(Dependency(anchor, callee, "errors",
                                      f"`{callee}` can raise `{exc}`; " + ("this function catches it" if caught else
                                                                           "this function does not catch it"),
                                      file, line, cert))
            empty = [f for f in cd.facts if f.kind == "return" and f.text in ("None", "nothing", "null", "undefined", "nil")]
            if empty:
                when = "; ".join(" and ".join(f.when) for f in empty[:2] if f.when) or "on some path"
                out.append(Dependency(anchor, callee, "result",
                                      f"`{callee}` returns {empty[0].text} when {when}", file, line, cert))
            states = [f.text for f in cd.facts if f.kind == "state"]
            if states:
                out.append(Dependency(anchor, callee, "result",
                                      f"`{callee}` changes state: " + "; ".join(_short(s, 60) for s in states[:3]) +
                                      (f" (+{len(states) - 3} more)" if len(states) > 3 else ""), file, line, cert))
        return out
