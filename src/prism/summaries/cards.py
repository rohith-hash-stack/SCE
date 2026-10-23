"""Context cards and static resources for one indexed function.

A card is the input the purpose summarizer sees; every field is read from
PRISM's own index:

    Qualified Name / Enclosing Scope / Decorators / Route
    Signature & Type Hints (capped at SIG_CAP characters)
    Architectural Axes (Role, Form, Output, Substance - the 4-axis masks)
    Direct Callees: resolved CALLS edges, plus method calls on a parameter
        or self that resolved to no edge (`session.add`), so state-change
        evidence the graph did not resolve survives
    Direct Callers: resolved CALLS edges
    Explicit Raises / Status (prism.semantics.contract_axis)
    Docstring: the first paragraph

Each edge list is capped (hub guard). `resources()` lists the concrete
settings, header/environment keys and ORM models a body touches.

The shipped summarizer (`prism.summaries.pipeline`) uses card v2
(`axes=False`); the axes line (v1) exists only for the harness's earlier
runs and needs the benchmark package's `axes_field`.
"""
from __future__ import annotations

import re

from prism.parser.lang_config import (
    CALL_NODE_TYPE,
    RAISE_NODE_TYPE,
    call_callee_segments,
    collect_decorator_texts,
    flatten_reference_chain,
    iter_scoped_nodes,
)
from prism.semantics.contract_axis import _local_contract, _params

CONTROL = {"if_statement", "for_statement", "for_in_statement", "while_statement", "try_statement",
           "with_statement", "switch_statement", "match_statement"}
CAP = 12            # max resolved callees / callers listed per card
CAP_UNRESOLVED = 8  # max unresolved receiver calls listed per card
SIG_CAP = 240       # max signature characters (FastAPI's Annotated[..., Doc("...")] parameters)
_SELF = ("self", "this", "cls")
_HEADER_OBJECTS = {"META", "headers", "environ", "COOKIES", "cookies", "session", "GET", "POST", "query_params", "env"}


def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf8", "replace")


def _first_paragraph(lines) -> str:
    para = []
    for line in lines:
        if not line and para:
            break
        if line:
            para.append(line)
    return " ".join(para)


def docstring(node, src: bytes, lang) -> str:
    """First paragraph of a Python docstring or of the JS/TS comment block
    right above the declaration."""
    body = node.child_by_field_name("body")
    if lang == "python" or str(lang).endswith("PYTHON"):
        if body is not None and body.named_children:
            first = body.named_children[0]
            if first.type == "expression_statement" and first.named_children and first.named_children[0].type == "string":
                s = _text(first.named_children[0], src).strip("\"' \nrbuRBU")
                return _first_paragraph(l.strip() for l in s.splitlines())
        return ""
    stmt = node
    while stmt.parent is not None and stmt.parent.type not in ("program", "class_body", "statement_block"):
        stmt = stmt.parent
    prev = stmt.prev_named_sibling
    if prev is None or prev.type != "comment":
        return ""
    lines = []
    for line in _text(prev, src).splitlines():
        line = line.strip().lstrip("/*").strip()
        if line.startswith("@"):
            break
        lines.append(line)
    return _first_paragraph(lines)


def call_sites(node, src: bytes, lang) -> list[list[str]]:
    if not CALL_NODE_TYPE.get(lang):
        return []
    return [s for c in iter_scoped_nodes(node, {CALL_NODE_TYPE[lang]}, lang) if (s := call_callee_segments(c, src, lang))]


def _capped(items, cap: int) -> str:
    items = list(items)
    return ", ".join(items[:cap]) + (f", ... (+{len(items) - cap} more)" if len(items) > cap else "")


def build_card(builder, masks: dict, entries, qname: str, axes: bool = True) -> dict | None:
    """`{"card", "docstring", "unresolved", "n_callers", "n_callees"}`, or
    None when the symbol has no definition node. `axes=False` (card v2)
    leaves out the Architectural Axes line: summarizers copied its raw
    labels ("pure compute", "no effect") into the summaries."""
    sym = builder.symbol_table.get(qname)
    node = builder.def_node(qname)
    parsed = builder.parsed_file(sym.file) if sym is not None else None
    if node is None or parsed is None:
        return None
    src, lang = parsed.source, parsed.language_id
    scope_q = qname.rpartition(".")[0]
    scope_sym = builder.symbol_table.get(scope_q)
    scope = f"{scope_q} ({scope_sym.kind})" if scope_sym is not None else f"{scope_q} (module)"
    entry = entries.get(qname) if isinstance(entries, dict) else (qname in entries)
    decorators = "; ".join(collect_decorator_texts(node, parsed)) or "none"
    if entry:
        route = f"{getattr(entry, 'method', '') or ''} {getattr(entry, 'path', '') or ''}".strip()
        decorators += " [registered entry point" + (f": {route}" if route else "") + "]"
    params = node.child_by_field_name("parameters")
    ret = node.child_by_field_name("return_type")
    sig = re.sub(r"\s+", " ", _text(params, src) if params is not None else "()") + \
        (" -> " + _text(ret, src).lstrip(":").strip() if ret is not None else "")
    if len(sig) > SIG_CAP:
        sig = sig[:SIG_CAP] + " ..."
    callees = sorted({t for _u, t, d in builder.graph.out_edges(qname, data=True)
                      if d.get("relation", "CALLS") == "CALLS" and not t.startswith("<")})
    callers = sorted({u for u, _t, d in builder.graph.in_edges(qname, data=True)
                      if d.get("relation", "CALLS") == "CALLS" and not u.startswith("<")})
    resolved_leaves = {c.rsplit(".", 1)[-1] for c in callees}
    roots = set(_params(node, src)) | set(_SELF)
    unresolved = list(dict.fromkeys(
        ".".join(s) for s in call_sites(node, src, lang)
        if len(s) >= 2 and s[0] in roots and s[-1] not in resolved_leaves))
    callee_txt = _capped(callees, CAP) if callees else ""
    if unresolved:
        callee_txt += ("; " if callee_txt else "") + "unresolved calls on parameters/self: " + \
            _capped(unresolved, CAP_UNRESOLVED)
    local = _local_contract(builder, qname, {})
    raises = ", ".join(f"{n}({', '.join(sorted(c))})" if c else n for n, c in sorted(local.throws.items())) if local else ""
    doc = docstring(node, src, lang)
    lines = [
        f"Qualified Name: {qname}",
        f"Enclosing Scope: {scope}",
        f"Decorators / Route: {decorators}",
        f"Signature & Type Hints: {sig}",
    ]
    if axes:
        from benchmarks.final_sweep.context_ops import axes_field
        field = axes_field(masks.get(qname, 0), "all") or "axes=none"
        ax = dict(p.split(":", 1) for p in field[len("axes="):].split(";") if ":" in p)
        lines.append(f"Architectural Axes: Role={ax.get('R', 'NONE')}, Form={ax.get('F', 'NONE')}, "
                     f"Output={ax.get('O', 'NONE')}, Substance={ax.get('S', 'NONE')}")
    card = "\n".join(lines + [
        f"Direct Callees: {callee_txt or 'none'}",
        f"Direct Callers: {_capped(callers, CAP) or 'none'}",
        f"Explicit Raises / Status: {raises or 'none'}",
        f"Docstring: {doc or 'none'}",
    ])
    return {"card": card, "docstring": doc, "unresolved": unresolved, "n_callers": len(callers), "n_callees": len(callees)}


def resources(builder, qname: str) -> list[str]:
    """Concrete resources the body touches: `setting X`, `env X`,
    `model X` (ORM `X.objects`), `<META|headers|...> KEY`."""
    sym, node = builder.symbol_table.get(qname), builder.def_node(qname)
    parsed = builder.parsed_file(sym.file) if sym is not None and node is not None else None
    if parsed is None:
        return []
    src, lang = parsed.source, parsed.language_id
    unquote = lambda s: s.strip("\"'")
    found = []
    for n in iter_scoped_nodes(node, {"attribute", "member_expression"}, lang):
        seg = flatten_reference_chain(n, src, lang) or []
        if len(seg) >= 2 and seg[0] == "settings":
            found.append(f"setting {seg[1]}")
        if len(seg) >= 3 and seg[0] == "process" and seg[1] == "env":
            found.append(f"env {seg[2]}")
        if "objects" in seg[1:]:
            found.append(f"model {seg[seg.index('objects') - 1]}")
    for n in iter_scoped_nodes(node, {"subscript", "subscript_expression"}, lang):
        obj = n.child_by_field_name("value") or n.child_by_field_name("object")
        key = n.child_by_field_name("subscript") or n.child_by_field_name("index")
        if obj is not None and key is not None and key.type == "string" and _text(obj, src).split(".")[-1] in _HEADER_OBJECTS:
            found.append(f"{_text(obj, src).split('.')[-1]} {unquote(_text(key, src))}")
    for seg, call in ((call_callee_segments(c, src, lang) or [], c)
                      for c in (iter_scoped_nodes(node, {CALL_NODE_TYPE[lang]}, lang) if CALL_NODE_TYPE.get(lang) else [])):
        args = call.child_by_field_name("arguments")
        first = args.named_children[0] if args is not None and args.named_children else None
        if first is not None and first.type == "string" and len(seg) >= 2 and seg[-1] in ("get", "getenv", "getlist", "pop") \
                and (seg[-2] in _HEADER_OBJECTS or seg[-1] == "getenv"):
            found.append(f"{seg[-2]} {unquote(_text(first, src))}")
    return list(dict.fromkeys(found))
