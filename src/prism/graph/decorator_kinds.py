"""What a Python decorator does with the function it decorates, read from
the decorator's own source (no framework catalog):

- `REGISTRATION` - hands the function to something else (a registry call,
  a container, an attribute of another object) and returns it unchanged,
  or returns the result of such a call (`app.task`-style). Route, command,
  signal and filter registration all look like this.
- `WRAPPER` - returns a *new* function that calls the original
  (`@functools.wraps` closures: `async_unsafe`, `login_required`, ...).
- `ANNOTATION` - returns the function unchanged having only set attributes
  on it (`admin.display`), or nothing at all (a no-op marker).
- `UNKNOWN` - mixed or unreadable returns (e.g. wraps on one branch,
  returns as-is on another).

The function that receives the decorated callable depends on the usage:
bare `@d` -> `d` itself; called `@d(...)` -> whatever `d(...)` returns
(a nested `def`, possibly through `self.other_method(...)` on the same
class, as FastAPI's `get` -> `api_route` -> `decorator` does). Return
statements that do not involve the received function at all belong to
another usage mode (Django's `Library.filter` serves both `@register.filter`
and `@register.filter("name")`) and are ignored; a nested function that
never calls the received one is such a factory branch, not a wrapper.
"""
from __future__ import annotations

import ast
from enum import Enum


class DecoratorKind(str, Enum):
    REGISTRATION = "registration"
    WRAPPER = "wrapper"
    ANNOTATION = "annotation"
    UNKNOWN = "unknown"


#: Calls that inspect, copy metadata onto, or print a function - passing it
#: to one of these is not handing it to a registry.
_INTROSPECTIVE = frozenset({
    "callable", "isinstance", "issubclass", "getattr", "hasattr", "setattr", "delattr", "id", "type", "print",
    "len", "repr", "str", "vars", "dir", "iscoroutinefunction", "isasyncgenfunction", "isgeneratorfunction",
    "iscoroutine", "isfunction", "ismethod", "signature", "getfullargspec", "getattr_static", "wraps",
    "update_wrapper", "partial", "warn", "markcoroutinefunction", "sync_to_async", "async_to_sync",
})
_MAX_DEPTH = 4


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _own_nodes(fn: ast.AST):
    """Nodes in `fn`'s body; nested defs are yielded but not entered."""
    stack = list(getattr(fn, "body", []))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def _nested_defs(fn: ast.AST) -> dict[str, ast.AST]:
    return {n.name: n for n in _own_nodes(fn) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _aliases(fn: ast.AST, p: str) -> set[str]:
    """`p` plus every name plainly assigned from it (`func = message`)."""
    names = {p}
    for n in _own_nodes(fn):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Name) and n.value.id in names:
            names |= {t.id for t in n.targets if isinstance(t, ast.Name)}
    return names


def _params(fn: ast.AST) -> list[str]:
    a = fn.args
    names = [p.arg for p in a.posonlyargs + a.args]
    return names[1:] if names and names[0] in ("self", "cls") else names


def _as_set(name) -> set[str]:
    return {name} if isinstance(name, str) else set(name)


def _mentions(node: ast.AST, name) -> bool:
    names = _as_set(name)
    return any(isinstance(n, ast.Name) and n.id in names for n in ast.walk(node))


def _calls(fn: ast.AST, name) -> bool:
    """`fn`'s body (nested defs included) calls `name(...)`."""
    names = _as_set(name)
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in names for n in ast.walk(fn))


def _callee_name(call: ast.Call) -> str:
    f = call.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def _arg_position(call: ast.Call, name) -> int | None:
    names = _as_set(name)
    for i, a in enumerate(call.args):
        if isinstance(a, ast.Name) and a.id in names:
            return i
    return None


def _passes(call: ast.Call, name) -> bool:
    names = _as_set(name)
    values = list(call.args) + [k.value for k in call.keywords]
    return any(isinstance(v, ast.Name) and v.id in names for v in values)


def _holds(node: ast.AST, names) -> bool:
    """`node` contains one of `names` as a value (in a tuple, dict, keyword...),
    not merely an attribute of it (`fn.__name__`)."""
    names = _as_set(names)
    attribute_bases = {id(n.value) for n in ast.walk(node) if isinstance(n, ast.Attribute)}
    return any(isinstance(n, ast.Name) and n.id in names and id(n) not in attribute_bases for n in ast.walk(node))


def _side_effects(fn: ast.AST, p) -> str:
    """For a function returning `p` unchanged: "stored" (handed to a call or
    put into another object), "attrs" (attributes set on `p` only) or "none"."""
    attrs = False
    for n in _own_nodes(fn):
        if (isinstance(n, ast.Call) and _callee_name(n) not in _INTROSPECTIVE
                and any(_holds(a, p) for a in list(n.args) + [k.value for k in n.keywords])):
            return "stored"
        if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            value = n.value
            for t in targets:
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id in _as_set(p):
                    attrs = True
                elif isinstance(t, (ast.Subscript, ast.Attribute)) and value is not None and _holds(value, p):
                    return "stored"
    return "attrs" if attrs else "none"


def _sibling(cls: ast.ClassDef | None, call: ast.Call, module: ast.Module | None = None) -> ast.AST | None:
    """The definition `call` invokes when it is readable here: `self.m(...)`
    on the same class, or a function defined in the same module."""
    f = call.func
    if cls is not None and isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in ("self", "cls"):
        return next((n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == f.attr), None)
    if module is not None and isinstance(f, ast.Name):
        return next((n for n in module.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == f.id), None)
    return None


def _receiver_kind(fn: ast.AST, p: str, cls: ast.ClassDef | None, module: ast.Module | None,
                   depth: int) -> DecoratorKind | None:
    """Kind of `fn` given that its parameter `p` is the decorated function;
    `None` when no return statement involves `p` (another usage mode)."""
    if depth > _MAX_DEPTH:
        return DecoratorKind.UNKNOWN
    names = _aliases(fn, p)
    nested = _nested_defs(fn)
    seen: set[str] = set()

    def follow(target: ast.AST | None, call: ast.Call) -> str | None:
        pos = _arg_position(call, names)
        params = _params(target) if target is not None else []
        if target is None or pos is None or pos >= len(params):
            return None
        sub = _receiver_kind(target, params[pos], cls, module, depth + 1)
        return sub.value if sub else None

    for n in _own_nodes(fn):
        if not isinstance(n, ast.Return) or n.value is None:
            continue
        v = n.value
        if isinstance(v, ast.Name) and v.id in names:
            seen.add("identity")
        elif isinstance(v, ast.Name) and v.id in nested:
            # a new function: a wrapper iff it calls the original (else it
            # is the factory branch of a dual-use decorator)
            if _calls(nested[v.id], names):
                seen.add("wrapper")
        elif isinstance(v, ast.Call):
            if isinstance(v.func, ast.Call) and _mentions(v.func, names):  # wraps(p)(inner)
                seen.add("wrapper")
            elif any(isinstance(a, ast.Name) and a.id in nested and _calls(nested[a.id], names) for a in v.args):
                seen.add("wrapper")  # update_wrapper(inner, p)
            elif _passes(v, names):
                target = nested.get(v.func.id) if isinstance(v.func, ast.Name) else None
                target = target or _sibling(cls, v, module)
                sub = follow(target, v)
                if sub is not None:
                    seen.add(sub)
                elif target is None and isinstance(v.func, ast.Attribute):
                    seen.add(DecoratorKind.REGISTRATION.value)  # handed to another object's method
                else:
                    seen.add("unknown")  # an unreadable plain function: may wrap
            elif _mentions(v, names):
                seen.add("unknown")
        elif _mentions(v, names):
            seen.add("unknown")
    if not seen:
        return None
    if "wrapper" in seen:
        return DecoratorKind.WRAPPER if seen == {"wrapper"} else DecoratorKind.UNKNOWN
    if "unknown" in seen:
        return DecoratorKind.UNKNOWN
    if DecoratorKind.REGISTRATION.value in seen:
        return DecoratorKind.REGISTRATION
    if DecoratorKind.ANNOTATION.value in seen and "identity" not in seen:
        return DecoratorKind.ANNOTATION
    return DecoratorKind.REGISTRATION if _side_effects(fn, names) == "stored" else DecoratorKind.ANNOTATION


def _factory_receivers(fn: ast.AST, cls: ast.ClassDef | None, module: ast.Module | None, depth: int = 0) -> list[ast.AST]:
    """The function(s) a decorator factory returns for `@fn(...)` usage."""
    if depth > _MAX_DEPTH:
        return []
    nested = _nested_defs(fn)
    out: list[ast.AST] = []
    for n in _own_nodes(fn):
        if isinstance(n, ast.Return) and n.value is not None:
            v = n.value
            if isinstance(v, ast.Name) and v.id in nested:
                out.append(nested[v.id])
            elif isinstance(v, ast.Call) and (target := _sibling(cls, v, module)) is not None:
                out.extend(_factory_receivers(target, cls, module, depth + 1))
    return out


def classify(fn: ast.AST, called: bool, cls: ast.ClassDef | None = None,
             module: ast.Module | None = None) -> DecoratorKind | None:
    """Kind of the decorator defined by `fn` (a `FunctionDef`; `cls` its
    class for methods; `module` its module, to follow same-module helper
    calls) used bare (`called=False`) or as `@fn(...)`. `None` when the
    receiving function cannot be found (the factory returns something this
    module cannot follow) - the caller decides on other evidence."""
    receivers = _factory_receivers(fn, cls, module) if called else [fn]
    kinds = set()
    for r in receivers:
        params = _params(r)
        if not params:
            continue
        k = _receiver_kind(r, params[0], cls, module, 0)
        if k is not None:
            kinds.add(k)
    if not kinds:
        return None
    return kinds.pop() if len(kinds) == 1 else DecoratorKind.UNKNOWN


def find_def(tree: ast.Module, line: int) -> tuple[ast.AST | None, ast.ClassDef | None]:
    """The function definition starting at `line` (decorators excluded or
    included) and its enclosing class."""
    def visit(body, cls):
        for n in body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = min([n.lineno] + [d.lineno for d in n.decorator_list])
                if line in (n.lineno, start):
                    return n, cls
                hit = visit(n.body, cls)
                if hit[0] is not None:
                    return hit
            elif isinstance(n, ast.ClassDef):
                hit = visit(n.body, n)
                if hit[0] is not None:
                    return hit
        return None, None
    return visit(tree.body, None)


def find_qualified(tree: ast.Module, path: list[str]) -> tuple[ast.AST | None, ast.ClassDef | None]:
    """`func` or `Class.method` by name in a module tree."""
    if len(path) == 1:
        n = next((n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == path[0]), None)
        return n, None
    if len(path) == 2:
        cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == path[0]), None)
        if cls is not None:
            m = next((n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == path[1]), None)
            return m, cls
    return None, None
