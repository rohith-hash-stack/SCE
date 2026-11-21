"""The code-review score (user, 2026-11-18, before the 3-arm re-baseline's launch): a descriptive, deterministic review
of the code each run leaves behind. It does not change the pre-registered metric, tests or readings.

`review(task, work, start_tree)` runs at scoring time (`runner.run_task`, before `score`, which applies the hidden
tests). It looks at the agent's changes to source files (what differs from the start state; test files left out) and
at the developer's own change (the task's commit against the start state, the same files left out), and checks:

1. **New undefined names** (Python, `pyflakes`): names a changed file uses but never defines or imports (a missing
   import, a typo), counted only if the start state's version of the file did not already have them. The smoke-test
   review's BASELINE run called `sys.exc_info()` without `import sys`.
2. **New syntax errors** (Python: `ast`; JS/TS: tree-sitter's error nodes): a changed file that parsed at the start
   and does not now.
3. **Helper reuse:** the functions the developer's added code calls that already exist in the repository (defined in a
   source file at the start state, and still defined after the developer's change, so a delete's target is not one). The share of them the agent's added code calls too. The smoke-test review's
   BASELINE run rebuilt the traceback itself instead of calling `create_plain_text_response_content`.
4. **Escaping kept:** if the developer's added code calls an escaping or sanitising function (`escape`, `quote`,
   `encodeURIComponent`, `sanitize...`), whether the agent's added code calls one too. The PRISM run put the
   traceback into the page unescaped.
5. **API match:** the other calls in the developer's added code (library and browser APIs such as
   `navigator.clipboard.writeText`; Python built-ins and the functions the change itself defines left out). The share
   the agent's added code makes too. A recall against one solution: it counts a fallback path the developer also
   wrote (`document.execCommand`) as much as the main call, so it can rank a run that used only the fallback (the
   smoke-test review's PRISM run) above one that used only the main call.

Each check is `None` where it does not apply (no Python file changed; the developer's change calls no existing helper,
no escaping, no external API). Calls are read from the added lines by one regular expression (`name(` or
`a.b.name(`, `self.`/`this.` dropped, keywords left out), the same for every arm. Checks 3-5 measure agreement with
the developer's change, which is one correct solution, not the only one; they are a reading, not a verdict.

The report (`rebaseline_report.code_review`) adds a sixth check from the scorer, **no regressions** (no
pass-to-pass test broken), and averages the applicable checks into one score in [0, 1] per run.
"""
from __future__ import annotations

import ast
import builtins
import re
import subprocess

PYFLAKES_UNDEFINED = ("UndefinedName", "UndefinedLocal", "UndefinedExport")
_CALL = re.compile(r"(?<![\w$.])((?:[A-Za-z_$][\w$]*\.)*[A-Za-z_$][\w$]*)\s*\(")
KEYWORDS = frozenset("""if elif else for while return def class function with not and or in is lambda yield await
async assert del print switch case catch typeof new super import export from raise throw except try finally match
constructor get set static void delete instanceof of let const var""".split())
ESCAPE_EXACT = frozenset({"escape", "quote", "quote_plus", "encodeURIComponent", "encodeURI", "escapeHtml",
                          "escapeHTML", "escape_html", "html_escape", "urlencode", "Markup"})
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|testing)/|(^|/)test_[^/]*\.py$|_test\.py$|\.(test|spec)\.[jt]sx?$")
_SOURCE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".html", ".css")


def _git(work: str, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=work, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def is_test(path: str, task: dict) -> bool:
    return path in set(task.get("test_files") or []) or bool(_TEST_PATH.search(path))


def _show(work: str, rev: str, path: str) -> str | None:
    r = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=work, capture_output=True)
    return r.stdout.decode("utf8", "replace") if r.returncode == 0 else None


def _added(work: str, a: str, b: str | None, files: list[str]) -> str:
    """The added lines (`+`) of the diff from tree/commit `a` to `b` (None: the working tree), these files only."""
    if not files:
        return ""
    args = ["diff", "--no-renames", "-U0", a] + ([b] if b else []) + ["--", *files]
    out = []
    for ln in _git(work, *args).splitlines():
        if ln.startswith("+") and not ln.startswith("+++"):
            s = ln[1:].strip()
            if not s.startswith(("#", "//", "*", "/*")):
                out.append(s)
    return "\n".join(out)


def calls(text: str) -> set[str]:
    out = set()
    for m in _CALL.finditer(text):
        name = re.sub(r"^(self|this|cls)\.", "", m.group(1))
        if name.split(".")[-1] in KEYWORDS or name in KEYWORDS:
            continue
        out.add(name)
    return out


def is_escape(name: str) -> bool:
    last = name.split(".")[-1]
    return last in ESCAPE_EXACT or last.lower().startswith(("escape", "sanitiz", "sanitis"))


def defined_in_repo(work: str, rev: str, names: set[str], task: dict) -> set[str]:
    """The names (by last segment) defined in a source file of `rev`: Python `def`/`class`, JS/TS `function`/`class`,
    or `name = ` / `name: ` / `const|let|var name`."""
    found = set()
    for name in sorted(names):
        last = name.split(".")[-1]
        if not re.fullmatch(r"[A-Za-z_$][\w$]*", last):
            continue
        pat = (rf"(def|class|function\*?)\s+{re.escape(last)}\b|(const|let|var)\s+{re.escape(last)}\b"
               rf"|^\s*(export\s+)?(async\s+)?{re.escape(last)}\s*[=:(]")
        hits = _git(work, "grep", "-l", "-E", pat, rev, "--").splitlines()
        if any(not is_test(h.split(":", 1)[-1], task) for h in hits):
            found.add(name)
    return found


def _undefined(src: str, path: str) -> set[str] | None:
    try:
        tree = ast.parse(src, path)
    except SyntaxError:
        return None
    from pyflakes import checker
    return {f"{type(m).__name__}:{m.message_args[0]}" for m in checker.Checker(tree, path).messages
            if type(m).__name__ in PYFLAKES_UNDEFINED}


def _parses(src: str, path: str) -> bool | None:
    if path.endswith(".py"):
        try:
            ast.parse(src, path)
            return True
        except SyntaxError:
            return False
    if path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts")):
        from prism.parser.tree_sitter_loader import parse_source
        parsed = parse_source(path, src.encode())
        return None if parsed is None else not parsed.root_node.has_error
    return None


def review(task: dict, work: str, start_tree: str) -> dict:
    """The review of the working tree in `work` against the start state `start_tree` (see the module docstring)."""
    import os
    changed = [f for f in _git(work, "diff", "--name-only", "--no-renames", start_tree).splitlines() if f]
    untracked = _git(work, "ls-files", "--others", "--exclude-standard").splitlines()
    files = sorted({f for f in changed + untracked if f.endswith(_SOURCE_EXT) and not f.startswith(".r3-")
                    and not is_test(f, task)})
    out: dict = {"source_files": files}
    if not files:
        return out
    # 1-2: undefined names and syntax, per changed file
    new_undef, new_syntax, py = [], [], False
    for f in files:
        path = os.path.join(work, f)
        after = open(path, encoding="utf8", errors="replace").read() if os.path.exists(path) else None
        before = _show(work, start_tree, f)
        if after is None:
            continue
        ok_after, ok_before = _parses(after, f), (_parses(before, f) if before is not None else True)
        if ok_after is False and ok_before is not False:
            new_syntax.append(f)
        if f.endswith(".py"):
            py = True
            ua = _undefined(after, f)
            ub = _undefined(before, f) if before is not None else set()
            if ua is not None:
                new_undef += [f"{f}: {u.split(':', 1)[1]}" for u in sorted(ua - (ub or set()))]
    out["new_undefined_names"] = new_undef if py else None
    out["new_syntax_errors"] = new_syntax
    # 3-5: against the developer's change (the task's commit, the same kind of files)
    new_files = [f for f in files if f in set(untracked)]
    added = _added(work, start_tree, None, [f for f in files if f not in set(new_files)])
    for f in new_files:                                   # a new file: every line is added
        added += "\n" + open(os.path.join(work, f), encoding="utf8", errors="replace").read()
    agent_calls = calls(added)
    commit = task.get("commit")
    gold_files = [f for f in (task.get("gold_files") or []) if f.endswith(_SOURCE_EXT) and not is_test(f, task)]
    if commit and gold_files and _git(work, "cat-file", "-t", commit).strip() == "commit":
        gold_added = _added(work, start_tree, commit, gold_files)
        own = {m.group(2) for m in re.finditer(r"\b(def|function\*?|class)\s+([A-Za-z_$][\w$]*)", gold_added)}
        own |= {q.split("::")[-1].split(".")[-1] for q in task.get("new_functions") or []}
        gold_calls = {c for c in calls(gold_added) if c.split(".")[-1] not in own}   # its own new functions: not APIs
        # an existing helper is defined before the developer's change and still after it (a delete's target, which
        # the change removes, is not one)
        repo = defined_in_repo(work, start_tree, gold_calls, task) & defined_in_repo(work, commit, gold_calls, task)
        esc = {c for c in gold_calls if is_escape(c)}
        external = {c for c in gold_calls - repo - esc if c.split(".")[-1] not in dir(builtins)}
        same = lambda c: c in agent_calls or any(a.split(".")[-1] == c.split(".")[-1] for a in agent_calls)
        out["helpers"] = sorted(repo)
        out["helpers_reused"] = sorted(c for c in repo if same(c))
        out["helper_reuse"] = round(len(out["helpers_reused"]) / len(repo), 4) if repo else None
        out["escaping_expected"] = sorted(esc)
        out["escaping_kept"] = (any(is_escape(a) for a in agent_calls)) if esc else None
        out["apis"] = sorted(external)
        out["apis_matched"] = sorted(c for c in external if same(c))
        out["api_match"] = round(len(out["apis_matched"]) / len(external), 4) if external else None
    else:
        out["reference"] = "no developer commit in the clone"
    return out


CHECKS = ("no_new_undefined_names", "no_new_syntax_errors", "helper_reuse", "escaping_kept", "api_match",
          "no_regressions")


def checks(rv: dict, score: dict) -> dict:
    """The six checks of one run, each in [0, 1] or None (not applicable), and their mean. A run that changed no
    source file has nothing to review: every check is None."""
    if not rv or not rv.get("source_files"):
        return {k: None for k in CHECKS + ("review_score",)}
    c = {"no_new_undefined_names": None if rv.get("new_undefined_names") is None
         else float(not rv["new_undefined_names"]),
         "no_new_syntax_errors": float(not rv.get("new_syntax_errors")),
         "helper_reuse": rv.get("helper_reuse"),
         "escaping_kept": None if rv.get("escaping_kept") is None else float(rv["escaping_kept"]),
         "api_match": rv.get("api_match"),
         "no_regressions": None if score.get("pass_to_pass_broken") is None
         else float(score["pass_to_pass_broken"] == 0)}
    vals = [v for v in c.values() if v is not None]
    c["review_score"] = round(sum(vals) / len(vals), 4) if vals else None
    return c
