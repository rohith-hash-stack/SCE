"""Signature-only header packages (offline export summarization).

A summarized package is published to its dependents as a header: the same
modules, classes, method and function signatures, annotations, imports and
`__init__` re-exports, with every body replaced by `...`. Indexed in a
dependent's (or the host's) graph, a header lets the ordinary resolver type
`PoolManager(...)`, follow return annotations and facade re-exports - and
bind calls to the package's real entry points - without a single internal
call edge of the package entering that graph.
"""
from __future__ import annotations

import ast
import os


#: Attribute-forwarding dunders: a class defining or assigning one is a
#: runtime proxy when instantiated at module level (`prism.deps.summarizer`).
_FORWARDERS = ("__getattr__", "__getattribute__")


def _stub(node):
    node.body = [ast.Expr(ast.Constant(Ellipsis))]
    return node


def _header_statements(body: list) -> list:
    out = []
    for stmt in body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            out.append(stmt)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(_stub(stmt))
        elif isinstance(stmt, ast.ClassDef):
            stmt.body = _class_members(stmt.body) or [ast.Pass()]
            out.append(stmt)
        elif isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in stmt.targets):
            out.append(stmt)
        elif isinstance(stmt, ast.If):  # `if TYPE_CHECKING:` / platform branches: keep what they declare
            out.extend(_header_statements(stmt.body))
        elif isinstance(stmt, ast.Try):  # `try: import x` - the primary branch
            out.extend(_header_statements(stmt.body))
    return out


def _class_members(body: list) -> list:
    """A class body's methods, annotated fields and nested classes -
    including those declared under `if TYPE_CHECKING:` / version branches
    (aiohttp's `ClientSession.get`) or `try:` (both arms of an `if`: a
    name declared only in `else` still exists)."""
    members = []
    seen: set[str] = set()
    for m in body:
        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if m.name not in seen:
                seen.add(m.name)
                members.append(_stub(m))
        elif isinstance(m, ast.AnnAssign) and isinstance(m.target, ast.Name):
            members.append(ast.AnnAssign(target=m.target, annotation=m.annotation, value=None, simple=1))
        elif isinstance(m, ast.Assign) and any(isinstance(t, ast.Name) and t.id in _FORWARDERS for t in m.targets):
            # `__getattr__ = _ProxyLookup(getattr)` (werkzeug's LocalProxy):
            # an attribute-forwarding class, assigned rather than defined.
            names = [t for t in m.targets if isinstance(t, ast.Name) and t.id in _FORWARDERS]
            members.append(ast.Assign(targets=names, value=ast.Constant(Ellipsis), lineno=0))
        elif isinstance(m, ast.ClassDef):
            members.extend(_header_statements([m]))
        elif isinstance(m, (ast.If, ast.Try)):
            for nested in _class_members([*m.body, *m.orelse]):
                name = getattr(nested, "name", None)
                if name is None or name not in seen:
                    if name is not None:
                        seen.add(name)
                    members.append(nested)
    return members


def header_source(source: str) -> str:
    """The signature-only header for one module's source text."""
    tree = ast.parse(source)
    return ast.unparse(ast.Module(body=_header_statements(tree.body), type_ignores=[])) + "\n"


def package_headers(package_dir: str) -> dict[str, str]:
    """`{path relative to the package directory: header source}` for every
    parseable `.py` file of a package (unparseable files are skipped)."""
    headers: dict[str, str] = {}
    for root, dirs, files in os.walk(package_dir):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    headers[os.path.relpath(path, package_dir)] = header_source(fh.read())
            except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
                continue
    return headers


def write_headers(headers: dict[str, str], package: str, root: str) -> list[str]:
    """Materialize a header package as `<root>/<package>/...`; returns the
    written file paths."""
    written = []
    for rel, text in headers.items():
        path = os.path.join(root, package, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        written.append(path)
    return written
