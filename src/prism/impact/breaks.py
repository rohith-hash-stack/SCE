"""Which calls a proposed change to the anchor breaks: its new code against every call site, mechanically.

Given the anchor's new code (what an AI or a developer is about to write), each direct caller's call is
bound to the new parameter list the way the language would bind it:

    fails             the call will raise / not run: a keyword that no longer exists, too many positional
                      arguments, a new required parameter it does not pass, a value given twice, the
                      function renamed
    changes meaning   the call still runs but an argument lands in another parameter (a parameter removed or
                      inserted before it), or the parameter in its position was renamed and its default changed
                      (`no_indent=False` -> `indent=True`: the same `True` now means the opposite)
    check             the new code raises an exception the old one did not and this call does not catch it;
                      the new code can return nothing and this call uses the result

A parameter only renamed (same position, same default) is not reported for positional calls: they bind the
same way. Calls with `*args` / `**kwargs` / spread arguments are checked only where the splat cannot matter.
Python, JavaScript and TypeScript; other languages give no statements.
"""
from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass

from prism.impact.dependencies import (
    _call_sites, _catches, _enclosing_catches, _result_statement, _short, _text, raised_names)
from prism.summaries.digest import CERTAIN, POSSIBLE

FAILS, MEANING, CHECK = "fails", "changes meaning", "check"
_LANGS = ("python", "javascript", "typescript", "tsx")
_RAISED = re.compile(r"\b(?:raise|throw)\s+(?:new\s+)?([A-Za-z_$][\w$.]*)")
_EMPTY_RETURN = re.compile(r"\breturn\s*(?:None|null|undefined)?\s*(?:;|$|#|//)", re.M)
_SELF = ("self", "cls")


@dataclass(frozen=True)
class Param:
    name: str
    kind: str        # "positional" | "positional_only" | "keyword_only" | "var_positional" | "var_keyword"
    default: str | None


@dataclass(frozen=True)
class Signature:
    name: str | None
    params: tuple[Param, ...]
    is_method: bool  # its first parameter was `self` / `cls` (dropped from `params`)

    def positional(self) -> list[Param]:
        return [p for p in self.params if p.kind in ("positional", "positional_only")]

    def names(self) -> dict[str, Param]:
        return {p.name: p for p in self.params if p.kind not in ("var_positional", "var_keyword")}

    def has(self, kind: str) -> bool:
        return any(p.kind == kind for p in self.params)


@dataclass(frozen=True)
class Break:
    caller: str
    file: str
    line: int
    call: str
    severity: str    # FAILS | MEANING | CHECK
    problem: str
    certainty: str = CERTAIN


def _lang(language) -> str:
    return str(language).lower().rsplit(".", 1)[-1]


def _balanced(text: str, start: int) -> tuple[str, int] | None:
    """The text inside the parentheses opening at `start`, and the index after the closing one."""
    depth, quote, i = 0, None, start
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i + 1
        i += 1
    return None


def _split_top(text: str) -> list[str]:
    parts, depth, quote, cur = [], 0, None, []
    for i, ch in enumerate(text):
        if quote:
            cur.append(ch)
            if ch == quote and text[i - 1] != "\\":
                quote = None
            continue
        if ch in "'\"`":
            quote = ch
        elif ch in "([{<":
            depth += 1
        elif ch in ")]}>" and depth:
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _strip_comments(code: str, lang: str) -> str:
    if lang == "python":
        return re.sub(r"(?m)^\s*#.*$", "", code)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", code)


def signature(code: str, language) -> Signature | None:
    """The parameter list of the function whose code is `code` (the first definition in it)."""
    lang = _lang(language)
    if lang not in _LANGS:
        return None
    text = _strip_comments(textwrap.dedent(code), lang)
    if lang == "python":
        m = re.search(r"\bdef\s+([A-Za-z_]\w*)\s*\(", text)
        if not m:
            return None
        name, open_at = m.group(1), m.end() - 1
    else:
        m = (re.search(r"\bfunction\b\s*\*?\s*([A-Za-z_$][\w$]*)?\s*(?:<[^>]*>)?\s*\(", text)
             or re.search(r"^\s*(?:(?:public|private|protected|static|async|readonly|get|set)\s+)*"
                          r"([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*\(", text, re.M)
             or re.search(r"()\(", text))
        if not m:
            return None
        name, open_at = (m.group(1) or None), m.end() - 1
    inner = _balanced(text, open_at)
    if inner is None:
        return None
    params, kind_after_star, is_method = [], False, False
    raw = _split_top(inner[0])
    for i, p in enumerate(raw):
        if lang == "python":
            if p == "/":
                params = [Param(x.name, "positional_only", x.default) for x in params]
                continue
            if p == "*":
                kind_after_star = True
                continue
            if p.startswith("**"):
                params.append(Param(p[2:].split(":", 1)[0].strip(), "var_keyword", None))
                continue
            if p.startswith("*"):
                params.append(Param(p[1:].split(":", 1)[0].strip(), "var_positional", None))
                kind_after_star = True
                continue
            head, _eq, default = p.partition("=")
            pname = head.split(":", 1)[0].strip()
            if i == 0 and pname in _SELF:
                is_method = True
                continue
            params.append(Param(pname, "keyword_only" if kind_after_star else "positional",
                                default.strip() if _eq else None))
        else:
            if p.startswith("..."):
                params.append(Param(p[3:].split(":", 1)[0].strip(), "var_positional", None))
                continue
            if p.startswith(("{", "[")):
                closing = _balanced(p.replace("[", "(", 1).replace("{", "(", 1), 0)
                rest = p[closing[1]:] if closing else ""
                _h, eq, default = rest.partition("=")
                params.append(Param(p.split("=", 1)[0].strip()[:30], "positional", default.strip() if eq else None))
                continue
            p = re.sub(r"^(?:public|private|protected|readonly)\s+", "", p)
            head, eq, default = p.partition("=")
            pname = re.split(r"[?:]", head, maxsplit=1)[0].strip()
            optional = "?" in head.split(":", 1)[0]
            if pname == "this":
                continue
            params.append(Param(pname, "positional", default.strip() if eq else ("undefined" if optional else None)))
    return Signature(name, tuple(params), is_method)


def _arguments(call, src: bytes) -> tuple[list[str], list[str], bool, bool]:
    """`(positional texts, keyword names, has *args / spread, has **kwargs)` of a call node."""
    args = call.child_by_field_name("arguments")
    positional, keywords, star, dstar = [], [], False, False
    if args is None:
        return positional, keywords, star, dstar
    for a in args.named_children:
        if a.type in ("comment",):
            continue
        if a.type == "keyword_argument":
            name = a.child_by_field_name("name")
            if name is not None:
                keywords.append(_text(name, src))
        elif a.type in ("list_splat", "spread_element"):
            star = True
        elif a.type == "dictionary_splat":
            dstar = True
        else:
            positional.append(_short(_text(a, src), 40))
    return positional, keywords, star, dstar


def _receiver(call, src: bytes) -> str:
    func = call.child_by_field_name("function")
    if func is None:
        return ""
    obj = func.child_by_field_name("object")
    return _text(obj, src) if obj is not None else ""


def bind(old: Signature, new: Signature, positional: list[str], keywords: list[str], star: bool, dstar: bool,
         lang: str, uses_arguments: bool = False) -> list[tuple[str, str]]:
    """`(severity, problem)` for one call with these arguments, old signature -> new signature."""
    out: list[tuple[str, str]] = []
    new_pos, old_pos = new.positional(), old.positional()
    new_names, old_names = new.names(), old.names()
    python = lang == "python"
    bound: dict[str, str] = {}
    if not star:
        for i, arg in enumerate(positional):
            if i < len(new_pos):
                bound[new_pos[i].name] = arg
            elif not new.has("var_positional"):
                if python:
                    out.append((FAILS, f"passes {len(positional)} positional arguments; it now takes at most "
                                       f"{len(new_pos)}"))
                elif not uses_arguments:
                    out.append((MEANING, f"passes {len(positional)} arguments; it now takes {len(new_pos)}, "
                                         "so the extra ones are ignored"))
                break
    for k in keywords:
        if k not in new_names or new_names[k].kind == "positional_only":
            if not new.has("var_keyword"):
                was = " (removed or renamed)" if k in old_names else ""
                out.append((FAILS, f"passes `{k}=`, which is no longer a parameter{was}"))
        elif k in bound:
            out.append((FAILS, f"`{k}` gets two values: position {list(bound).index(k) + 1} (`{bound[k]}`) "
                               f"and `{k}=`"))
        else:
            bound[k] = k
    if not star and not dstar:
        for p in new.params:
            if p.kind in ("var_positional", "var_keyword") or p.default is not None or p.name in bound:
                continue
            if python:
                out.append((FAILS, f"does not pass `{p.name}`, now required"))
            elif p.name not in old_names:
                out.append((MEANING, f"passes nothing for the new parameter `{p.name}` (it will be undefined)"))
    if not star:
        for i, arg in enumerate(positional):
            if i >= len(old_pos) or i >= len(new_pos):
                break
            o, n = old_pos[i], new_pos[i]
            if o.name == n.name:
                continue
            if o.name not in new_names and n.name not in old_names:  # renamed in place
                if (o.default or "") != (n.default or ""):
                    out.append((MEANING, f"`{arg}` (position {i + 1}) was `{o.name}={o.default}` and is now "
                                         f"`{n.name}={n.default}`: check it still means what this call wants"))
                continue
            out.append((MEANING, f"`{arg}` (position {i + 1}) went to `{o.name}`; it now goes to `{n.name}`"))
            break  # the arguments after it shift the same way
    return out


class BreakChecker:
    """The calls a change to an anchor breaks (shares the change context's index and digests)."""

    def __init__(self, deps):
        self.deps = deps          # prism.impact.dependencies.DependencyAnalyzer
        self.b = deps.b

    def check(self, anchor: str, new_code: str, include_tests: bool = False) -> list[Break]:
        b = self.b
        sym = b.symbol_table.get(anchor)
        node = b.def_node(anchor)
        if sym is None or node is None:
            raise KeyError(anchor)
        lang = _lang(sym.language_id)
        parsed_anchor = b.parsed_file(sym.file)
        old_code = _text(node, parsed_anchor.source)
        old, new = signature(old_code, lang), signature(new_code, lang)
        if old is None or new is None:
            return []
        uses_arguments = lang != "python" and re.search(r"\barguments\b", new_code) is not None
        old_raised = set(raised_names(self.deps.digests.digest(anchor))) | set(_RAISED.findall(old_code))
        new_raised = [x for x in dict.fromkeys(_RAISED.findall(new_code)) if x not in old_raised]
        returns_nothing = bool(_EMPTY_RETURN.search(new_code)) and not _EMPTY_RETURN.search(old_code)
        leaf = anchor.rsplit(".", 1)[-1].split("#", 1)[0]
        renamed = new.name and old.name and new.name != old.name and leaf not in ("__init__", "constructor")
        cls = anchor.rsplit(".", 2)[-2] if old.is_method and "." in anchor else None
        out: list[Break] = []
        for caller, cert in self.deps._callers(anchor):
            if cert == CERTAIN and not self.deps._digest_certain(caller, anchor):
                cert = POSSIBLE
            csym = b.symbol_table.get(caller)
            cnode = b.def_node(caller)
            parsed = b.parsed_file(csym.file) if csym is not None else None
            if cnode is None or parsed is None or (not include_tests and b._in_test_file(csym.file)):
                continue
            file = self.deps._rel(csym.file)
            for call in _call_sites(b, cnode, parsed, anchor):
                src = parsed.source
                text, line = _short(_text(call, src), 80), call.start_point[0] + 1
                positional, keywords, star, dstar = _arguments(call, src)
                if cls and old.is_method and _receiver(call, src).rsplit(".", 1)[-1] == cls and positional:
                    positional = positional[1:]  # `Class.method(obj, ...)`: the first argument is `self`
                found = []
                if renamed:
                    found.append((FAILS, f"calls `{old.name}`, which is now named `{new.name}`"))
                found += bind(old, new, positional, keywords, star, dstar, lang, uses_arguments)
                catches = _enclosing_catches(call, cnode, src)
                for exc in new_raised:
                    if catches is None or not _catches(catches, exc):
                        found.append((CHECK, f"the new code raises `{exc}`; this call does not catch it"))
                if returns_nothing:
                    use = _result_statement(call, cnode, src, parsed.language_id)
                    if use not in ("ignores the return value", "returns the result to its own callers"):
                        found.append((CHECK, f"the new code can return nothing; this call {use}"))
                out += [Break(caller, file, line, text, sev, problem, cert) for sev, problem in found]
        order = {FAILS: 0, MEANING: 1, CHECK: 2}
        return sorted(out, key=lambda x: (order[x.severity], x.file, x.line, x.problem))


def render_breaks(breaks: list[Break], calls_checked: int | None = None) -> str:
    out = ["## Calls this change breaks (its new code checked against every call site)"]
    if not breaks:
        out.append("No call site breaks by its signature, raised errors or empty returns"
                   + (f" ({calls_checked} checked)." if calls_checked is not None else "."))
        return "\n".join(out) + "\n"
    for x in breaks:
        cert = "" if x.certainty == CERTAIN else " (caller linked by name only, possible)"
        out.append(f"- **{x.severity}**: `{x.caller}` ({x.file}:{x.line}) `{x.call}`: {x.problem}{cert}")
    callers = len({x.caller for x in breaks if x.severity != CHECK})
    if callers:
        out.append(f"These {callers} caller{'s' if callers != 1 else ''} must change with it.")
    return "\n".join(out) + "\n"
