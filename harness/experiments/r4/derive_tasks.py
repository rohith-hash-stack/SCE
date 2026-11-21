"""Phase E's derived tasks (the user's decision, 2026-11-21): rename, signature change and module move, on definitions
the scout (`derive_scout.py`) found used from 3+ other files, each validated in the repository's own test environment.

    python -B -m harness.experiments.r4.derive_tasks SCOUT.json OUT.json --cache DIR --scratch DIR \
        --want rename=5 --want signature=5 --want move=5 --per-repo 2 NAME=CLONE ...

No model calls. A task states one change and is scored by:
1. **the repository's tests:** the test files that use the target, as the gold change leaves them (the scored tests,
   applied over the run's own copies, as R4's other tasks are), plus one generated test that asserts the change
   itself (the new name exists and the old one does not; the parameter is keyword-only; the function is defined in
   its new module);
2. **a static completeness check** (`derived_checks.py`): no Python file uses the old name, passes the parameter
   positionally, or imports the function from its old module, more often than the gold tree does.

**The gold change** is made mechanically from the start tree, in every file that uses the target:
- **rename:** every identifier and `__all__` string equal to the old name, in the target's own file and its dependent
  files. The new name is a verb synonym (`get_` -> `fetch_`, ...) or an `_impl`/`Impl` suffix, and appears nowhere
  in the repository before.
- **signature:** the last positional parameter (after `self`) becomes keyword-only (`*` before it), and every call
  in those files that passes it positionally names it.
- **move:** a module-level function whose body uses no other definition of its module moves to a new module beside
  its old one. The new module gets the imports its body needs, copied from the old module. Every
  `from <old> import name` is rewritten, and the old module imports it back only if it still uses it.

**A candidate becomes a task only if every check passes:**
- the environment installs;
- the scored test files pass at the start (3 runs);
- the gold tree passes the static check and every scored test (3 runs);
- the start tree fails the static check and at least one scored test (the generated one at least);
- no scored test is flaky.

Candidates have 3-10 dependent files and at most 30 uses to update (one run of 28 turns can make the whole change),
and are tried in a fixed order: grep's precision (the share of files with the name that use it) lowest first, then the
most dependent files. Per repository, at most `--per-repo` tasks per family.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tokenize

from harness.experiments.r4 import derived_checks as DC

FAMILIES = ("rename", "signature", "move")
MAX_TEST_FILES = 4
#: the change's size: 3+ dependent files (the decision), at most 10 files and 30 uses to update (28 turns)
MIN_FILES, MAX_FILES, MAX_USES = 3, 10, 30
VERBS = {"get": "fetch", "fetch": "retrieve", "make": "build", "build": "construct", "create": "build", "parse": "decode",
         "load": "read", "read": "load", "write": "emit", "check": "verify", "validate": "verify", "is": "check_is",
         "has": "check_has", "to": "convert_to", "from": "build_from", "set": "assign", "add": "append", "remove": "drop",
         "find": "locate", "format": "render", "render": "format", "run": "execute", "handle": "process",
         "process": "handle", "update": "refresh", "send": "dispatch", "resolve": "determine", "extract": "pull",
         "normalize": "canonicalize", "convert": "transform", "apply": "perform", "generate": "produce"}


def _git(cwd, *args, check=False):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, errors="replace", check=check)


def _read(work, rel):
    with open(os.path.join(work, rel), "rb") as fh:
        return fh.read().decode("utf8")


def _write(work, rel, text):
    path = os.path.join(work, rel)
    os.makedirs(os.path.dirname(path) or work, exist_ok=True)
    with open(path, "w", encoding="utf8") as fh:
        fh.write(text)


def importable(work: str, rel: str) -> str:
    """The module name a file is imported by: its path from the topmost directory that still has `__init__.py`."""
    parts = rel[:-3].split("/")
    dirs, stem = parts[:-1], parts[-1]
    j = len(dirs)
    while j > 0 and os.path.isfile(os.path.join(work, *dirs[:j], "__init__.py")):
        j -= 1                                                      # dirs[:j] is a package: go up
    mod = dirs[j:] + ([] if stem == "__init__" else [stem])
    return ".".join(mod)


def new_name(old: str, taken) -> str | None:
    if old[:1].isupper():
        options = [old + "Impl", "Base" + old, old + "Core"]
    else:
        head, _, rest = old.partition("_")
        options = [f"{VERBS[head]}_{rest}" if head in VERBS and rest else None, old + "_impl", old + "_fn"]
    for o in options:
        if o and o != old and not taken(o):
            return o
    return None


# ---------------------------------------------------------------------------------------------- gold changes
def _replace_tokens(text: str, old: str, new: str) -> str:
    """Every identifier equal to `old`, and every string literal whose value is `old`, renamed (quotes kept)."""
    lines = text.splitlines(keepends=True)
    edits = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.NAME and tok.string == old:
            edits.append((tok.start, tok.end, new))
        elif tok.type == tokenize.STRING:
            try:
                if ast.literal_eval(tok.string) == old:
                    edits.append((tok.start, tok.end, tok.string.replace(old, new)))
            except (ValueError, SyntaxError):
                pass
    for (sl, sc), (el, ec), rep in sorted(edits, reverse=True):
        if sl != el:
            continue
        ln = lines[sl - 1]
        lines[sl - 1] = ln[:sc] + rep + ln[ec:]
    return "".join(lines)


def gold_rename(work, c, new) -> dict:
    return {f: _replace_tokens(_read(work, f), c["leaf"], new) for f in sorted(set(c["dependents"]) | {c["file"]})}


def _offset(lines, line, col):
    return sum(len(x) for x in lines[:line - 1]) + col


def gold_signature(work, c) -> tuple[dict, dict] | None:
    """Make the last positional parameter keyword-only; name it at every positional call."""
    tree = ast.parse(_read(work, c["file"]))
    qual = _qual(c)
    node = DC.find_def(tree, qual)
    if node is None or node.args.vararg or node.args.posonlyargs:
        return None
    args = node.args.args
    is_method = c["kind"] == "method" and args and args[0].arg in ("self", "cls")
    first = 1 if is_method else 0
    if len(args) <= first:
        return None
    p = args[-1]
    limit = len(args) - 1 - first                                   # positional arguments a call may still pass
    required = len(node.args.defaults) == 0
    text = _read(work, c["file"])
    lines = text.splitlines(keepends=True)
    off = _offset(lines, p.lineno, p.col_offset)
    changed = {c["file"]: text[:off] + "*, " + text[off:]}
    for f in sorted(set(c["dependents"]) | {c["file"]}):
        src = changed.get(f, _read(work, f))
        t = ast.parse(src)
        sites = []
        for n in ast.walk(t):
            if isinstance(n, ast.Call):
                fn = n.func
                callee = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
                if callee != c["leaf"] or any(isinstance(a, ast.Starred) for a in n.args):
                    continue
                if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == qual.split(".")[0] \
                        and is_method:
                    return None                                     # `Class.method(obj, ...)`: self passed explicitly
                if len(n.args) > limit:
                    if len(n.args) > limit + 1:
                        return None
                    sites.append(n.args[limit])
        if sites:
            ls = src.splitlines(keepends=True)
            for a in sorted(sites, key=lambda a: (a.lineno, a.col_offset), reverse=True):
                o = _offset(ls, a.lineno, a.col_offset)
                src = src[:o] + f"{p.arg}=" + src[o:]
            changed[f] = src
    return changed, {"param": p.arg, "max_positional": limit, "required": required}


def _outside_bases(work: str, rel: str, qual: str) -> list[str]:
    """The base classes of a method's class that no repository file defines (a library's `BaseModel`): the method may
    override one of theirs, which a rename or a signature change would break."""
    cls = DC.find_def(ast.parse(_read(work, rel)), qual.rsplit(".", 1)[0])
    out = []
    for b in getattr(cls, "bases", []):
        name = b.attr if isinstance(b, ast.Attribute) else b.id if isinstance(b, ast.Name) else \
            b.value.id if isinstance(b, ast.Subscript) and isinstance(b.value, ast.Name) else None
        if name in (None, "object"):
            continue
        if not _git(work, "grep", "-l", "-E", rf"^\s*class\s+{name}\b", "--", "*.py").stdout.strip():
            out.append(name)
    return out


NOT_PRODUCT = re.compile(r"(^|/)(examples?|docs?|scripts?|benchmarks?|bench|tools)/")


def _qual(c) -> str:
    """The definition's name within its module (`Class.method`)."""
    rel = c["file"][:-3].replace("/", ".")
    rel = rel[:-9] if rel.endswith(".__init__") else rel
    return c["qname"][len(rel) + 1:] if c["qname"].startswith(rel + ".") else c["qname"].rsplit(".", 1)[-1]


def gold_move(work, c, new_file) -> dict | None:
    old = c["file"]
    text = _read(work, old)
    tree = ast.parse(text)
    fn = next((n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == c["leaf"]),
              None)
    if fn is None:
        return None
    lines = text.splitlines(keepends=True)
    first = min([fn.lineno] + [d.lineno for d in fn.decorator_list])
    body = "".join(lines[first - 1:fn.end_lineno])
    free = set(c.get("move_free_names") or [])
    imports, future = [], []
    for n in tree.body:
        stmts = [n]
        if isinstance(n, ast.If):                                   # `if TYPE_CHECKING:` blocks
            stmts = [x for x in n.body if isinstance(x, (ast.Import, ast.ImportFrom))]
        for s in stmts:
            if isinstance(s, ast.ImportFrom) and s.module == "__future__":
                future.append(ast.unparse(s))
            elif isinstance(s, (ast.Import, ast.ImportFrom)):
                keep = [a for a in s.names if (a.asname or a.name).split(".")[0] in free]
                if keep:
                    s2 = type(s)(**{**{k: getattr(s, k) for k in s._fields}, "names": keep})
                    imports.append(ast.unparse(s2))
    bound = {x for n in tree.body for x in _bindings(n)}
    if free - bound - set(dir(__import__("builtins"))):
        return None                                                 # a free name the module does not bind at all
    head = "\n".join(future + sorted(set(imports), key=imports.index))
    new_text = (head + "\n\n\n" if head else "") + body.rstrip("\n") + "\n"
    rest = "".join(lines[:first - 1]) + "".join(lines[fn.end_lineno:])
    rest = re.sub(r"\n{4,}", "\n\n\n", rest)
    new_stem = os.path.splitext(os.path.basename(new_file))[0]
    changed = {new_file: new_text}
    if DC.name_uses(rest, c["leaf"]):                               # the old module still uses it: import it back
        rest_tree = ast.parse(rest)
        last = max([n.end_lineno for n in rest_tree.body if isinstance(n, (ast.Import, ast.ImportFrom))] or [0])
        rl = rest.splitlines(keepends=True)
        rest = "".join(rl[:last]) + f"from .{new_stem} import {c['leaf']}\n" + "".join(rl[last:])
    changed[old] = rest
    old_names = DC.module_names(old) | {importable(work, old)}
    new_mod = importable(work, new_file)
    for f in sorted(set(c["dependents"]) - {old}):
        src = _read(work, f)
        t = ast.parse(src)
        sl = src.splitlines(keepends=True)
        edits = []
        for n in ast.walk(t):
            if not isinstance(n, ast.ImportFrom) or not any(a.name == c["leaf"] for a in n.names):
                continue
            if DC._resolve(f, n.module, n.level) not in old_names:
                continue
            mine = [a for a in n.names if a.name == c["leaf"]]
            others = [a for a in n.names if a.name != c["leaf"]]
            if n.level:
                if not n.module:
                    return None                                     # `from . import name` of a package's own name
                new_from = "." * n.level + ".".join(n.module.split(".")[:-1] + [new_stem])
            else:
                new_from = new_mod
            indent = re.match(r"\s*", sl[n.lineno - 1]).group(0)
            moved = f"{indent}from {new_from} import " + ", ".join(ast.unparse(a) for a in mine) + "\n"
            kept = (indent + ast.unparse(ast.ImportFrom(module=n.module, names=others, level=n.level)) + "\n"
                    if others else "")
            edits.append((n.lineno, n.end_lineno, kept + moved))
        for a, b, rep in sorted(edits, reverse=True):
            sl[a - 1:b] = [rep]
        if edits:
            changed[f] = "".join(sl)
    return changed


def _bindings(n) -> set:
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {n.name}
    if isinstance(n, (ast.Import, ast.ImportFrom)):
        return {(a.asname or a.name).split(".")[0] for a in n.names}
    if isinstance(n, ast.If):
        return {x for s in n.body + n.orelse for x in _bindings(s)}
    if isinstance(n, ast.Try):
        return {x for s in n.body + n.orelse + n.finalbody for x in _bindings(s)} | \
            {x for h in n.handlers for s in h.body for x in _bindings(s)}
    out = set()
    for t in (n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, (ast.AnnAssign, ast.AugAssign))
              else []):
        out |= {x.id for x in ast.walk(t) if isinstance(x, ast.Name)}
    return out


# ---------------------------------------------------------------------------------------------- the generated test
def generated_test(family: str, mod: str, qual: str, details: dict) -> str:
    owner_path = qual.split(".")[:-1]
    leaf = qual.split(".")[-1]
    get_owner = "owner = importlib.import_module(%r)\n" % mod + "".join(
        f"    owner = getattr(owner, {p!r})\n" for p in owner_path)
    if family == "rename":
        return ("import importlib\n\n\n"
                f"def test_phase2_rename_{details['new']}():\n"
                f"    {get_owner}"
                f"    assert hasattr(owner, {details['new']!r})\n"
                f"    assert not hasattr(owner, {leaf!r})\n")
    if family == "signature":
        return ("import importlib\nimport inspect\n\n\n"
                f"def test_phase2_keyword_only_{details['param']}():\n"
                f"    {get_owner}"
                f"    p = inspect.signature(getattr(owner, {leaf!r})).parameters[{details['param']!r}]\n"
                "    assert p.kind is inspect.Parameter.KEYWORD_ONLY\n")
    return ("import importlib\n\n\n"
            f"def test_phase2_moved_{leaf}():\n"
            f"    new = importlib.import_module({details['new_module']!r})\n"
            f"    assert getattr(new, {leaf!r}).__module__ == {details['new_module']!r}\n")


# ---------------------------------------------------------------------------------------------- validation
def _run_states(env, files):
    from harness.experiments.r3.gate import run_states
    ensure = getattr(env, "ensure_current_build", None)
    if ensure is not None:
        ensure()                                                    # compiled packages: build what is on disk
    return run_states(env, files)


def build(c: dict, family: str, work: str, env, rev: str) -> dict:
    """One candidate, validated: a task record, or `{"rejected": reason}`."""
    from harness.experiments.r3.gate import classify, reset_to
    from harness.experiments.r3.runner import changed_functions, snapshot_tree
    reset_to(work, rev)
    known = set(env.test_files())
    tests = [f for f in c["test_dependents"] if f in known][:MAX_TEST_FILES]
    if not tests:
        return {"rejected": "no runnable test file uses it"}
    qual = _qual(c)
    taken = lambda name: bool(_git(work, "grep", "-l", "-w", name, "--").stdout.strip())
    if c["kind"] == "method":
        outside = _outside_bases(work, c["file"], qual)
        if outside:
            return {"rejected": f"method of a class with an outside base (may override it): {outside}"}
    details: dict = {}
    if family == "rename":
        nn = new_name(c["leaf"], taken)
        if nn is None:
            return {"rejected": "no free new name"}
        changed = gold_rename(work, c, nn)
        details = {"new": nn}
        derived = {"kind": "rename", "file": c["file"], "qualname": qual, "old": c["leaf"], "new": nn}
    elif family == "signature":
        r = gold_signature(work, c)
        if r is None:
            return {"rejected": "no rewritable signature"}
        changed, details = r
        derived = {"kind": "signature", "file": c["file"], "qualname": qual, **details}
    else:
        stem = os.path.splitext(os.path.basename(c["file"]))[0].lstrip("_")
        new_file = os.path.join(os.path.dirname(c["file"]), f"{stem}_{c['leaf']}.py".lstrip("_"))
        if os.path.exists(os.path.join(work, new_file)):
            return {"rejected": "new module exists"}
        changed = gold_move(work, c, new_file)
        if changed is None:
            return {"rejected": "not movable"}
        details = {"new_file": new_file, "new_module": importable(work, new_file)}
        derived = {"kind": "move", "file": c["file"], "name": c["leaf"], "new_file": new_file}
    for f, text in changed.items():
        try:
            ast.parse(text)
        except SyntaxError as exc:
            return {"rejected": f"gold does not parse: {f}: {exc}"}
    gen_rel = os.path.join(os.path.dirname(tests[0]), f"test_phase2_{family}_{c['leaf']}.py")
    gen = generated_test(family, importable(work, c["file"]), qual, details)
    scored = tests + [gen_rel]
    # 1. the start: the scored test files pass, and the start tree fails the static check
    start = _run_states(env, tests)
    bad = [k for k, v in start.items() if k != "__unparsed__" and v[0] != "passed"]
    if bad or start["__unparsed__"]:
        return {"rejected": f"tests do not pass at the start: {bad[:3] or start['__unparsed__']}"}
    start_tree = snapshot_tree(work)
    # 2. the gold tree
    for f, text in changed.items():
        _write(work, f, text)
    _write(work, gen_rel, gen)
    gold_measure = DC.measure(derived, work)
    derived["baseline"] = gold_measure["counts"]
    chk = DC.check(derived, work)
    if not chk["ok"]:
        return {"rejected": f"gold fails the static check: {chk['problems']}"}
    gold_tree = snapshot_tree(work)
    end = _run_states(env, scored)
    gold_bad = [k for k, v in end.items() if k != "__unparsed__" and v[0] != "passed"]
    if gold_bad or end["__unparsed__"]:
        return {"rejected": f"gold fails tests: {gold_bad[:3] or end['__unparsed__']}"}
    test_set = set(tests) | {gen_rel}
    hidden = _git(work, "diff", "--binary", start_tree, gold_tree, "--", *sorted(test_set)).stdout
    gold_patch = _git(work, "diff", "--binary", start_tree, gold_tree).stdout
    gold_files = sorted(f for f in changed if f not in test_set)
    gold_functions = sorted(changed_functions(work, start_tree, gold_files)) if gold_files else []
    # 3. the start with the scored tests: fail-to-pass and pass-to-pass
    _git(work, "checkout", "-q", "-f", start_tree, "--", ".")
    _git(work, "clean", "-fdq", "-e", ".r3-*")
    if DC.check(derived, work)["ok"]:
        return {"rejected": "the start tree already passes the static check"}
    p = subprocess.run(["git", "-C", work, "apply", "--whitespace=nowarn", "-"], input=hidden, text=True,
                       capture_output=True)
    if p.returncode:
        return {"rejected": f"hidden tests do not apply: {p.stderr[:200]}"}
    hidden_start = _run_states(env, scored)
    cls = classify(hidden_start, end)
    if cls["flaky"]:
        return {"rejected": f"flaky: {cls['flaky'][:3]}"}
    if not cls["fail_to_pass"]:
        return {"rejected": "no fail-to-pass test"}
    visible_start_pass = {}
    for k, v in start.items():
        if k != "__unparsed__" and v[0] == "passed":
            f, t = k.split("::", 1)
            visible_start_pass.setdefault(f, []).append(t)
    reset_to(work, rev)
    return {"tests": tests, "generated_test": gen_rel, "hidden_patch": hidden, "gold_patch": gold_patch,
            "fail_to_pass": cls["fail_to_pass"], "pass_to_pass": cls["pass_to_pass"], "derived": derived,
            "details": details, "gold_files": gold_files, "gold_functions": gold_functions,
            "visible_start_pass": visible_start_pass}


def prompt_for(family: str, c: dict, r: dict, turns: int) -> tuple[str, str]:
    qual = _qual(c)
    kind = {"method": "method", "class": "class"}.get(c["kind"], "function")
    display = f"the {kind} `{qual}`"
    line = c["lines"][0]
    tests = ", ".join(f"`{t}`" for t in r["tests"])
    check = (f"When you call done, the harness checks your work: {{goal}}, and the tests in {tests} that passed at the "
             f"start still pass (a test you delete is not counted). If the check fails, done is rejected, you are "
             f"told what is still missing, and you continue. Run `run_tests` yourself before calling done. You have "
             f"{turns} turns in total; a rejected done uses one.")
    if family == "rename":
        new = r["details"]["new"]
        goal = f"`{qual}` is renamed to `{new}` and no code still uses the old name `{c['leaf']}`"
        text = (f"Rename {display} in `{c['file']}` (it is defined at line {line}) to `{new}`. Update every use of it "
                f"across the repository so nothing refers to the old name: calls, imports, re-exports, `__all__` "
                f"entries, type annotations, attribute uses and tests. Do not keep an alias under the old name. "
                f"Remaining tests must still pass.")
    elif family == "signature":
        p = r["details"]["param"]
        goal = f"the parameter `{p}` of `{qual}` is keyword-only and no call passes it positionally"
        text = (f"Make the parameter `{p}` of {display} in `{c['file']}` (it is defined at line {line}) "
                f"keyword-only: callers must pass it as `{p}=...`. Keep it otherwise as it is "
                f"({'still required' if r['derived'].get('required') else 'same default'}). Update every call that "
                f"passes it positionally, across the repository and its tests. Remaining tests must still pass.")
    else:
        nf = r["details"]["new_file"]
        goal = f"`{c['leaf']}` is defined in `{nf}` and nothing imports it from `{c['file']}`"
        text = (f"Move the function `{c['leaf']}` from `{c['file']}` (it is defined at line {line}) into a new module "
                f"`{nf}`. Update every import of it across the repository, including tests and package re-exports, to "
                f"import it from the new module. `{c['file']}` must no longer define or re-export it (it may import "
                f"it from the new module if it still uses it itself). Remaining tests must still pass.")
    return text + "\n\nWhen your changes are complete, call done.\n\n" + check.format(goal=goal), display


def ranked(scout: dict, family: str) -> list[dict]:
    out = []
    for name, rep in scout.items():
        for c in rep["candidates"]:
            if not c["test_dependents"] or len(c["dependents"]) - len(c["test_dependents"]) < 1:
                continue
            if NOT_PRODUCT.search(c["file"]):
                continue                                            # examples, docs, scripts: not the product
            if not MIN_FILES <= len(c["dependents"]) <= MAX_FILES or sum(c["use_kinds"].values()) > MAX_USES:
                continue                                            # one run can make the whole change
            if family == "signature" and not (c.get("params") and c.get("positional_calls")):
                continue
            if family == "signature":
                pc = c["positional_calls"]
                n_args = len(c["params"]) - (1 if c["kind"] == "method" else 0)
                files = [f for f, xs in pc.items() if any(x >= n_args for x in xs)]
                if len(files) < 3:
                    continue
            if family == "move" and not (c["kind"] == "function" and c.get("move_self_contained")
                                         and c.get("move_from_imports_only")):
                continue
            if family in ("rename", "move") and "." in _qual(c) and c["kind"] == "function":
                continue                                            # nested functions
            out.append(c)
    return sorted(out, key=lambda c: (c["grep_precision"] if c["grep_precision"] is not None else 1.0,
                                      -len(c["dependents"]), c["repo"], c["qname"]))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("scout")
    p.add_argument("out")
    p.add_argument("--cache", required=True)
    p.add_argument("--scratch", required=True)
    p.add_argument("--want", action="append", default=[], help="family=N")
    p.add_argument("--per-repo", type=int, default=2)
    p.add_argument("--max-attempts", type=int, default=12, help="per family and repository")
    p.add_argument("--turns", type=int, default=28)
    p.add_argument("--repos", default=None, help="comma-separated, in this order")
    p.add_argument("--cleanup", action="store_true", help="remove each repository's worktree when done with it")
    p.add_argument("clones", nargs="+")
    a = p.parse_args(argv)
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))
    scout = json.load(open(a.scout))
    clones = dict(x.split("=", 1) for x in a.clones)
    want = {k: int(v) for k, v in (w.split("=") for w in a.want)}
    state = json.load(open(a.out)) if os.path.exists(a.out) else {"tasks": [], "attempts": []}
    from harness.experiments.r3.shared_tools import make_env
    for repo in (a.repos.split(",") if a.repos else sorted(scout)):
        rev = scout[repo]["rev"]
        need = {f: n for f, n in want.items()
                if sum(1 for t in state["tasks"] if t["family"] == f) < n
                and sum(1 for t in state["tasks"] if t["family"] == f and t["repo"] == repo) < a.per_repo}
        if not need:
            continue
        work = os.path.join(a.scratch, f"derive_{repo}")
        if not os.path.isdir(work):
            _git(clones[repo], "worktree", "prune")
            subprocess.run(["git", "-C", clones[repo], "worktree", "add", "-q", "--detach", work, rev], check=True)
        env = make_env(clones[repo], rev, work, a.cache)
        try:
            env.setup()
        except Exception as exc:
            state["attempts"].append({"repo": repo, "setup_failed": str(exc)[:300]})
            json.dump(state, open(a.out, "w"), indent=1)
            print(repo, "setup failed", flush=True)
            continue
        for family in FAMILIES:
            if family not in need:
                continue
            tried = 0
            for c in [c for c in ranked({repo: scout[repo]}, family)]:
                if sum(1 for t in state["tasks"] if t["family"] == family) >= want[family] or \
                        sum(1 for t in state["tasks"] if t["family"] == family and t["repo"] == repo) >= a.per_repo or \
                        tried >= a.max_attempts:
                    break
                if any(x.get("qname") == c["qname"] and x.get("family") == family for x in state["attempts"]):
                    continue
                if any(t["anchor_qname"] == c["qname"] for t in state["tasks"]):
                    continue                                        # one task per target
                tried += 1
                try:
                    r = build(c, family, work, env, rev)
                except Exception as exc:                            # a candidate's own failure, never the run's
                    r = {"rejected": f"{type(exc).__name__}: {str(exc)[:200]}"}
                state["attempts"].append({"repo": repo, "family": family, "qname": c["qname"],
                                          **({"rejected": r["rejected"]} if "rejected" in r else {"ok": True})})
                if "rejected" not in r:
                    prompt, display = prompt_for(family, c, r, a.turns)
                    n = sum(1 for t in state["tasks"] if t["family"] == family) + 1
                    state["tasks"].append({
                        "id": f"p2-{family}-{n:02d}", "family": family, "repo": repo, "language": "python",
                        "commit": rev, "parent": rev, "subject": f"derived {family}: {c['qname']}",
                        "start_patch": None, "after_index_patch": None, "start_files": [], "start_functions": [],
                        "test_files": [], "hidden_patch": r["hidden_patch"], "visible_tests": r["tests"],
                        "tests_run": r["tests"] + [r["generated_test"]], "fail_to_pass": r["fail_to_pass"],
                        "pass_to_pass": r["pass_to_pass"], "visible_start_pass": r["visible_start_pass"],
                        "gold_files": r["gold_files"], "gold_functions": r["gold_functions"], "prompt": prompt,
                        "anchor": f"{c['file']}::{_qual(c)}", "anchor_qname": c["qname"], "target_display": display,
                        "target_line": c["lines"][0], "derived": r["derived"], "done_gate": True,
                        "explicit_tools": True, "restore_scored_tests": True,
                        "dependents": {"files": len(c["dependents"]), "test_files": len(c["test_dependents"]),
                                       "agreed": len(c["agreed"]), "prism_only": len(c["prism_only"]),
                                       "benchmark_only": len(c["benchmark_only"]),
                                       "grep_precision": c["grep_precision"],
                                       "grep_noise_files": c["grep_noise_files"],
                                       "qualified_uses": c["qualified_uses"], "aliased_imports": c["aliased_imports"],
                                       "reexports": len(c["reexports"])},
                        "gold_patch_sha": __import__("hashlib").sha256(r["gold_patch"].encode()).hexdigest(),
                    })
                    _write(os.path.dirname(os.path.abspath(a.out)), f"gold/{state['tasks'][-1]['id']}.patch",
                           r["gold_patch"])
                json.dump(state, open(a.out, "w"), indent=1)
                print(repo, family, c["qname"], r.get("rejected", "OK"), flush=True)
        if a.cleanup:
            _git(clones[repo], "worktree", "remove", "--force", work)
            shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
