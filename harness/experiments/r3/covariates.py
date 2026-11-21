"""R3a's per-task covariate (decided 2026-11-08, after the pilot): does the anchor's new code call a function that its
file neither defines nor imports?

    python -m harness.experiments.r3.covariates TASKS.json OUT.json NAME=CLONE ...

The pilot's three looping PRISM runs were exactly its three tasks with this property (`PILOT_LOOP_INVESTIGATION.md`):
the developer's commit added the helper, or its import, outside the anchor, so the remaining work is not only in the
callers.

**How:** the start state's anchor file (the parent's file with the start patch applied) is parsed: Python with `ast`,
JS/TS with tree-sitter. The calls are the bare calls (`name(...)`, not `obj.name(...)`) on the lines the start patch
adds. A called name is missing when nothing in the file binds it and it is not a builtin or a standard global.
"Binds" counts any binding anywhere in the file: a definition, an import, an assignment, a parameter, a destructured
name. So a name bound only in another function's scope counts as defined. The check is lenient on purpose: it
prefers missing a case to flagging a false one. A Python file with `from x import *` gives `"unknown"`.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import json
import re
import subprocess
import sys

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

JS_GLOBALS = frozenset("""
Array ArrayBuffer AbortController AbortSignal AggregateError Atomics BigInt BigInt64Array BigUint64Array Blob Boolean
Buffer BroadcastChannel DataView Date DOMException Error EvalError Event EventTarget FinalizationRegistry Float32Array
Float64Array FormData Function Headers Infinity Int16Array Int32Array Int8Array Intl JSON Map Math MessageChannel
NaN Number Object Promise Proxy RangeError ReadableStream ReferenceError Reflect RegExp Request Response Set
SharedArrayBuffer String Symbol SyntaxError TextDecoder TextEncoder TransformStream TypeError URIError URL
URLSearchParams Uint16Array Uint32Array Uint8Array Uint8ClampedArray WeakMap WeakRef WeakSet WritableStream
WebAssembly atob btoa clearImmediate clearInterval clearTimeout console crypto decodeURI decodeURIComponent
encodeURI encodeURIComponent escape eval fetch globalThis isFinite isNaN parseFloat parseInt performance process
queueMicrotask require setImmediate setInterval setTimeout structuredClone unescape import super
$state $derived $effect $props $bindable $inspect $host
""".split())

JS_BINDERS = {"variable_declarator": "name", "function_declaration": "name", "generator_function_declaration": "name",
              "class_declaration": "name", "function_expression": "name", "class": "name",
              "formal_parameters": None, "import_clause": None, "import_specifier": None, "namespace_import": None,
              "catch_clause": "parameter", "for_in_statement": "left", "arrow_function": "parameter",
              "required_parameter": None, "optional_parameter": None, "assignment_expression": "left"}


def added_lines(patch: str) -> set[int]:
    """The new-file line numbers of the lines a unified diff adds."""
    out, line = set(), None
    for ln in patch.splitlines():
        m = HUNK.match(ln)
        if m:
            line = int(m.group(1))
            continue
        if line is None or ln.startswith(("+++", "---")):
            continue
        if ln.startswith("+"):
            out.add(line)
            line += 1
        elif ln.startswith("-"):
            continue
        else:
            line += 1
    return out


def missing_python(text: str, lines: set[int]) -> list[str] | str:
    tree = ast.parse(text)
    bound = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__", "__package__", "__builtins__"}
    star = False
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            bound.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(n.name)
        elif isinstance(n, ast.arg):
            bound.add(n.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                if a.name == "*":
                    star = True
                bound.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            bound.update(n.names)
        elif hasattr(ast, "MatchAs") and isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name:
            bound.add(n.name)
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.lineno in lines}
    miss = sorted(called - bound)
    return "unknown" if (miss and star) else miss


def _js_parser(path: str):
    from tree_sitter import Language, Parser
    if path.endswith(".tsx"):
        import tree_sitter_typescript as m
        return Parser(Language(m.language_tsx()))
    if path.endswith((".ts", ".mts", ".cts")):
        import tree_sitter_typescript as m
        return Parser(Language(m.language_typescript()))
    import tree_sitter_javascript as m
    return Parser(Language(m.language()))


def _idents(node, out: set[str]) -> None:
    if node.type in ("identifier", "shorthand_property_identifier_pattern"):
        out.add(node.text.decode("utf8"))
    for c in node.children:
        _idents(c, out)


def missing_js(text: str, lines: set[int], path: str) -> list[str]:
    root = _js_parser(path).parse(text.encode("utf8")).root_node
    bound, called = set(JS_GLOBALS), set()
    stack = [root]
    while stack:
        n = stack.pop()
        stack.extend(n.children)
        if n.type in JS_BINDERS:
            field = JS_BINDERS[n.type]
            target = n.child_by_field_name(field) if field else n
            if target is not None:
                _idents(target, bound)
        if n.type == "call_expression":
            f = n.child_by_field_name("function")
            if f is not None and f.type == "identifier" and f.start_point[0] + 1 in lines:
                called.add(f.text.decode("utf8"))
    return sorted(called - bound)


def missing_names(task: dict, start_text: str) -> list[str] | str:
    lines = added_lines(task.get("start_patch") or "")
    if not lines:
        return []
    path = task["anchor_file"]
    if path.endswith(".py"):
        return missing_python(start_text, lines)
    return missing_js(start_text, lines, path)


def start_text(clone: str, task: dict) -> str:
    """The anchor file at the start state: the parent's file with the start patch applied (in a scratch index)."""
    import os
    import tempfile
    with tempfile.TemporaryDirectory(prefix="r3cov_") as tmp:
        work = os.path.join(tmp, "w")
        subprocess.run(["git", "-C", clone, "worktree", "add", "-q", "--detach", work, task["parent"]], check=True,
                       capture_output=True)
        try:
            if task.get("start_patch"):
                subprocess.run(["git", "apply", "-"], cwd=work, input=task["start_patch"], text=True, check=True,
                               capture_output=True)
            return open(os.path.join(work, task["anchor_file"]), encoding="utf8").read()
        finally:
            subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", work], capture_output=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("tasks")
    p.add_argument("out")
    p.add_argument("repos", nargs="+", metavar="NAME=CLONE")
    a = p.parse_args(argv)
    clones = dict(r.split("=", 1) for r in a.repos)
    doc = json.load(open(a.tasks))
    res = {}
    for t in doc["tasks"]:
        m = missing_names(t, start_text(clones[t["repo"]], t))
        res[t["id"]] = {"missing_function": m if m == "unknown" else bool(m), "names": [] if m == "unknown" else m}
    flagged = sorted(i for i, r in res.items() if r["missing_function"] is True)
    json.dump({"tasks_sha256": doc.get("tasks_sha256"), "rule": "anchor's new code (lines the start patch adds) calls a "
               "bare name its file neither defines nor imports (covariates.py)", "flagged": flagged, "tasks": res},
              open(a.out, "w"), indent=1, sort_keys=True)
    print(json.dumps({"tasks": len(res), "flagged": len(flagged), "unknown": sum(r["missing_function"] == "unknown"
                                                                                  for r in res.values())}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
