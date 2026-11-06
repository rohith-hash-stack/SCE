"""R3's shared tools, identical in both versions (pre-registration, "The loop and its parity"):
`read_file`, `list_dir`, `grep`, `edit_file`, `run_tests`, `done`.

    env = make_env(clone, rev, workdir, cache)     # the JS/TS (`jsbuild.Env`) or Python (`pybuild.PyEnv`) builder
    env.setup()
    tools = SharedTools(workdir, env, visible_tests, tokenizer_path)
    text = tools.call("read_file", path="src/a.py", start=1, end=80)

- **The disk tools** (`read_file`, `list_dir`, `grep`) read the working tree as it is now, and are never gated
  (amendment R3-5).
- **`edit_file`** replaces one exact, unique span in a file, or creates a new file when `old` is empty and the file
  does not exist. It never runs code.
- **`run_tests`** runs the given test files (default: the task's visible tests) through the revision's builder. It
  reports per-file counts and the failing tests' names and output. It never reveals hidden tests: they are not in the
  working tree during the run, and are added only for scoring.
- **`done`** ends the run.

Every output is cut at the per-call cap: 4,000 Qwen2.5 tokens, the same cap as the index-backed tools.
"""
from __future__ import annotations

import os
import re
import subprocess

CAP_TOKENS = 4000
GREP_MAX_LINES = 200
FAIL_TAIL_CHARS = 1500


def language_of(workdir: str) -> str:
    if os.path.exists(os.path.join(workdir, "package.json")) and not os.path.exists(os.path.join(workdir, "pyproject.toml")):
        return "js"
    if os.path.exists(os.path.join(workdir, "pyproject.toml")) or os.path.exists(os.path.join(workdir, "setup.py")):
        return "python"
    return "js" if os.path.exists(os.path.join(workdir, "package.json")) else "python"


def make_env(clone: str, rev: str, workdir: str, cache: str):
    """The builder for this repository's language, with the shared interface: setup(), test_files(), run_test()."""
    if language_of(workdir) == "js":
        from harness.experiments.r3.jsbuild import Env
        return Env(clone, rev, workdir, cache)
    from harness.experiments.r3.pybuild import PyEnv
    return PyEnv(clone, rev, workdir, cache)


class ToolCallError(Exception):
    pass


def whitespace_matches(text: str, old: str) -> list[int]:
    """1-based line numbers where `old` matches `text` line by line once leading and trailing whitespace is ignored.

    The first line of `old` may end a file line and the last may begin one, as an exact span may.
    """
    want = [ln.strip() for ln in old.strip("\n").split("\n")]
    if not any(want):
        return []
    have = [ln.strip() for ln in text.split("\n")]
    k, hits = len(want), []
    for i in range(len(have) - k + 1):
        win = have[i:i + k]
        if k == 1:
            ok = want[0] in win[0]
        else:
            ok = (win[0].endswith(want[0]) and win[-1].startswith(want[-1]) and win[1:-1] == want[1:-1])
        if ok:
            hits.append(i + 1)
    return hits


class SharedTools:
    NAMES = ("read_file", "list_dir", "grep", "edit_file", "run_tests", "done")

    def __init__(self, workdir: str, env, visible_tests: list[str], tokenizer_path: str | None = None):
        self.work, self.env, self.visible = os.path.abspath(workdir), env, list(visible_tests)
        self.done_summary = None
        self._tok = None
        if tokenizer_path:
            from tokenizers import Tokenizer
            self._tok = Tokenizer.from_file(tokenizer_path)

    # ------------------------------------------------------------------ dispatch
    def call(self, tool: str, **args) -> str:
        if tool not in self.NAMES:
            return f"error: unknown tool {tool!r}"
        try:
            return self._cap(getattr(self, "_" + tool)(**args))
        except ToolCallError as exc:
            return f"error: {exc}"
        except TypeError as exc:
            return f"error: bad arguments for {tool}: {exc}"

    def _path(self, rel: str) -> str:
        path = os.path.realpath(os.path.join(self.work, rel))
        if not (path == self.work or path.startswith(self.work + os.sep)):
            raise ToolCallError(f"{rel!r} is outside the repository")
        if os.sep + ".git" + os.sep in path + os.sep or os.sep + ".r3-" in path:
            raise ToolCallError(f"{rel!r} is not part of the project")
        return path

    def _cap(self, text: str) -> str:
        if self._tok is None:
            return text
        ids = self._tok.encode(text).ids
        if len(ids) <= CAP_TOKENS:
            return text
        marker = f"\n... [cut at {CAP_TOKENS:,} tokens]"
        return self._tok.decode(ids[:CAP_TOKENS - len(self._tok.encode(marker).ids)]) + marker

    # ------------------------------------------------------------------ the tools
    def _read_file(self, path: str, start: int = 1, end: int | None = None) -> str:
        p = self._path(path)
        if not os.path.isfile(p):
            raise ToolCallError(f"no file {path!r}")
        with open(p, "rb") as fh:
            lines = fh.read().decode("utf8", "replace").splitlines()
        start = max(1, int(start))
        end = len(lines) if end is None else min(len(lines), int(end))
        # The number and the line are separated by one tab: indentation is spaces, so the prefix cannot be read as
        # part of it (the qwen notice check, 2026-11-06: a space gutter was copied into `edit_file`'s `old` text).
        body = "\n".join(f"{i}\t{lines[i - 1]}" for i in range(start, end + 1))
        return f"{path} (lines {start}-{end} of {len(lines)})\n{body}"

    def _list_dir(self, path: str = ".") -> str:
        p = self._path(path)
        if not os.path.isdir(p):
            raise ToolCallError(f"no directory {path!r}")
        out = []
        for name in sorted(os.listdir(p)):
            if name in (".git", "node_modules") or name.startswith(".r3-"):
                continue
            out.append(name + ("/" if os.path.isdir(os.path.join(p, name)) else ""))
        return f"{path}:\n" + "\n".join(out)

    def _grep(self, pattern: str, path: str = ".") -> str:
        p = self._path(path)
        rel = os.path.relpath(p, self.work)
        r = subprocess.run(["git", "-C", self.work, "grep", "-n", "-I", "-E", "--untracked", "-e", pattern, "--",
                            rel if rel != "." else ":/", ":!node_modules"], capture_output=True, text=True,
                           errors="replace")
        if r.returncode not in (0, 1):
            raise ToolCallError(f"bad pattern: {r.stderr.strip()[:200]}")
        lines = [ln[:300] for ln in r.stdout.splitlines() if "/.r3-" not in ln and not ln.startswith(".r3-")]
        if not lines:
            return f"no matches for {pattern!r}"
        more = f"\n... ({len(lines) - GREP_MAX_LINES} more lines)" if len(lines) > GREP_MAX_LINES else ""
        return "\n".join(lines[:GREP_MAX_LINES]) + more

    def _edit_file(self, path: str, old: str, new: str) -> str:
        p = self._path(path)
        if old == "":
            if os.path.exists(p):
                raise ToolCallError(f"{path} exists: give the exact text to replace")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf8") as fh:
                fh.write(new)
            return f"created {path} ({new.count(chr(10)) + 1} lines)"
        if not os.path.isfile(p):
            raise ToolCallError(f"no file {path!r}")
        with open(p, encoding="utf8", errors="replace") as fh:
            text = fh.read()
        n = text.count(old)
        if n == 0:
            near = whitespace_matches(text, old)
            if len(near) == 1:
                raise ToolCallError(
                    f"the text to replace was not found in {path}, but it matches once apart from whitespace, at line "
                    f"{near[0]}. Copy the indentation exactly; the line-number prefix that read_file shows (the number "
                    f"and a tab) is not part of the file")
            raise ToolCallError(f"the text to replace was not found in {path}")
        if n > 1:
            raise ToolCallError(f"the text to replace occurs {n} times in {path}; give a longer, unique span")
        with open(p, "w", encoding="utf8") as fh:
            fh.write(text.replace(old, new, 1))
        line = text[:text.index(old)].count("\n") + 1
        return f"edited {path} at line {line}: replaced {old.count(chr(10)) + 1} lines with {new.count(chr(10)) + 1}"

    def _run_tests(self, files: list[str] | None = None) -> str:
        files = list(files) if files else list(self.visible)
        if not files:
            raise ToolCallError("no test files given, and the task has no visible tests")
        out = []
        for rel in files:
            self._path(rel)
            if not os.path.isfile(os.path.join(self.work, rel)):
                out.append(f"{rel}: no such test file")
                continue
            r = self.env.run_test(rel)
            if not r.get("parsed"):
                out.append(f"{rel}: the test run produced no results (exit {r.get('rc')})\n{r.get('tail', '')[-FAIL_TAIL_CHARS:]}")
                continue
            failing = [t for t, s in (r.get("tests") or {}).items() if s == "failed"]
            line = f"{rel}: {r['passed']} passed, {r['failed']} failed, {r['skipped']} skipped"
            if failing:
                line += "\nfailing:\n" + "\n".join(f"- {t}" for t in failing[:30])
                line += "\n" + re.sub(r"\x1b\[[0-9;]*m", "", r.get("tail", ""))[-FAIL_TAIL_CHARS:]
            out.append(line)
        return "\n\n".join(out)

    def _done(self, summary: str = "") -> str:
        self.done_summary = summary or "(no summary)"
        return "done"
