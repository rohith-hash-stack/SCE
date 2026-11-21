"""The static checks of Phase E's derived tasks (`derive_tasks.py`): whether a working tree has made the stated change
completely. The scorer requires it besides the tests (`r3/runner.py` `score`), the done gate checks it (`done_gate.py`:
counts only, never where), and the task builder checks the gold tree with it.

A task's `derived` record says what to check:

| kind | done when |
|---|---|
| rename | the definition is under its new name where it was (`file`, `qualname` within it) and not under the old one, and no Python file has more uses of the old name (identifiers, and string literals equal to it, as in `__all__`) than the gold tree has (`baseline`: the same name used for other objects) |
| signature | the definition still has the parameter, now keyword-only (still required if it was), and no Python file has more calls to the name passing it positionally (more than `max_positional` positional arguments) than the gold tree has |
| move | the function is defined at the top level of `new_file` and not in `file`, and no Python file imports it from its old module (`from <old> import <name>`, or `<old>.<name>` through `import <old>`) more often than the gold tree does |

Every count is per file against the gold tree's own count, so a name another object also uses (a library's `send`)
never counts against the run; a run's new file starts from 0.
"""
from __future__ import annotations

import ast
import io
import os
import subprocess
import tokenize


def python_files(work: str) -> list[str]:
    """The tracked and untracked Python files of the working tree (not the harness's own `.r3-*` files)."""
    out = subprocess.run(["git", "-C", work, "ls-files", "--cached", "--others", "--exclude-standard", "*.py"],
                         capture_output=True, text=True).stdout.split()
    return sorted({f for f in out if not f.startswith(".r3-") and "/.r3-" not in f
                   and os.path.isfile(os.path.join(work, f))})


def _read(work: str, rel: str) -> str:
    with open(os.path.join(work, rel), "rb") as fh:
        return fh.read().decode("utf8", "replace")


def _parse(text: str):
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None


def name_uses(text: str, name: str) -> int:
    """Identifier tokens equal to `name`, plus string literals whose value is exactly `name` (`__all__`, `getattr`)."""
    n = 0
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.NAME and tok.string == name:
                n += 1
            elif tok.type == tokenize.STRING:
                try:
                    if ast.literal_eval(tok.string) == name:
                        n += 1
                except (ValueError, SyntaxError):
                    pass
    except (tokenize.TokenError, IndentationError, SyntaxError):
        n += sum(1 for _ in __import__("re").finditer(rf"\b{name}\b", text))
    return n


def find_def(tree, qualname: str):
    """The `def`/`class` node at `qualname` (`Class.method`) within a module, or None."""
    body = tree.body if tree is not None else []
    node = None
    for part in qualname.split("."):
        node = next((n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                     and n.name == part), None)
        if node is None:
            return None
        body = node.body
    return node


def positional_calls(tree, name: str, limit: int) -> int:
    """Calls to `name` (a bare name or `x.name`) with more than `limit` positional arguments and no `*args`."""
    if tree is None:
        return 0
    n = 0
    for c in ast.walk(tree):
        if isinstance(c, ast.Call):
            f = c.func
            callee = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if callee == name and len(c.args) > limit and not any(isinstance(a, ast.Starred) for a in c.args):
                n += 1
    return n


def _resolve(rel: str, module: str | None, level: int) -> str:
    """The absolute module a `from` import in `rel` names."""
    if level == 0:
        return module or ""
    pkg = rel[:-3].replace("/", ".").split(".")
    pkg = pkg[:-1] if not rel.endswith("__init__.py") else pkg[:-1]
    base = pkg[:len(pkg) - (level - 1)] if level > 1 else pkg
    return ".".join(base + ([module] if module else []))


def module_names(rel: str) -> set:
    """The dotted names a file can be imported by (with and without a leading `src.`)."""
    m = rel[:-3].replace("/", ".")
    m = m[:-9] if m.endswith(".__init__") else m
    names = {m}
    for prefix in ("src.", "lib."):
        if m.startswith(prefix):
            names.add(m[len(prefix):])
    return names


def old_imports(tree, rel: str, old_module_names: set, name: str) -> int:
    """`from <old> import name` statements, and `<alias>.name` uses through `import <old> [as alias]`."""
    if tree is None:
        return 0
    n, aliases = 0, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = _resolve(rel, node.module, node.level)
            if mod in old_module_names and any(a.name == name for a in node.names):
                n += 1
            # `from pkg import mod` then `mod.name`
            for a in node.names:
                if f"{mod}.{a.name}" in old_module_names:
                    aliases.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name in old_module_names:
                    aliases.add(a.asname or a.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == name:
            v = node.value
            dotted = []
            while isinstance(v, ast.Attribute):
                dotted.append(v.attr)
                v = v.value
            if isinstance(v, ast.Name):
                full = ".".join([v.id] + dotted[::-1])
                if full in aliases or (not dotted and v.id in aliases):
                    n += 1
    return n


def measure(derived: dict, work: str) -> dict:
    """Per-file counts of what the check counts, and the definition facts, for this tree."""
    kind = derived["kind"]
    counts: dict[str, int] = {}
    facts: dict = {}
    files = python_files(work)
    own = derived["file"]
    own_tree = _parse(_read(work, own)) if os.path.isfile(os.path.join(work, own)) else None
    if kind == "rename":
        parent, _, _leaf = derived["qualname"].rpartition(".")
        new_q = f"{parent}.{derived['new']}" if parent else derived["new"]
        facts["new_defined"] = find_def(own_tree, new_q) is not None
        facts["old_defined"] = find_def(own_tree, derived["qualname"]) is not None
        for f in files:
            c = name_uses(_read(work, f), derived["old"])
            if c:
                counts[f] = c
    elif kind == "signature":
        node = find_def(own_tree, derived["qualname"])
        facts["defined"] = node is not None
        if node is not None:
            kw = [a.arg for a in node.args.kwonlyargs]
            pos = [a.arg for a in node.args.posonlyargs + node.args.args]
            p = derived["param"]
            facts["param_keyword_only"] = p in kw and p not in pos
            if p in kw:
                default = node.args.kw_defaults[kw.index(p)]
                facts["param_required"] = default is None
        leaf = derived["qualname"].rsplit(".", 1)[-1]
        for f in files:
            c = positional_calls(_parse(_read(work, f)), leaf, derived["max_positional"])
            if c:
                counts[f] = c
    elif kind == "move":
        leaf = derived["name"]
        new = derived["new_file"]
        new_tree = _parse(_read(work, new)) if os.path.isfile(os.path.join(work, new)) else None
        facts["new_defined"] = new_tree is not None and any(
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == leaf for n in new_tree.body)
        facts["old_defined"] = own_tree is not None and any(
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == leaf for n in own_tree.body)
        old_names = module_names(own)
        for f in files:
            c = old_imports(_parse(_read(work, f)), f, old_names, leaf)
            if c:
                counts[f] = c
    else:
        raise ValueError(f"unknown derived kind {kind!r}")
    return {"counts": counts, "facts": facts}


def check(derived: dict, work: str) -> dict:
    """`{ok, problems, remaining, facts}`: `remaining` is how many counted places exceed the gold tree's count."""
    m = measure(derived, work)
    base = derived.get("baseline") or {}
    remaining = sum(max(0, c - base.get(f, 0)) for f, c in m["counts"].items())
    facts, problems = m["facts"], []
    kind = derived["kind"]
    if kind == "rename":
        if not facts["new_defined"]:
            problems.append(f"`{derived['new']}` is not defined where `{derived['old']}` was")
        if facts["old_defined"]:
            problems.append(f"`{derived['old']}` is still defined")
        if remaining:
            problems.append(f"the old name `{derived['old']}` is still used in {remaining} place(s)")
    elif kind == "signature":
        if not facts.get("defined"):
            problems.append("the function is no longer defined where it was")
        elif not facts.get("param_keyword_only"):
            problems.append(f"`{derived['param']}` is not keyword-only")
        elif derived.get("required") and not facts.get("param_required"):
            problems.append(f"`{derived['param']}` is no longer required")
        if remaining:
            problems.append(f"{remaining} call(s) still pass `{derived['param']}` positionally")
    else:
        if not facts["new_defined"]:
            problems.append(f"`{derived['name']}` is not defined in {derived['new_file']}")
        if facts["old_defined"]:
            problems.append(f"`{derived['name']}` is still defined in {derived['file']}")
        if remaining:
            problems.append(f"{remaining} import(s) still take `{derived['name']}` from its old module")
    return {"ok": not problems, "problems": problems, "remaining": remaining, "facts": facts}
