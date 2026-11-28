"""PRISM's per-turn gateway (POC 2's PRISM_GW arm; `harness/experiments/r4/AMENDMENTS.md`, "Phase 3, POC 2").

The gateway sits between an agent and its tools. On every turn it sees the call the agent is about to make, and the
answer after it ran. It knows one thing the agent does not: PRISM's graph of the task's target, i.e. every place that
uses the definition the task names (the places `prism_find_references` lists) and every other use of the same name
(look-alikes: other objects' names). From that it does seven things, each logged as an *intervention*:

| type | when | what | blocking |
|---|---|---|---|
| `suggest_tool` | the first answer, and a search for the target's name before `prism_find_references` was asked | names the tool that lists every place | no |
| `search_augment` | a search whose hits include tracked lines | which hits are the target's places, which are look-alikes | no |
| `wrong_target` | a PRISM query about a look-alike of the target (same name, other object) | says it is not the task's target | no |
| `worklist` | after each applied edit to a Python file | the places that still use the old name; look-alikes changed | no |
| `stall` | 5 calls without an applied edit while places are pending, or a call the harness refused as a loop | the next pending places | no |
| `block_lookalike` | an edit that would change a look-alike's use of the name | not applied; says why | yes |
| `block_syntax` | an edit that would leave a Python file that parsed unparseable (e.g. a branch with no body); every task | not applied; says why | yes |
| `block_done` | `done` while places are pending or look-alikes changed | not accepted; lists them | yes |

Every block can be overridden: the same call sent again with `"confirm": true` is let through. The outcome of each
intervention is recorded:
- a block is **prevented** when the agent does not override it within the next `WINDOW` calls (or the run ends), and
  **ignored** when it does;
- guidance (`suggest_tool`, `worklist`, `stall`, `wrong_target`) is **followed** when the agent does what it points
  to within `WINDOW` calls (asks `prism_find_references` about the target; makes an edit that reduces the pending
  count), else **ignored**;
- `search_augment` is **ignored** if a look-alike it named is later changed, else **followed**.

At the end (`finish`) each intervention also records whether the places it named are correct in the final tree
(renamed / removed places, unchanged look-alikes): `places_ok`. Whether an intervention *contributed* to a solved
task is decided in the report, which knows the hidden tests' verdict (`rebaseline_report.gateway_effectiveness`).

How places are tracked: the gateway counts, on each tracked line, the uses of the name at the start (place lines:
every occurrence of the word; look-alike lines: the name's own tokens, never strings or comments). Each applied edit
maps the file's old lines to its new ones (`difflib`), so the counts follow the lines as they move. A place line is
**pending** while it still holds more occurrences than its look-alike uses; a look-alike line is **changed** when it
holds fewer name tokens than at the start, or was deleted.

**Which tasks:** by default (POC 2, Phase 3b), only a rename: the place tracking, the worklist, the look-alike blocks
and the `done` block apply to tasks whose prompt starts with "Rename". On any other task the gateway makes only the
syntax check, and records why in `error`.

**`families=True`** (the breadth study, `harness/experiments/r4/breadth/GATEWAY.md`) adds three task families, each
with its own places. Everything else (the interventions, their outcomes, the overrides, `finish`) is the same:

| family | prompt | a place | pending while |
|---|---|---|---|
| delete | "Remove ..." | as a rename: each line PRISM lists as a use of the target (its definition included); look-alikes tracked | the line still holds the name (more often than its look-alike uses), as for a rename |
| signature | "Make the parameter `P` of ... keyword-only" | the definition, and each call site PRISM resolves to the target that passes `P` positionally | the definition still takes `P` positionally; a call to the name on that line still passes more positional arguments than come before `P` (no `*args`) |
| move | "Move the function `N` from `OLD` ... into a new module `NEW`" | the definition in `OLD`, each `from OLD import N` (and `OLD.N` through `import OLD`) in any file, and `N` being defined in `NEW` | `OLD` still defines `N` at its top level; the line still imports `N` from `OLD`; `NEW` does not define `N` yet |

A signature or move place is checked by parsing the file (`ast`), never by the name's text; these families track no
look-alikes (their edits keep the name). The exact-text note (arm C) stays a rename's.

The gateway has no harness code in it. It reads the working tree (`root`) and asks `refs(symbol)` (a callable
returning `prism_find_references`' places as data: `target`, `leaf`, `rows`, `possible`; the harness passes
`ToolLayer.reference_places`) about the target, once at the start and to resolve PRISM queries' symbols.
"""
from __future__ import annotations

import ast
import difflib
import io
import json
import os
import re
import subprocess
import tokenize
from dataclasses import dataclass

WINDOW = 3                  # calls within which guidance counts as followed, and a block as overridden
STALL_CALLS = 5             # calls without an applied edit, while places are pending, before a `stall` note
MAX_LISTED = 12             # places listed in one message
MAX_SUGGEST = 2             # `suggest_tool` notes per run
SEARCH_TOOLS = ("grep", "prism_search", "semantic_search", "ast_search")
QUERY_TOOLS = ("prism_find_references", "prism_change_context", "prism_blast_radius", "prism_read_code",
               "find_references")
PREFIX = "PRISM gateway: "
_HIT = re.compile(r"^([^\s:]+\.py):(\d+):", re.M)
RENAME = re.compile(r"\s*Rename\b")
REMOVE = re.compile(r"\s*Remove\b")
SIGNATURE = re.compile(r"\s*Make the parameter `(\w+)` of .*?\bkeyword-only", re.S)
MOVE = re.compile(r"\s*Move the function `(\w+)` from `([\w./-]+\.py)`.*?\binto a new module `([\w./-]+\.py)`", re.S)


def family_of(prompt: str, families: bool) -> str | None:
    """The task family the gateway tracks places for (None: the syntax check only)."""
    if RENAME.match(prompt):
        return "rename"
    if not families:
        return None
    if REMOVE.match(prompt):
        return "delete"
    if SIGNATURE.match(prompt):
        return "signature"
    if MOVE.match(prompt):
        return "move"
    return None


def _module_names(rel: str) -> set[str]:
    """The dotted names a file can be imported by (with and without a leading `src.` / `lib.`)."""
    m = rel[:-3].replace("/", ".")
    m = m[:-9] if m.endswith(".__init__") else m
    return {m} | {m[len(p):] for p in ("src.", "lib.") if m.startswith(p)}


def _find_def(tree, qualname: str):
    """The `def`/`class` node at `qualname` (`Class.method`) within a module, and whether its parent is a class."""
    body, node, in_class = (tree.body if tree is not None else []), None, False
    for part in qualname.split("."):
        in_class = isinstance(node, ast.ClassDef)
        node = next((n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                     and n.name == part), None)
        if node is None:
            return None, False
        body = node.body
    return node, in_class


def _tree(text: str | None):
    if text is None:
        return None
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None


def _positional_lines(tree, name: str, limit: int) -> set[int]:
    """The lines of calls to `name` (`name(...)`, `x.name(...)`) passing more than `limit` positional arguments and no
    `*args`: the call's own line and its name's line."""
    out: set[int] = set()
    for c in ast.walk(tree) if tree is not None else ():
        if isinstance(c, ast.Call):
            f = c.func
            callee = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if callee == name and len(c.args) > limit and not any(isinstance(a, ast.Starred) for a in c.args):
                out |= {c.lineno, f.end_lineno or c.lineno}
    return out


def _resolve_from(rel: str, module: str | None, level: int) -> str:
    """The absolute module a `from` import in file `rel` names."""
    if level == 0:
        return module or ""
    pkg = rel[:-3].replace("/", ".").split(".")[:-1]
    base = pkg[:len(pkg) - (level - 1)] if level > 1 else pkg
    return ".".join(base + ([module] if module else []))


def _old_import_lines(tree, rel: str, old: set[str], name: str) -> set[int]:
    """Lines importing `name` from a module in `old`: `from <old> import name`, and `<alias>.name` through
    `import <old> [as alias]` or `from <pkg> import <old's last part>`."""
    out, aliases = set(), set()
    for node in ast.walk(tree) if tree is not None else ():
        if isinstance(node, ast.ImportFrom):
            mod = _resolve_from(rel, node.module, node.level)
            for a in node.names:
                if mod in old and a.name == name:
                    out.add(getattr(a, "lineno", None) or node.lineno)
                if f"{mod}.{a.name}" in old:
                    aliases.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            aliases |= {a.asname or a.name for a in node.names if a.name in old}
    for node in ast.walk(tree) if tree is not None and aliases else ():
        if isinstance(node, ast.Attribute) and node.attr == name:
            try:
                recv = ast.unparse(node.value)
            except Exception:
                continue
            if recv in aliases:
                out.add(node.lineno)
    return out


@dataclass
class Decision:
    """`before`'s verdict on a call: `block` (with the message the agent gets instead of the tool's answer)."""
    block: bool
    message: str = ""
    intervention: int | None = None


def infer_target(prompt: str) -> list[str]:
    """The symbols the task names, in the order to try: `path.py:line` from "`path.py` (it is defined at line N)",
    then each backticked name before the first "to `...`"."""
    out = []
    for m in re.finditer(r"`([\w./-]+\.py)`[^`\n]{0,40}?\bline (\d+)", prompt):
        out.append(f"{m.group(1)}:{m.group(2)}")
    head = prompt.split(" to `", 1)[0]
    out += [n for n in re.findall(r"`([A-Za-z_][\w.]*)`", head) if not n.endswith(".py")]
    return out


def _word_counts(text: str, leaf: str) -> dict[int, int]:
    """Occurrences of the word `leaf` per line (strings and comments included)."""
    pat = re.compile(rf"\b{re.escape(leaf)}\b")
    return {i + 1: n for i, ln in enumerate(text.splitlines()) if (n := len(pat.findall(ln)))}


def _name_counts(text: str, leaf: str) -> dict[int, int] | None:
    """`leaf` as a NAME token per line (never in strings or comments); None if the text does not tokenize."""
    out: dict[int, int] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.NAME and tok.string == leaf:
                out[tok.start[0]] = out.get(tok.start[0], 0) + 1
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    return out


def _parses(text: str) -> str | None:
    """None if `text` parses as Python, else the error (line and message)."""
    try:
        ast.parse(text)
        return None
    except SyntaxError as exc:
        return f"line {exc.lineno}: {exc.msg}"


def _line_map(a: list[str], b: list[str]) -> dict[int, int | None]:
    """Each 1-based line of `a` to its line in `b` (None: deleted). A replaced block maps line by line, its extra lines
    to the block's last new line."""
    out: dict[int, int | None] = {}
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        for k in range(i2 - i1):
            if op == "equal":
                out[i1 + k + 1] = j1 + k + 1
            elif op == "replace":
                out[i1 + k + 1] = j1 + min(k, j2 - j1 - 1) + 1
            else:                                         # delete
                out[i1 + k + 1] = None
    return out


def _simulate_edit(text: str, old: str, new: str, occurrence) -> str | None:
    """`edit_file`'s result on `text`, or None if the tool would refuse the edit (not found, ambiguous)."""
    if old == "":
        return None
    starts, i = [], text.find(old)
    while i != -1:
        starts.append(i)
        i = text.find(old, i + 1)
    if not starts:
        return None
    if len(starts) > 1:
        try:
            k = int(occurrence) if occurrence is not None else None
        except (TypeError, ValueError):
            return None
        if k is None or not 1 <= k <= len(starts):
            return None
        at = starts[k - 1]
    else:
        at = starts[0]
    return text[:at] + new + text[at + len(old):]


class Gateway:
    def __init__(self, root: str, prompt: str, refs, is_test=None, exact_fix: bool = False, keep_indent: bool = False,
                 families: bool = False):
        """`root`: the agent's working tree. `refs(symbol)`: PRISM's places of a symbol (see the module doc); it may
        raise when the symbol does not resolve.

        Phase 3b's two switches (AMENDMENTS "Phase 3b"), each off by default, never both on in one arm:
        - `exact_fix` (arm C): after a refused `edit_file`, an `exact_fix` note (`_exact_fix`). It changes no file and
          no decision; it is added after everything else the gateway says.
        - `keep_indent` (arm E): the source lines the gateway quotes keep their own leading whitespace (`_shown`).

        `families` (the breadth study): delete, signature and move tasks get their own places too (module doc)."""
        self.root, self.refs = os.path.abspath(root), refs
        self.families = families
        self.family = family_of(prompt, families)
        self.param = self.limit = self.qual = self.own = None   # signature: the parameter, positional args before it
        self.move = None                                         # move: {"name", "old", "new", "old_modules"}
        self.exact_fix, self.keep_indent = exact_fix, keep_indent
        self.new_name = None                         # arm C: the name the task renames the target to
        self.is_test = is_test or (lambda f: "test" in f.split("/")[-1] or f.startswith(("tests/", "test/")))
        self.interventions: list[dict] = []
        self.calls = 0                               # tool calls seen (the gateway's clock)
        self.refs_asked = False                      # prism_find_references about the target, seen
        self.suggested = 0
        self.since_edit = 0
        self.last_stall = -STALL_CALLS
        self._snap: dict[str, str] = {}              # file -> text before the edit now running
        self._new: list[int] = []                    # interventions created in the current turn
        self.target = self.leaf = None
        self.error = None
        self.lines: dict[str, dict[int, dict]] = {}  # file -> line -> {"kind", "place", "keep", "start"}
        self.gone: list[dict] = []                   # tracked lines deleted by edits
        self._start(prompt)

    # ------------------------------------------------------------------ the start state
    def _start(self, prompt: str):
        if self.family is None and self.families:
            self.error = "not a task family the gateway tracks: only the syntax check applies"
            return
        if self.family is None:
            # what must change is known only for a rename (every use of the target takes the new name); a deletion's
            # fix may leave uses that the task's tests drop. Any other task gets the syntax check only.
            self.error = "not a rename task: only the syntax check applies"
            return
        d, tried = None, infer_target(prompt)
        for sym in tried:
            try:
                d = self.refs(sym)
                break
            except Exception as exc:                 # the next candidate
                self.error = f"{sym}: {type(exc).__name__}: {exc}"[:300]
        if d is None:
            self.error = self.error or "no target named in the task"
            return
        self.error = None
        self.target, self.leaf = d["target"], d["leaf"]
        if self.family in ("signature", "move"):
            self._start_ast(prompt, d)
            return
        if self.exact_fix and self.family == "rename":                           # "... to `NEW`.": the same split `infer_target` uses
            m = re.match(r"([A-Za-z_]\w*)`", prompt.split(" to `", 1)[1]) if " to `" in prompt else None
            self.new_name = m.group(1) if m else None
        places: dict[tuple[str, int], int] = {}
        for (f, line, _col) in d["rows"]:
            places[(f, line)] = places.get((f, line), 0) + 1
        possible = {(f, line) for (f, line, _c) in d.get("possible", {})}
        texts: dict[str, str] = {}
        for f in {f for f, _ in places} | self._files_with_word():
            p = os.path.join(self.root, f)
            if f.endswith(".py") and os.path.isfile(p):
                texts[f] = open(p, encoding="utf8", errors="replace").read()
        for f, text in texts.items():
            words = _word_counts(text, self.leaf)
            names = _name_counts(text, self.leaf) or {}
            for line, n in words.items():
                if (f, line) in places:
                    keep = max(0, n - places[(f, line)])
                    self.lines.setdefault(f, {})[line] = {"kind": "place", "place": places[(f, line)], "keep": keep,
                                                         "start": line}
                elif (f, line) not in possible and names.get(line):
                    self.lines.setdefault(f, {})[line] = {"kind": "lookalike", "keep": names[line], "start": line}
        self.places_total = sum(1 for f in self.lines for e in self.lines[f].values() if e["kind"] == "place")

    def enable_families(self, prompt: str):
        """`families=True` after construction (the harness's switch): a task the default gateway does not track (it
        made no place, `target` is None) is started again as its family. A rename is unchanged."""
        self.families = True
        if self.family is not None:
            return
        self.family, self.error, self.target, self.leaf, self.lines = family_of(prompt, True), None, None, None, {}
        self._start(prompt)

    def _start_ast(self, prompt: str, d: dict):
        """A signature or move task's places (module doc), found by parsing the files."""
        if self.family == "signature":
            self.param = SIGNATURE.match(prompt).group(1)
            self.own = d.get("file")
            text = self._text(self.own) if self.own else None
            q = d["target"]
            for m in sorted(_module_names(self.own), key=len, reverse=True) if self.own else ():
                if q.startswith(m + "."):
                    q = q[len(m) + 1:]
                    break
            self.qual = q
            node, in_class = _find_def(_tree(text), q)
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.target = None
                self.error = f"signature: `{q}` is not a function in {self.own}"
                return
            params = [a.arg for a in node.args.posonlyargs + node.args.args]
            if in_class and not any(isinstance(x, ast.Name) and x.id == "staticmethod" for x in node.decorator_list):
                params = params[1:]
            if self.param not in params:
                self.target = None
                self.error = f"signature: `{self.param}` is not a positional parameter of `{q}`"
                return
            self.limit = params.index(self.param)
            self.lines.setdefault(self.own, {})[node.lineno] = {"kind": "place", "sub": "def", "place": 1, "keep": 0,
                                                                "start": node.lineno}
            possible = {(f, line) for (f, line, _c) in d.get("possible", {})}
            calls = {(f, line) for (f, line, _c) in d["rows"] if (f, line) not in possible and f.endswith(".py")}
            trees: dict = {}
            for f, line in sorted(calls):
                if f not in trees:
                    trees[f] = _positional_lines(_tree(self._text(f)), self.leaf, self.limit)
                if line in trees[f] and line not in self.lines.get(f, {}):
                    self.lines.setdefault(f, {})[line] = {"kind": "place", "sub": "call", "place": 1, "keep": 0,
                                                          "start": line}
        else:
            name, old, new = MOVE.match(prompt).groups()
            self.move = {"name": name, "old": old, "new": new, "old_modules": _module_names(old)}
            node, _ = _find_def(_tree(self._text(old)), name)
            if node is None:
                self.target = None
                self.error = f"move: `{name}` is not defined in {old}"
                return
            self.lines.setdefault(old, {})[node.lineno] = {"kind": "place", "sub": "def", "place": 1, "keep": 0,
                                                           "start": node.lineno}
            for f in sorted(self._files_with_word()):
                if f == new:
                    continue
                for line in sorted(_old_import_lines(_tree(self._text(f)), f, self.move["old_modules"], name)):
                    self.lines.setdefault(f, {}).setdefault(line, {"kind": "place", "sub": "import", "place": 1,
                                                                   "keep": 0, "start": line})
        self.places_total = sum(1 for f in self.lines for e in self.lines[f].values() if e["kind"] == "place") + (
            1 if self.family == "move" else 0)

    def _status_ast(self, f: str, text: str | None) -> dict[int, str]:
        """A signature or move file's tracked lines now: "pending" / "done" (module doc)."""
        entries = self.lines.get(f, {})
        if text is None:
            return {line: "done" for line in entries}
        tree = _tree(text)
        if tree is None:                                  # unparseable: nothing counts as done there yet
            return {line: "pending" for line in entries}
        out = {}
        if self.family == "signature":
            calls = _positional_lines(tree, self.leaf, self.limit)
            for line, e in entries.items():
                if e["sub"] == "def":
                    node, _ = _find_def(tree, self.qual)
                    still = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and self.param in [
                        a.arg for a in node.args.posonlyargs + node.args.args]
                    out[line] = "pending" if still else "done"
                else:
                    out[line] = "pending" if line in calls else "done"
        else:
            imports = _old_import_lines(tree, f, self.move["old_modules"], self.move["name"])
            for line, e in entries.items():
                if e["sub"] == "def":
                    out[line] = "pending" if _find_def(tree, self.move["name"])[0] is not None else "done"
                else:
                    out[line] = "pending" if line in imports else "done"
        return out

    def _new_defined(self) -> bool:
        """Move: `N` is defined at the top level of the new module."""
        return _find_def(_tree(self._text(self.move["new"])), self.move["name"])[0] is not None

    def _phrase(self, n: int) -> str:
        """`n` of the task's places, as the worklist and the done block say it (a rename: as before)."""
        if self.family == "signature":
            return (f"{n} of {self.places_total} place(s) still take or pass `{self.param}` positionally for "
                    f"`{self.target}`")
        if self.family == "move":
            return (f"{n} of {self.places_total} place(s) still define or import `{self.move['name']}` from "
                    f"`{self.move['old']}`, or the new module `{self.move['new']}` does not define it yet")
        if self.family == "delete":
            return f"{n} of {self.places_total} place(s) still use `{self.leaf}` for `{self.target}`, which is removed"
        return f"{n} of {self.places_total} place(s) still use `{self.leaf}` for `{self.target}`"

    def _files_with_word(self) -> set[str]:
        r = subprocess.run(["git", "-C", self.root, "grep", "-l", "-w", "-I", "--untracked", "-e", self.leaf, "--",
                            "*.py"], capture_output=True, text=True, errors="replace")
        return {ln for ln in r.stdout.splitlines() if ln and "/.r3-" not in ln and not ln.startswith(".r3-")}

    # ------------------------------------------------------------------ state
    def _text(self, f: str) -> str | None:
        p = os.path.join(self.root, f)
        return open(p, encoding="utf8", errors="replace").read() if os.path.isfile(p) else None

    def _status(self, f: str, text: str | None = None) -> dict[int, str]:
        """Each tracked line of `f` now: "pending" / "done" (places), "intact" / "changed" (look-alikes)."""
        text = self._text(f) if text is None else text
        if self.family in ("signature", "move"):
            return self._status_ast(f, text)
        if text is None:
            return {line: ("done" if e["kind"] == "place" else "changed") for line, e in self.lines.get(f, {}).items()}
        words = _word_counts(text, self.leaf)
        names = _name_counts(text, self.leaf)
        out = {}
        for line, e in self.lines.get(f, {}).items():
            if e["kind"] == "place":
                out[line] = "pending" if words.get(line, 0) > e["keep"] else "done"
            else:
                n = (names if names is not None else words).get(line, 0)
                out[line] = "changed" if n < e["keep"] else "intact"
        return out

    def pending(self) -> list[tuple[str, int]]:
        out = sorted((f, line) for f in self.lines for line, s in self._status(f).items() if s == "pending")
        if self.family == "move" and self.target is not None and not self._new_defined():
            out.append((self.move["new"], 0))             # line 0: "not defined there yet"
        return out

    def changed(self) -> list[tuple[str, int]]:
        out = [(f, line) for f in self.lines for line, s in self._status(f).items() if s == "changed"]
        return sorted(out + [(g["file"], g["was"]) for g in self.gone if g["kind"] == "lookalike"])

    def _remap(self, f: str, before: str, after: str):
        m = _line_map(before.splitlines(), after.splitlines())
        moved = {}
        for line, e in self.lines.get(f, {}).items():
            new = m.get(line, line)
            if new is None:
                self.gone.append({"file": f, "was": line, "kind": e["kind"], "start": e["start"]})
            elif new in moved:                       # two tracked lines merged into one: keep the place
                if e["kind"] == "place":
                    moved[new] = e
            else:
                moved[new] = e
        self.lines[f] = moved

    def _shown(self, raw: str, cap: int = 100) -> str:
        """A quoted source line: stripped and cut at `cap` (as before), with its own leading whitespace put back in
        arm E (`keep_indent`)."""
        text = raw.strip()[:cap]
        if self.keep_indent and text:
            return raw[:len(raw) - len(raw.lstrip())] + text
        return text

    def _listing(self, items: list[tuple[str, int]]) -> str:
        out = []
        for f, line in items[:MAX_LISTED]:
            if line == 0:                                 # move: the new module, which must define the function
                out.append(f"  {f}: `{self.move['name']}` is not defined there yet")
                continue
            text = (self._text(f) or "").splitlines()
            src = self._shown(text[line - 1]) if 0 < line <= len(text) else ""
            out.append(f"  {f}:{line}: {src}")
        if len(items) > MAX_LISTED:
            out.append(f"  ... and {len(items) - MAX_LISTED} more")
        return "\n".join(out)

    # ------------------------------------------------------------------ interventions
    def _add(self, turn: int, kind: str, message: str, blocking: bool = False, key: str | None = None,
             places=(), lookalikes=()) -> int:
        self.interventions.append({
            "id": len(self.interventions), "turn": turn, "call": self.calls, "type": kind, "blocking": blocking,
            "key": key, "places": [f"{f}:{ln}" for f, ln in places], "lookalikes": [f"{f}:{ln}" for f, ln in lookalikes],
            "_track": [(f, ln) for f, ln in places], "_track_look": [(f, ln) for f, ln in lookalikes],
            "pending_at": len(self.pending()), "outcome": None, "resolved_call": None, "repeats": 0,
            "chars": len(message)})
        self._new.append(len(self.interventions) - 1)
        return len(self.interventions) - 1

    def _resolve(self, i: int, outcome: str):
        iv = self.interventions[i]
        if iv["outcome"] is None:
            iv["outcome"], iv["resolved_call"] = outcome, self.calls

    def _tick(self, tool: str, args: dict, applied_edit: bool, pending_now: int, confirmed_key: str | None):
        """Resolve open interventions against the call just made."""
        for iv in self.interventions:
            if iv["outcome"] is not None or iv["call"] == self.calls:
                continue
            age = self.calls - iv["call"]
            t = iv["type"]
            if iv["blocking"]:
                if confirmed_key is not None and confirmed_key == iv["key"]:
                    self._resolve(iv["id"], "ignored")
                elif age >= WINDOW:
                    self._resolve(iv["id"], "prevented")
            elif t in ("suggest_tool", "wrong_target"):
                if self.refs_asked and tool == "prism_find_references":
                    self._resolve(iv["id"], "followed")
                elif age >= WINDOW:
                    self._resolve(iv["id"], "ignored")
            elif t in ("worklist", "stall"):
                if applied_edit and pending_now < iv["pending_at"]:
                    self._resolve(iv["id"], "followed")
                elif iv["pending_at"] == 0 and tool == "done":
                    self._resolve(iv["id"], "followed")
                elif age >= WINDOW:
                    self._resolve(iv["id"], "ignored")
            elif t == "search_augment":
                if set(iv["_track_look"]) & set(self.changed()):
                    self._resolve(iv["id"], "ignored")
            elif t == "exact_fix":                    # arm C only: did the model send the exact text?
                if applied_edit and tool == "edit_file" and str(args.get("old", "")).rstrip("\n") == iv["fix_old"] \
                        and str(args.get("new", "")).rstrip("\n") == iv["fix_new"]:
                    iv["response"] = "exact"
                    self._resolve(iv["id"], "followed")
                elif applied_edit and self._entry_done(iv):
                    iv["response"] = "different"
                    self._resolve(iv["id"], "followed")
                elif age >= WINDOW:
                    iv["response"] = "none"
                    self._resolve(iv["id"], "ignored")

    @staticmethod
    def _key(tool: str, args: dict) -> str:
        import json
        return tool + "|" + json.dumps({k: v for k, v in args.items() if k != "confirm"}, sort_keys=True, default=str)

    def new_interventions(self) -> list[dict]:
        """The interventions made since the last call of this method (for the turn log), without internal fields."""
        out = [{k: v for k, v in self.interventions[i].items() if not k.startswith("_")} for i in self._new]
        self._new = []
        return out

    # ------------------------------------------------------------------ the two hooks
    def before(self, turn: int, tool: str, args: dict) -> Decision:
        """The verdict on a call about to run. `args` may carry `confirm: true` (an override of an earlier block)."""
        self.calls += 1
        confirm = args.get("confirm") is True
        key = self._key(tool, args)
        self._confirmed = key if confirm else None
        self._snap = {}
        if self.target is None and tool != "edit_file":
            return Decision(False)
        open_block = next((iv for iv in self.interventions if iv["blocking"] and iv["outcome"] is None
                           and iv["key"] == key), None)
        if tool == "edit_file":
            f = str(args.get("path", "")).replace("\\", "/").removeprefix("./")
            before = self._text(f)
            if before is not None:
                self._snap[f] = before
            if confirm or before is None or not f.endswith(".py"):
                return Decision(False)
            after = _simulate_edit(before, str(args.get("old", "")), str(args.get("new", "")), args.get("occurrence"))
            if after is None:
                return Decision(False)
            err = _parses(after)
            if err is not None and _parses(before) is None:
                msg = (f"{PREFIX}not applied: this edit would leave {f} unparseable ({err}). A block (an `if`/`else`, "
                       f"a `def`, a `class`) left with no statements needs a body, e.g. `pass`; or remove the block's "
                       f"header too. If you are sure, send the same call again with \"confirm\": true.")
                return self._block(turn, "block_syntax", msg, key, open_block)
            if self.target is not None and f in self.lines:
                m = _line_map(before.splitlines(), after.splitlines())
                names = _name_counts(after, self.leaf)
                names = names if names is not None else _word_counts(after, self.leaf)
                hit = []
                for line, e in self.lines[f].items():
                    if e["kind"] != "lookalike":
                        continue
                    new = m.get(line, line)
                    if new is None or names.get(new, 0) < e["keep"]:
                        hit.append((f, line))
                if hit:
                    msg = (f"{PREFIX}not applied: this edit changes `{self.leaf}` on line(s) where it belongs to "
                           f"another object, not to `{self.target}`:\n{self._listing(hit)}\nLeave those uses "
                           f"unchanged and edit only the uses of `{self.target}`. If you are sure, send the same call "
                           f"again with \"confirm\": true.")
                    return self._block(turn, "block_lookalike", msg, key, open_block, lookalikes=hit)
            return Decision(False)
        if tool == "done" and not confirm:
            pend, chg = self.pending(), self.changed()
            if pend or chg:
                parts = []
                if pend and self.family != "rename":
                    parts.append(f"{self._phrase(len(pend))}:\n{self._listing(pend)}")
                elif pend:
                    parts.append(f"{len(pend)} of {self.places_total} place(s) still use `{self.leaf}` where it means "
                                 f"`{self.target}`:\n{self._listing(pend)}")
                if chg:
                    parts.append(f"{len(chg)} use(s) of `{self.leaf}` that belong to other objects were changed:\n"
                                 f"{self._listing(chg)}")
                msg = (f"{PREFIX}not finished yet. " + "\n".join(parts) + "\nIf you are sure, call done again with "
                       f"\"confirm\": true.")
                return self._block(turn, "block_done", msg, key, open_block, places=pend, lookalikes=chg)
        return Decision(False)

    def _block(self, turn, kind, msg, key, open_block, places=(), lookalikes=()) -> Decision:
        if open_block is not None:                    # the same call blocked again: one intervention
            open_block["repeats"] += 1
            return Decision(True, msg, open_block["id"])
        return Decision(True, msg, self._add(turn, kind, msg, True, key, places, lookalikes))

    def after(self, turn: int, tool: str, args: dict, output: str, blocked: bool = False) -> str:
        """What to add to the answer the agent gets (empty: nothing). Called after every call, blocked ones too."""
        applied = False
        extra: list[str] = []
        if self.target is None:
            self._tick(tool, args, False, 0, self._confirmed)
            return ""
        if not blocked and tool == "edit_file" and output.startswith(("edited", "created")):
            f = str(args.get("path", "")).replace("\\", "/").removeprefix("./")
            now = self._text(f)
            if f in self.lines and f in self._snap and now is not None:
                self._remap(f, self._snap[f], now)
            applied = True
            self.since_edit = 0
        elif not blocked:
            self.since_edit += 1
        if not blocked and tool in QUERY_TOOLS:
            sym = args.get("symbol")
            q = None
            if isinstance(sym, str):
                try:
                    q = self.refs(sym)["target"]
                except Exception:
                    q = None
            if q == self.target and tool == "prism_find_references":
                self.refs_asked = True
            elif q is not None and q != self.target and q.rsplit(".", 1)[-1] == self.leaf:
                msg = (f"{PREFIX}`{q}` is another object named `{self.leaf}`, not the task's target `{self.target}`. "
                       f"Its uses are look-alikes: leave them unchanged.")
                self._add(turn, "wrong_target", msg)
                extra.append(msg)
        pend = self.pending()
        self._tick(tool, args, applied, len(pend), self._confirmed)
        if blocked:
            return ""
        if applied and str(args.get("path", "")).endswith(".py"):
            chg = self.changed()
            msg = PREFIX + self._phrase(len(pend))
            msg += (":\n" + self._listing(pend)) if pend else "."
            if chg:
                msg += (f"\n{len(chg)} use(s) of `{self.leaf}` that belong to other objects were changed (restore "
                        f"them):\n{self._listing(chg)}")
            self._add(turn, "worklist", msg, places=pend, lookalikes=chg)
            extra.append(msg)
        if tool in SEARCH_TOOLS and not output.startswith("error"):
            pattern = str(args.get("pattern") or args.get("query") or "")
            hits = {(f, int(n)) for f, n in _HIT.findall(output)}
            places = sorted(h for h in hits if self.lines.get(h[0], {}).get(h[1], {}).get("kind") == "place")
            looks = sorted(h for h in hits if self.lines.get(h[0], {}).get(h[1], {}).get("kind") == "lookalike")
            if places or looks:
                msg = (f"{PREFIX}of these hits, {len(places)} are uses of `{self.target}` and {len(looks)} belong to "
                       f"other objects named `{self.leaf}`" + (f" (leave them unchanged):\n{self._listing(looks)}"
                                                               if looks else "."))
                self._add(turn, "search_augment", msg, places=places, lookalikes=looks)
                extra.append(msg)
            if not self.refs_asked and self.suggested < MAX_SUGGEST and self.leaf in pattern:
                extra.append(self._suggest(turn))
        if self.calls == 1 and not self.refs_asked and self.suggested < MAX_SUGGEST and not extra:
            extra.append(self._suggest(turn))
        if pend and not applied and (output.startswith("not answered again") or (
                self.since_edit >= STALL_CALLS and self.calls - self.last_stall >= STALL_CALLS)):
            self.last_stall = self.calls
            what = (f"{len(pend)} place(s) still use `{self.leaf}` for `{self.target}`" if self.family == "rename" else
                    self._phrase(len(pend)))
            msg = (f"{PREFIX}no change has been made for {self.since_edit} call(s); {what}. Next:\n"
                   f"{self._listing(pend[:5])}")
            self._add(turn, "stall", msg, places=pend[:5])
            extra.append(msg)
        if self.exact_fix and self.family == "rename" and tool == "edit_file" and \
                output.startswith(("error", "not answered again")):
            note = self._exact_fix(turn, args, output)        # arm C: after every other decision, changing none
            if note:
                extra.append(note)
        return ("\n\n" + "\n\n".join(extra)) if extra else ""

    # ------------------------------------------------------------------ arm C: the exact replacement
    def _exact_fix(self, turn: int, args: dict, output: str) -> str | None:
        """Arm C (Phase 3b): after an `edit_file` the tool refused (its error, or the loop breaker's refusal of a
        repeated call), the exact `old` and `new` for the nearest pending place in that file. Guidance only: no file
        is changed and no other decision depends on it. None (no note) when no place qualifies unambiguously:
        - a place qualifies when every use of the name on its line is the target's (no look-alike use to keep), its
          whole line occurs exactly once in the file (so `old` matches once), and the task's new name is known;
        - the reference point is the line the tool's error names ("at line N", "from line N"); for a loop refusal, the
          line of the last note given for the same call; with neither, a note only if one place qualifies;
        - two qualifying places at the same distance from the reference point: no note (ambiguous)."""
        f = str(args.get("path", "")).replace("\\", "/").removeprefix("./")
        key = self._key("edit_file", args)
        text = self._text(f)
        if self.new_name is None or text is None or f not in self.lines:
            return None
        lines = text.splitlines()
        status = self._status(f, text)
        cands = []
        for line, s in sorted(status.items()):
            e = self.lines[f][line]
            if s != "pending" or e["kind"] != "place" or e["keep"] != 0 or not 0 < line <= len(lines):
                continue
            raw = lines[line - 1]
            new = re.sub(rf"\b{re.escape(self.leaf)}\b", self.new_name, raw)
            if new != raw and text.count(raw) == 1:
                cands.append((line, raw, new))
        if not cands:
            return None
        m = re.search(r"\b(?:at|from) line (\d+)", output)
        ref = int(m.group(1)) if m else None
        if ref is None:
            prev = next((iv for iv in reversed(self.interventions) if iv["type"] == "exact_fix" and iv["key"] == key),
                        None)
            ref = int(prev["places"][0].rsplit(":", 1)[1]) if prev else None
        if ref is None:
            if len(cands) != 1:
                return None
            pick = cands[0]
        else:
            cands.sort(key=lambda c: (abs(c[0] - ref), c[0]))
            if len(cands) > 1 and abs(cands[0][0] - ref) == abs(cands[1][0] - ref):
                return None
            pick = cands[0]
        line, raw, new = pick
        msg = (f"{PREFIX}the exact text for the nearest place that still uses `{self.leaf}` for `{self.target}`, "
               f"{f}:{line}. To change it, call edit_file with exactly these as `old` and `new` (each is the whole "
               f"line, its indentation included; the quotes are JSON's, not part of the text):\n"
               f"old: {json.dumps(raw, ensure_ascii=False)}\nnew: {json.dumps(new, ensure_ascii=False)}")
        i = self._add(turn, "exact_fix", msg, key=key, places=[(f, line)])
        self.interventions[i].update({
            "warning_type": "actionable_correction", "refusal": output[:300],
            "refused_old": str(args.get("old", ""))[:300], "fix_old": raw, "fix_new": new, "response": None,
            "_entry": self.lines[f][line], "_file": f})
        return msg

    def _entry_done(self, iv: dict) -> bool:
        """Whether the place an `exact_fix` note named is done now (followed through edits by its tracked entry)."""
        f = iv["_file"]
        for line, e in self.lines.get(f, {}).items():
            if e is iv["_entry"]:
                return self._status(f).get(line) == "done"
        return True                                   # the line was deleted

    def _suggest(self, turn: int) -> str:
        self.suggested += 1
        files = len({f for f in self.lines for e in self.lines[f].values() if e["kind"] == "place"})
        looks = sum(1 for f in self.lines for e in self.lines[f].values() if e["kind"] == "lookalike")
        if self.family in ("signature", "move"):
            msg = (f"{PREFIX}this task changes {self.places_total} place(s) in {files} file(s) for `{self.target}`. "
                   f"prism_find_references(\"{self.target}\") lists every one of its uses, each at its line.")
        else:
            msg = (f"{PREFIX}`{self.target}` is used at {self.places_total} place(s) in {files} file(s); {looks} other "
                   f"line(s) use the name `{self.leaf}` for other objects. prism_find_references(\"{self.target}\") "
                   f"lists every one of its uses, each at its line.")
        self._add(turn, "suggest_tool", msg)
        return msg

    # ------------------------------------------------------------------ the end
    def finish(self) -> dict:
        """The gateway's record of the run: every intervention with its outcome, and the places' final state."""
        pend, chg = set(self.pending()), set(self.changed())
        for iv in self.interventions:
            if iv["outcome"] is None:
                iv["outcome"] = "prevented" if iv["blocking"] else (
                    "followed" if iv["type"] == "search_augment" else "ignored")
            if iv["type"] == "exact_fix" and iv.get("response") is None:
                iv["response"] = "none"
            iv["places_ok"] = (not (set(iv["_track"]) & pend) and not (set(iv["_track_look"]) & chg)) \
                if (iv["_track"] or iv["_track_look"]) else None
        ivs = [{k: v for k, v in iv.items() if not k.startswith("_")} for iv in self.interventions]
        count = lambda pred: sum(1 for iv in ivs if pred(iv))
        return {
            "target": self.target, "error": self.error, "places": getattr(self, "places_total", 0),
            "lookalikes": sum(1 for f in self.lines for e in self.lines[f].values() if e["kind"] == "lookalike"),
            "pending_at_end": len(pend), "lookalikes_changed_at_end": len(chg),
            "interventions": len(ivs),
            "prevented_tool_calls": count(lambda iv: iv["type"] == "block_done" and iv["outcome"] == "prevented"),
            "prevented_edits": count(lambda iv: iv["type"] in ("block_lookalike", "block_syntax")
                                     and iv["outcome"] == "prevented"),
            "ignored": count(lambda iv: iv["outcome"] == "ignored"),
            "followed": count(lambda iv: iv["outcome"] == "followed"),
            "by_type": {t: count(lambda iv, t=t: iv["type"] == t) for t in sorted({iv["type"] for iv in ivs})},
            "log": ivs,
        }
