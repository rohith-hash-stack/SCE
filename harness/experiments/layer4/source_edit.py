"""On-disk edits to one function, shared by the Layer 4 splice harness and
the Node tracer: locate a function by file + line, replace its body or the
whole definition, stub it, or wrap its body in enter/exit hooks - and put
the file back byte-for-byte afterwards (`edited`).

Functions are found with Prism's tree-sitter grammars, so the same code
handles Python, JavaScript and TypeScript: the innermost function-like node
(`function_definition`; `function_declaration`, `method_definition`,
`function_expression`, `arrow_function`, `generator_function_declaration`)
whose span contains `line` (1-based, the line of the `def`/`function`/
method name - a decorator line also selects its function).
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import textwrap
from dataclasses import dataclass
from pathlib import Path

PY_FUNCS = ("function_definition",)
JS_FUNCS = ("function_declaration", "method_definition", "function_expression", "arrow_function", "function",
            "generator_function_declaration", "generator_function")


@dataclass(frozen=True)
class FunctionSpan:
    path: str
    language: str  # "python" | "javascript" | "typescript" | "tsx"
    start: int  # byte offsets of the whole definition (decorators excluded)
    end: int
    body_start: int  # byte offsets of the body: Python block / JS statement block or expression
    body_end: int
    indent: str  # indentation of the definition's first line
    expression_body: bool  # JS arrow function with an expression body: `(x) => x + 1`

    def text(self, source: bytes) -> str:
        return source[self.start:self.end].decode()


def _language(path: str) -> str:
    ext = os.path.splitext(path)[1]
    return {".py": "python", ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx"}.get(ext, "javascript")


def locate(path: str, line: int) -> FunctionSpan:
    """The innermost function whose definition spans `line`."""
    from prism.parser.tree_sitter_loader import parse_source

    source = Path(path).read_bytes()
    parsed = parse_source(path, source)
    if parsed is None:
        raise ValueError(f"no grammar for {path}")
    kinds = PY_FUNCS if _language(path) == "python" else JS_FUNCS
    best = None
    stack = [parsed.root_node]
    while stack:
        n = stack.pop()
        start_line = n.start_point[0] + 1
        if n.type == "decorated_definition":
            start_line = n.start_point[0] + 1
        if n.start_point[0] + 1 <= line <= n.end_point[0] + 1:
            target = n
            if n.type == "decorated_definition":
                target = next((c for c in n.children if c.type in PY_FUNCS), None)
            if (target is not None and target.type in kinds and start_line <= line
                    and target.child_by_field_name("body") is not None):  # not the `function` keyword token
                if best is None or (target.end_byte - target.start_byte) < (best.end_byte - best.start_byte):
                    best = target
            stack.extend(n.children)
    if best is None:
        raise ValueError(f"no function at {path}:{line}")
    body = best.child_by_field_name("body")
    if body is None:
        raise ValueError(f"function at {path}:{line} has no body")
    line_start = source.rfind(b"\n", 0, best.start_byte) + 1
    indent = source[line_start:best.start_byte].decode()
    indent = indent[: len(indent) - len(indent.lstrip())]
    return FunctionSpan(path, _language(path), best.start_byte, best.end_byte, body.start_byte, body.end_byte, indent,
                        body.type not in ("block", "statement_block"))


def _body_indent(source: bytes, span: FunctionSpan) -> str:
    """Indentation of a Python body's first statement."""
    line_start = source.rfind(b"\n", 0, span.body_start) + 1
    first = source[line_start:span.body_start].decode()
    return first if not first.strip() else span.indent + "    "


def _write(path: str, data: bytes) -> None:
    Path(path).write_bytes(data)


def replace_function(span: FunctionSpan, new_definition: str) -> None:
    """Replace the whole definition (keeping Python decorators, which sit
    outside the span) with `new_definition`, re-indented to the original."""
    source = Path(span.path).read_bytes()
    text = textwrap.dedent(new_definition).strip("\n")
    lines = text.splitlines()
    text = "\n".join([lines[0]] + [(span.indent + ln) if ln.strip() else ln for ln in lines[1:]])
    _write(span.path, source[:span.start] + text.encode() + source[span.end:])


def replace_body(span: FunctionSpan, new_body: str) -> None:
    """Replace the body only. Python: `new_body` is the statements; JS: the
    statements without the surrounding braces."""
    source = Path(span.path).read_bytes()
    body = textwrap.dedent(new_body).strip("\n")
    if span.language == "python":
        pad = _body_indent(source, span)
        text = "\n".join((pad + ln) if ln.strip() else ln for ln in body.splitlines())
        start = source.rfind(b"\n", 0, span.body_start) + 1  # replace from the body's first line start
        _write(span.path, source[:start] + text.encode() + source[span.body_end:])
    else:
        pad = span.indent + "  "
        text = "{\n" + "\n".join((pad + ln) if ln.strip() else ln for ln in body.splitlines()) + "\n" + span.indent + "}"
        _write(span.path, source[:span.body_start] + text.encode() + source[span.body_end:])


def stub(span: FunctionSpan) -> None:
    """Replace the body with one that always raises."""
    if span.language == "python":
        replace_body(span, 'raise NotImplementedError("layer4 stub")')
    else:
        replace_body(span, 'throw new Error("layer4 stub");')


def instrument_js(span: FunctionSpan, enter: str, exit_: str) -> None:
    """Wrap a JS/TS body: `{ const __l4t = <enter>; try { BODY } finally {
    <exit>(__l4t) } }`. An expression-bodied arrow becomes a block
    returning the expression."""
    source = Path(span.path).read_bytes()
    body = source[span.body_start:span.body_end].decode()
    inner = f"return ({body});" if span.expression_body else body[1:-1]
    text = f"{{ const __l4t = {enter}; try {{{inner}}} finally {{ {exit_}(__l4t); }} }}"
    _write(span.path, source[:span.body_start] + text.encode() + source[span.body_end:])


@contextlib.contextmanager
def edited(*paths: str):
    """Restore every file in `paths` byte-for-byte on exit, whatever happens
    in between, and check it was restored."""
    saved = {p: Path(p).read_bytes() for p in paths}
    try:
        yield
    finally:
        for p, data in saved.items():
            Path(p).write_bytes(data)
        for p, data in saved.items():
            if hashlib.sha256(Path(p).read_bytes()).digest() != hashlib.sha256(data).digest():
                raise RuntimeError(f"{p} was not restored")
