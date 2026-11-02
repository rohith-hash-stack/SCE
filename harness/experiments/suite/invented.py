"""Invented calls in a newfn answer: scorer v2 (post-hoc R1 investigation §5, reports/r1/REPORT.md).

An answer's call is **invented** when it is
- a bare call `name(...)` (not after a `.`) whose name starts with a lower-case letter, or a call on `self.`,
  `cls.` or `this.` (only counted when the new function is a method of a known class);
- and it resolves to nothing below.

A call resolves, and is not invented, when its name is:
1. **bound in the answer itself:** a parameter of any function or lambda in it, a name it assigns (including loop,
   `with`, `except` and comprehension targets), a name it imports, or a function or class it defines;
2. **a built-in** (Python's `builtins`, or the JavaScript globals in `newfn._BUILTINS`);
3. **a name of the repository** at the task's parent revision: a function, method or class (`repo_leaves`), a
   module-level assignment (an alias such as `websocket_mask = _websocket_mask_python`), or a name imported
   anywhere in the repository;
4. **for `self.` / `cls.` / `this.` calls**, an attribute of the instance or class:
   - a method of the class (`class_methods`);
   - an attribute assigned on an instance anywhere in the repository (`self._socket_action = ...`), or at class
     level, or on a prototype;
   - a method or class attribute of a base class, followed through the repository by name;
   - an attribute of a built-in base class (`dict.get`).

   When a base class cannot be resolved (it comes from another package), the call is not counted. It is recorded
   as `unresolved` instead.

Comments and strings never contain calls, and a Markdown fence around the whole code is removed first (the same for
every version). Python is parsed with `ast` after removing the common indent. If it does
not parse (Python 2 code, an answer cut short), comments and strings are removed with `tokenize` and the calls are
found by pattern. JavaScript always uses patterns, after removing comments and string literals.

`calls()` in `newfn.py` is deliberately left as it was: it also feeds helper reuse, the pre-registered primary.
"""
from __future__ import annotations

import ast
import builtins
import collections
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import textwrap
import tokenize
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).parent / "repos"
SELF_NAMES = frozenset(("self", "cls", "this"))
_PY_KEYWORDS = frozenset("if for while return elif not and or in is with assert yield del raise except def class "
                         "lambda print exec async await".split())
_JS_KEYWORDS = frozenset("if for while switch catch function typeof return new await super yield delete void in of "
                         "instanceof do else try finally throw case class constructor get set static async".split())
BUILTIN_BASES = {"dict": dict, "list": list, "set": set, "frozenset": frozenset, "str": str, "bytes": bytes, "int": int,
                 "float": float, "tuple": tuple, "object": object, "Exception": Exception,
                 "BaseException": BaseException, "ValueError": ValueError, "TypeError": TypeError,
                 "KeyError": KeyError, "RuntimeError": RuntimeError, "OrderedDict": collections.OrderedDict,
                 "defaultdict": collections.defaultdict, "deque": collections.deque, "Counter": collections.Counter,
                 "UserDict": collections.UserDict, "MutableMapping": dict, "Mapping": dict, "MutableSet": set,
                 "MutableSequence": list, "Sequence": list}


# ------------------------------------------------------------------ the answer's calls and bound names
def _py_tree(code: str):
    try:
        return ast.parse(textwrap.dedent(code))
    except (SyntaxError, ValueError):
        return None


def _py_bound(tree, package: str = "") -> set[str]:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            a = n.args
            out |= {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs}
            out |= {x.arg for x in (a.vararg, a.kwarg) if x}
            if not isinstance(n, ast.Lambda):
                out.add(n.name)
        elif isinstance(n, ast.ClassDef):
            out.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)) and not _internal(n, package):
            out |= {(x.asname or x.name).split(".")[0] for x in n.names}
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            out |= set(n.names)
    return out


def _internal(n, package: str) -> bool:
    """An import from the repository itself (relative, or of its own package): it binds nothing on its own."""
    return isinstance(n, ast.ImportFrom) and (n.level > 0 or bool(package) and (n.module or "").split(".")[0] == package)


def _py_internal_imports(tree, package: str) -> set[str]:
    return {x.asname or x.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and _internal(n, package)
            for x in n.names}


def _py_calls(tree) -> list[tuple[str, str]]:
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.append(("bare", f.id))
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in SELF_NAMES:
                out.append(("self", f.attr))
    return out


def _py_strip(code: str) -> str:
    """Python source without comments and strings, by tokens (works for Python 2 too)."""
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(textwrap.dedent(code)).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                out.append(" ")
            elif tok.type in (tokenize.NEWLINE, tokenize.NL):
                out.append("\n")
            else:
                out.append(tok.string + " ")
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return re.sub(r"[ \t]*\.[ \t]*", ".", "".join(out))  # `os . unlink (` -> `os.unlink (`: still a method call


def _js_strip(code: str) -> str:
    code = re.sub(r"(?s)/\*.*?\*/", " ", code)
    code = re.sub(r"(?m)//.*$", " ", code)
    return re.sub(r"`(?:\\.|[^`\\])*`|'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"", "''", code)


def _idents(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z_$][\w$]*", text))


def _pattern_bound(text: str, lang: str) -> set[str]:
    out = set()
    if lang == "python":
        for m in re.finditer(r"\bdef\s+(\w+)\s*\(([^)]*)\)", text):
            out.add(m.group(1))
            out |= {p.split("=")[0].split(":")[0].strip(" *") for p in m.group(2).split(",")}
        for m in re.finditer(r"\blambda\s+([^:]*):", text):
            out |= _idents(m.group(1))
        for m in re.finditer(r"(?m)^\s*([\w\s,*()\[\]]+?)\s*(?:[+\-*/|&%]|//)?=(?!=)", text):
            out |= _idents(m.group(1))
        for m in re.finditer(r"\bfor\s+([\w\s,()]+?)\s+in\b", text):
            out |= _idents(m.group(1))
        out |= set(re.findall(r"\bas\s+(\w+)", text))
        out |= set(re.findall(r"\bexcept\s+[\w.()\s,]+?,\s*(\w+)\s*:", text))  # Python 2: `except E, e:`
        out |= set(re.findall(r"\bclass\s+(\w+)", text))
        for m in re.finditer(r"\bimport\s+([\w\s,.]+)", text):
            out |= {x.strip().split(".")[0] for x in m.group(1).split(",")}
    else:
        for m in re.finditer(r"\bfunction\s*([\w$]*)\s*\(([^)]*)\)", text):
            out |= {m.group(1)} | _idents(m.group(2))
        for m in re.finditer(r"\(([^()]*)\)\s*=>", text):
            out |= _idents(m.group(1))
        out |= set(re.findall(r"([A-Za-z_$][\w$]*)\s*=>", text))
        for m in re.finditer(r"\b(?:const|let|var)\s+([^=;]+?)\s*(?:=|;|\bof\b|\bin\b)", text):
            out |= _idents(m.group(1))
        out |= set(re.findall(r"\bcatch\s*\(\s*([\w$]+)", text))
        out |= set(re.findall(r"(?m)^\s*(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{", text))  # method shorthand
        out |= set(re.findall(r"\bclass\s+([\w$]+)", text))
    out.discard("")
    return out


def _pattern_calls(text: str, lang: str) -> list[tuple[str, str]]:
    out = []
    keywords = _PY_KEYWORDS if lang == "python" else _JS_KEYWORDS
    selfs = r"self|cls" if lang == "python" else r"this|self"
    for m in re.finditer(rf"\b(?:{selfs})\s*\.\s*([A-Za-z_$][\w$]*)\s*\(", text):
        out.append(("self", m.group(1)))
    for m in re.finditer(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(", text):
        before = text[max(0, m.start() - 9):m.start()]
        if m.group(1) not in keywords and not re.search(r"\b(?:def|function|class)\s+$", before):
            out.append(("bare", m.group(1)))
    return out


def answer_calls(code: str, lang: str, package: str = "") -> tuple[list[tuple[str, str]], set[str], set[str], str]:
    """(calls, names bound in the answer, names it imports from the repository itself, how it was read)."""
    if lang == "python":
        tree = _py_tree(code)
        if tree is not None:
            return _py_calls(tree), _py_bound(tree, package), _py_internal_imports(tree, package), "ast"
        text = _py_strip(code)
        internal = set()
        for m in re.finditer(r"\bfrom\s+(\.+[\w.]*|[\w.]+)\s+import\s+([\w\s,]+)", text):
            if m.group(1).startswith(".") or (package and m.group(1).split(".")[0] == package):
                internal |= {x.split(" as ")[-1].strip() for x in m.group(2).split(",") if x.strip()}
        return _pattern_calls(text, lang), _pattern_bound(text, lang) - internal, internal, "pattern"
    text = _js_strip(code)
    return _pattern_calls(text, lang), _pattern_bound(text, lang), set(), "pattern"


# ------------------------------------------------------------------ the repository's names at a revision
def _py_file_names(src: str, acc: dict) -> None:
    tree = _py_tree(src)
    if tree is None:
        text = _py_strip(src)
        acc["module"] |= set(re.findall(r"(?m)^([A-Za-z_]\w*)\s*(?::[^=\n]*)?=(?!=)", text))
        acc["attributes"] |= set(re.findall(r"\.\s*(\w+)\s*=(?!=)", text))
        for m in re.finditer(r"\bimport\s+([\w\s,.()]+)", text):
            acc["imported"] |= {x.split(" as ")[-1].strip(" ()").split(".")[0] for x in m.group(1).split(",")}
        for m in re.finditer(r"(?m)^\s*class\s+(\w+)\s*(?:\(([^)]*)\))?\s*:", text):
            c = acc["classes"][m.group(1)]
            c["bases"] |= {b.strip().rsplit(".", 1)[-1] for b in (m.group(2) or "").split(",") if b.strip() and "=" not in b}
            nxt = re.search(r"(?m)^\S", text[m.end():])  # the class body: up to the next line at column 0
            body = text[m.end():m.end() + nxt.start()] if nxt else text[m.end():]
            c["members"] |= set(re.findall(r"\bdef\s+(\w+)", body)) | set(re.findall(r"(?m)^\s+(\w+)\s*=(?!=)", body))
        return
    todo = list(tree.body)  # module level, including if / try / with / for blocks, not function or class bodies
    while todo:
        n = todo.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, (ast.AnnAssign, ast.AugAssign)) else []
        for t in targets:
            acc["module"] |= {x.id for x in ast.walk(t) if isinstance(x, ast.Name)}
        for field in ("body", "orelse", "finalbody", "handlers"):
            todo += [x for x in getattr(n, field, []) if isinstance(x, ast.AST)]
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            acc["imported"] |= {(x.asname or x.name).split(".")[0] for x in n.names}
        elif isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store):  # on any object: `instance._x = ...`
            acc["attributes"].add(n.attr)
        elif isinstance(n, ast.ClassDef):
            c = acc["classes"][n.name]
            for b in n.bases:
                if isinstance(b, ast.Name):
                    c["bases"].add(b.id)
                elif isinstance(b, ast.Attribute):
                    c["bases"].add(b.attr)
                elif isinstance(b, ast.Subscript):  # Generic[T], Dict[str, int]
                    v = b.value
                    c["bases"].add(v.id if isinstance(v, ast.Name) else getattr(v, "attr", "object"))
            for s in n.body:
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    c["members"].add(s.name)
                elif isinstance(s, (ast.Assign, ast.AnnAssign)):
                    for t in (s.targets if isinstance(s, ast.Assign) else [s.target]):
                        c["members"] |= {x.id for x in ast.walk(t) if isinstance(x, ast.Name)}


def _js_file_names(src: str, acc: dict) -> None:
    text = _js_strip(src)
    acc["module"] |= set(re.findall(r"(?m)^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)", text))
    acc["module"] |= set(re.findall(r"(?m)^(?:module\.)?exports\.([\w$]+)\s*=", text))
    acc["attributes"] |= set(re.findall(r"\.\s*([\w$]+)\s*=(?!=)", text))
    acc["attributes"] |= set(re.findall(r"\.prototype\.([\w$]+)\s*=", text))
    for m in re.finditer(r"\b(?:const|let|var)\s+\{([^}]*)\}\s*=\s*require", text):
        acc["imported"] |= {x.split(":")[-1].strip() for x in m.group(1).split(",") if x.strip()}
    for m in re.finditer(r"\bimport\s+(?:([\w$]+)\s*,?\s*)?(?:\{([^}]*)\})?\s*from\b", text):
        acc["imported"] |= {m.group(1)} if m.group(1) else set()
        acc["imported"] |= {x.split(" as ")[-1].strip() for x in (m.group(2) or "").split(",") if x.strip()}
    for m in re.finditer(r"\bclass\s+([\w$]+)(?:\s+extends\s+([\w$.]+))?\s*\{", text):
        c = acc["classes"][m.group(1)]
        if m.group(2):
            c["bases"].add(m.group(2).rsplit(".", 1)[-1])
        body = text[m.end():]
        nxt = re.search(r"\bclass\s+[\w$]+", body)
        c["members"] |= set(re.findall(r"(?m)^\s*(?:static\s+)?(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{",
                                       body[:nxt.start()] if nxt else body))


def scan_tree(root: str) -> dict:
    """Module-level names, imported names, instance attributes and classes (bases, members) of every source file."""
    acc = {"module": set(), "imported": set(), "attributes": set(),
           "classes": collections.defaultdict(lambda: {"bases": set(), "members": set()})}
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("node_modules", ".git") and not d.startswith(".")]
        for f in files:
            path = os.path.join(dirpath, f)
            try:
                src = Path(path).read_text(errors="replace")
            except OSError:
                continue
            if f.endswith(".py"):
                _py_file_names(src, acc)
            elif f.endswith((".js", ".mjs", ".cjs", ".ts")):
                _js_file_names(src, acc)
    return acc


def class_attributes(acc: dict, cls_leaf: str) -> tuple[set[str], bool]:
    """Members of the class and of all its base classes (by name through the repository, then built-ins), and whether
    some base could not be resolved."""
    seen, todo, out, unresolved = set(), [cls_leaf], set(), False
    while todo:
        c = todo.pop()
        if c in seen:
            continue
        seen.add(c)
        if c in acc["classes"]:
            out |= acc["classes"][c]["members"]
            todo += sorted(acc["classes"][c]["bases"])
        elif c in BUILTIN_BASES:
            out |= set(dir(BUILTIN_BASES[c]))
        elif c not in ("Generic", "Protocol", "ABC", "metaclass"):
            unresolved = True
    return out, unresolved


def repo_names_in(root: str, cls_qname: str | None) -> dict:
    """The repository-level names scorer v2 needs, from a checked-out tree."""
    acc = scan_tree(root)
    inherited, unresolved = class_attributes(acc, cls_qname.rsplit(".", 1)[-1]) if cls_qname else (set(), False)
    return {"module": sorted(acc["module"]), "imported": sorted(acc["imported"]),
            "attributes": sorted(acc["attributes"]), "inherited": sorted(inherited), "bases_unresolved": unresolved}


def repo_names_at(clone: str, rev: str, cls_qname: str | None) -> dict:
    """`repo_names_in` for the tree at `rev` of `clone` (read with `git archive`)."""
    with tempfile.TemporaryDirectory(prefix="names_") as tmp:
        data = subprocess.run(["git", "-C", clone, "archive", rev], capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            tar.extractall(tmp, filter="data")
        return repo_names_in(tmp, cls_qname)


@lru_cache(maxsize=None)
def _supplement(repo: str) -> dict:
    path = ROOT / repo / "newfn_names.json"
    return json.loads(path.read_text())["tasks"] if path.exists() else {}


def repo_names_for(task: dict) -> dict:
    """A task's repository names: planned with the task (`repo_names`), else the supplement for older task sets."""
    return task.get("repo_names") or _supplement(task.get("repo", "")).get(task.get("id", ""), {})


# ------------------------------------------------------------------ the scorer
def invented(task: dict, code: str, names: dict | None = None) -> dict:
    """{"invented": [names], "unresolved": [self. calls on classes with external bases], "read_as": ast|pattern}."""
    from harness.experiments.suite.fences import strip_outer_fence
    from harness.experiments.suite.newfn import _BUILTINS
    names = repo_names_for(task) if names is None else names
    code = strip_outer_fence(code)  # a fence around the whole code is formatting, not code
    lang = "python" if task.get("file", "").endswith(".py") else "js"
    parts = [p for p in task.get("file", "").split("/") if p not in ("src", "lib")]
    sites, bound, _internal_names, read_as = answer_calls(code, lang, parts[0] if len(parts) > 1 else "")
    repo = set(task.get("repo_leaves", [])) | set(names.get("module", [])) | set(names.get("imported", []))
    imports = " ".join(task.get("file_imports", []))
    members = set(task.get("class_methods", [])) | set(names.get("attributes", [])) | set(names.get("inherited", []))
    out, unresolved = [], []
    for kind, name in sites:
        if name in bound:
            continue
        if kind == "self":
            if not task.get("class_qname") or name in members:
                continue
            (unresolved if names.get("bases_unresolved") else out).append(name)
        elif name[:1].islower() and name not in _BUILTINS and name not in repo \
                and not re.search(rf"\b{re.escape(name)}\b", imports):
            out.append(name)
    return {"invented": out, "unresolved": unresolved, "read_as": read_as}
