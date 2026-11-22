"""BASELINE_LSP's IDE-agent tools besides `find_references` (Phase 3, the user's decision 2026-11-22: the baseline
should have what Claude Code, Cursor and Copilot have: semantic search, structural search and a shell).

All three read the working tree as it is now (edits included), as an IDE agent's tools do:

- **`semantic_search(query)`**: search by meaning, not exact text. A BM25 index over the repository's code chunks:
  every Python function, method and class body (`ast`), module-level code between them, and other text files in
  40-line windows. Identifiers are split (`get_user_name` and `getUserName` give `get`, `user`, `name`). The top
  `SEM_TOP` chunks, each as `path:lo-hi` with its name and up to `SEM_LINES` lines. (A local embedding model was
  ruled out: it would be downloaded and run on CPU in every run, beside the GPU model. BM25 is what Cursor and
  Copilot fall back to.)
- **`ast_search(pattern, path)`**: structural search with ast-grep (pinned in the cell), the tool IDE agents call for
  code patterns: `$X.close()` (any receiver), `$F($A, $B)` (two arguments), `def $NAME($$$ARGS): $$$BODY`,
  `class $C($BASE)`. Python files only. Each match as `path:line:col: <the line>`, at most `AST_MAX` matches.
- **`run_shell(command)`**: read-only shell commands in the repository: an allow-list (`ls`, `cat`, `head`, `tail`,
  `wc`, `find`, `tree`, `rg`, `grep`, `file`, `stat`, `du`, `sort`, `uniq`, `cut`, `awk`, `git` with `log`, `show`,
  `diff`, `status`, `blame`, `grep`, `ls-files`, `branch`, `rev-parse`), joined by `|`, `&&`, `;` only. Refused:
  redirection (`>`, `<`), command substitution, background jobs, `find -delete/-exec/-execdir/-ok/-fprint*`,
  `awk` with `system`, `>` or `|` inside its program, and `sort -o`. Each runs with a 20 s limit; output capped at
  `SHELL_MAX_LINES` lines. Writing files stays `edit_file`'s job.
"""
from __future__ import annotations

import ast
import json
import math
import os
import re
import shlex
import subprocess
from collections import Counter

SEM_TOP, SEM_LINES, WINDOW = 10, 12, 40
AST_MAX = 100
SHELL_MAX_LINES, SHELL_TIMEOUT = 200, 20
_TEXT_EXT = {".py", ".pyi", ".md", ".rst", ".txt", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".json", ".html", ".js",
             ".ts", ".tsx", ".jsx", ".css", ".sh"}
_STOP = {"self", "cls", "the", "and", "for", "return", "def", "class", "import", "from", "none", "true", "false", "if",
         "else", "elif", "in", "is", "not", "or", "of", "to", "a", "an", "with", "as", "try", "except", "raise", "pass"}


class BaselineToolError(Exception):
    pass


def _words(text: str) -> list[str]:
    out = []
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9]*", text):
        for part in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+", tok) or [tok]:
            w = part.lower()
            if len(w) > 1 and w not in _STOP:
                out.append(w)
    return out


class BaselineTools:
    def __init__(self, work: str):
        self.work = os.path.abspath(work)
        self._chunks: dict[str, list] = {}        # file -> [(lo, hi, name, words Counter)], by mtime
        self._mtimes: dict[str, float] = {}

    # ------------------------------------------------------------------ files
    def _files(self) -> list[str]:
        r = subprocess.run(["git", "-C", self.work, "ls-files", "--cached", "--others", "--exclude-standard"],
                           capture_output=True, text=True)
        return sorted(f for f in r.stdout.split() if os.path.splitext(f)[1] in _TEXT_EXT and not f.startswith(".r3-")
                      and "/.r3-" not in f and os.path.isfile(os.path.join(self.work, f)))

    def _read(self, rel: str) -> list[str]:
        with open(os.path.join(self.work, rel), encoding="utf8", errors="replace") as fh:
            return fh.read().splitlines()

    # ------------------------------------------------------------------ semantic search
    def _chunk(self, rel: str) -> list:
        lines = self._read(rel)
        spans = []
        if rel.endswith(".py"):
            try:
                tree = ast.parse("\n".join(lines))
            except (SyntaxError, ValueError, RecursionError):
                tree = None
            if tree is not None:
                def walk(body, prefix):
                    for n in body:
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            lo = min([n.lineno] + [d.lineno for d in n.decorator_list])
                            name = prefix + n.name
                            if isinstance(n, ast.ClassDef):
                                first = n.body[0].lineno - 1 if n.body else n.end_lineno
                                spans.append((lo, max(lo, first), name))
                                walk(n.body, name + ".")
                            else:
                                spans.append((lo, n.end_lineno, name))
                walk(tree.body, "")
        if not spans or not rel.endswith(".py"):
            spans = [(i + 1, min(len(lines), i + WINDOW), "") for i in range(0, max(len(lines), 1), WINDOW)]
        else:                                      # module-level code between the definitions
            covered = set()
            for lo, hi, _n in spans:
                covered.update(range(lo, hi + 1))
            run = []
            for i in range(1, len(lines) + 1):
                if i not in covered and lines[i - 1].strip():
                    run.append(i)
                if run and (i in covered or i == len(lines) or len(run) >= WINDOW):
                    spans.append((run[0], run[-1], "<module>"))
                    run = []
        return [(lo, hi, name, Counter(_words(name + " " + "\n".join(lines[lo - 1:hi]))))
                for lo, hi, name in spans]

    def _index(self) -> list:
        files = self._files()
        for f in files:
            m = os.path.getmtime(os.path.join(self.work, f))
            if self._mtimes.get(f) != m:
                self._chunks[f], self._mtimes[f] = self._chunk(f), m
        for f in set(self._chunks) - set(files):
            self._chunks.pop(f), self._mtimes.pop(f)
        return [(f, *c) for f in files for c in self._chunks[f]]

    def semantic_search(self, query: str) -> str:
        q = _words(query)
        if not q:
            raise BaselineToolError("the query has no searchable words")
        chunks = self._index()
        n = len(chunks)
        avg = sum(sum(c[4].values()) for c in chunks) / max(n, 1)
        df = Counter(w for c in chunks for w in set(c[4]) if w in q)
        k1, b = 1.5, 0.75
        scored = []
        for f, lo, hi, name, words in chunks:
            length = sum(words.values())
            s = 0.0
            for w in set(q):
                tf = words.get(w, 0)
                if tf:
                    idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                    s += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / max(avg, 1)))
            if s > 0:
                scored.append((-s, f, lo, hi, name))
        if not scored:
            return f"No code matches {query!r}."
        out = []
        for neg, f, lo, hi, name in sorted(scored)[:SEM_TOP]:
            lines = self._read(f)
            body = "\n".join(f"{i}\t{lines[i - 1]}" for i in range(lo, min(hi, lo + SEM_LINES - 1) + 1))
            more = f"\n... ({hi - lo + 1 - SEM_LINES} more lines)" if hi - lo + 1 > SEM_LINES else ""
            out.append(f"{f}:{lo}-{hi}" + (f" `{name}`" if name else "") + f" (score {-neg:.1f})\n{body}{more}")
        return f"{len(scored)} matching chunks; the top {min(SEM_TOP, len(scored))}:\n\n" + "\n\n".join(out)

    # ------------------------------------------------------------------ structural search
    def ast_search(self, pattern: str, path: str = ".") -> str:
        target = os.path.normpath(os.path.join(self.work, path))
        if not (target == self.work or target.startswith(self.work + os.sep)) or not os.path.exists(target):
            raise BaselineToolError(f"no such path in the repository: {path}")
        try:
            r = subprocess.run(["ast-grep", "run", "--pattern", pattern, "--lang", "python", "--json=stream",
                                os.path.relpath(target, self.work)], cwd=self.work, capture_output=True, text=True,
                               timeout=60)
        except FileNotFoundError as exc:
            raise BaselineToolError("ast-grep is not installed") from exc
        except subprocess.TimeoutExpired as exc:
            raise BaselineToolError("ast_search timed out (60 s); narrow `path`") from exc
        if r.returncode not in (0, 1):
            raise BaselineToolError((r.stderr or "ast-grep failed").strip().splitlines()[0][:300])
        hits = []
        for ln in r.stdout.splitlines():
            try:
                m = json.loads(ln)
            except json.JSONDecodeError:
                continue
            f = m["file"]
            if f.startswith(".r3-") or "/.r3-" in f:
                continue
            st = m["range"]["start"]
            first = (m.get("lines") or "").splitlines()[0].strip() if m.get("lines") else ""
            hits.append((f, st["line"] + 1, st["column"] + 1, first))
        if not hits:
            return f"No match for the pattern {pattern!r}."
        hits.sort()
        body = [f"{f}:{line}:{col}: {text}" for f, line, col, text in hits[:AST_MAX]]
        more = [f"... ({len(hits) - AST_MAX} more)"] if len(hits) > AST_MAX else []
        return f"{len(hits)} match(es) for {pattern!r}:\n" + "\n".join(body + more)

    # ------------------------------------------------------------------ the shell
    ALLOWED = {"ls", "cat", "head", "tail", "wc", "find", "tree", "rg", "grep", "file", "stat", "du", "sort", "uniq",
               "cut", "awk", "git", "echo", "pwd", "basename", "dirname", "nl", "tr", "diff"}
    GIT_ALLOWED = {"log", "show", "diff", "status", "blame", "grep", "ls-files", "branch", "rev-parse", "shortlog"}

    def _check(self, argv: list[str]) -> None:
        cmd = argv[0]
        if cmd not in self.ALLOWED:
            raise BaselineToolError(f"`{cmd}` is not allowed: read-only commands only "
                                    f"({', '.join(sorted(self.ALLOWED))}); use edit_file to change files")
        if cmd == "git":
            sub = next((a for a in argv[1:] if not a.startswith("-")), None)
            if sub not in self.GIT_ALLOWED or any(a in ("--output", "-o") or a.startswith("--output=") for a in argv):
                raise BaselineToolError(f"`git {sub}` is not allowed (read-only: {', '.join(sorted(self.GIT_ALLOWED))})")
        if cmd == "git" and any(a in ("-c", "--exec-path", "-O") or a.startswith(("--config-env", "--exec-path=",
                                                                              "--open-files-in-pager", "--ext-diff",
                                                                              "-c="))
                                for a in argv[1:]):
            raise BaselineToolError("git options that set configuration or run programs are not allowed")
        if cmd == "rg" and any(a.startswith("--pre") for a in argv):
            raise BaselineToolError("rg --pre runs a program; not allowed")
        if cmd == "tree" and any(a == "-o" for a in argv):
            raise BaselineToolError("tree may not write files")
        if cmd == "find" and any(a in ("-delete", "-exec", "-execdir", "-ok", "-okdir") or a.startswith("-fprint")
                                 or a == "-fls" for a in argv):
            raise BaselineToolError("find may not delete, run commands or write files")
        if cmd == "sort" and any(a == "-o" or a.startswith("--output") for a in argv):
            raise BaselineToolError("sort may not write files")
        if cmd == "awk" and any(re.search(r"system|getline|>|\|", a) for a in argv[1:]):
            raise BaselineToolError("awk may not run commands or write files")

    def run_shell(self, command: str) -> str:
        if re.search(r"[<>`]|\$\(|&(?!&)|\n", command.replace("&&", "")):
            raise BaselineToolError("no redirection, substitution, background jobs or newlines; read-only commands "
                                    "joined by |, && or ;")
        try:
            lex = shlex.shlex(command, posix=True, punctuation_chars="|;&")
            lex.whitespace_split = True
            tokens = list(lex)
        except ValueError as exc:
            raise BaselineToolError(f"cannot parse the command: {exc}") from exc
        argv: list[str] = []
        for t in tokens + [";"]:
            if t in ("|", ";", "&&", "||"):
                if not argv:
                    raise BaselineToolError("an empty command")
                self._check(argv)
                argv = []
            else:
                argv.append(t)
        env = dict(os.environ, GIT_PAGER="cat", PAGER="cat", LC_ALL="C.UTF-8")
        try:
            r = subprocess.run(["bash", "-c", command], cwd=self.work, capture_output=True, text=True,
                               errors="replace", timeout=SHELL_TIMEOUT, env=env, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return f"(stopped after {SHELL_TIMEOUT} s)"
        lines = (r.stdout + (("\n" + r.stderr) if r.stderr.strip() else "")).rstrip("\n").splitlines()
        more = [f"... ({len(lines) - SHELL_MAX_LINES} more lines)"] if len(lines) > SHELL_MAX_LINES else []
        head = "" if r.returncode == 0 else f"(exit {r.returncode})\n"
        return head + ("\n".join(lines[:SHELL_MAX_LINES] + more) or "(no output)")
