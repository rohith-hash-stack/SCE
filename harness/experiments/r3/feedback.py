"""Phase 2, Phase C1-C3 (`harness/experiments/r4/PHASE2_PLAN.md`): harness feedback that every arm gets the same way.

The 600-run audit found that the harness did not tell the agent when its own edit broke a file (delete-05: 13 runs
ended with `else:` and no body). `edit_file` reported a normal edit, and `run_tests` returned only the last 1,200
characters of pytest's output, which held only the cut summary lines, not the `IndentationError`.

- **C1, `syntax_problem(path, text)`:** whether a Python (`ast`) or JS/TS (tree-sitter) file's text parses; if not,
  where and why. `edit_file` reports it after the edit (user decision: the edit is applied, with a warning).
- **C2, `first_error(output, workdir)`:** the first real error in a test run's full output (the exception line and the
  `File ..., line N` lines before it), so a collection or import error is shown even when the output's tail is only
  a list of failing test names.
- **C3, `Repeats`:** a note when a call repeats an earlier identical call and gets the identical answer.

All three only add text to a tool's answer; they never refuse an action or change what the tool does.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re

JS_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts")
FIRST_ERROR_CHARS = 900


# ---------------------------------------------------------------------------------------------- C1: syntax
def syntax_problem(path: str, text: str) -> str | None:
    """None if `text` parses as `path`'s language (or the language is not checked); else 'line N: message'."""
    if path.endswith(".py"):
        try:
            ast.parse(text, filename=path)
        except SyntaxError as exc:
            return f"line {exc.lineno}: {exc.msg}"
        except ValueError as exc:                     # e.g. null bytes
            return str(exc)
        return None
    if path.endswith(JS_EXTS):
        from prism.parser.tree_sitter_loader import parse_source
        parsed = parse_source(path, text.encode("utf8", "replace"))
        if parsed is None or parsed.root_node is None or not parsed.root_node.has_error:
            return None
        bad = _first_error_node(parsed.root_node)
        if bad is None:
            return "the file does not parse"
        line, col = bad.start_point[0] + 1, bad.start_point[1] + 1
        what = f"missing {bad.type}" if bad.is_missing else "unexpected text"
        snippet = text.splitlines()[line - 1].strip()[:80] if line <= len(text.splitlines()) else ""
        return f"line {line}, column {col}: {what}" + (f" near `{snippet}`" if snippet else "")
    return None


def _first_error_node(node):
    stack = [node]
    while stack:
        n = stack.pop(0)
        if n.type == "ERROR" or n.is_missing:
            return n
        if n.has_error:
            stack = list(n.children) + stack
    return None


def edit_warning(path: str, before: str | None, after: str) -> str:
    """The text to add to `edit_file`'s answer: empty when the file parses after the edit."""
    problem = syntax_problem(path, after)
    if problem is None:
        return ""
    was_broken = before is not None and syntax_problem(path, before) is not None
    if was_broken:
        return (f"\nwarning: {path} still does not parse after this edit ({problem}). The edit was applied; the file "
                f"must parse before its code can run.")
    return (f"\nwarning: this edit was applied, but {path} no longer parses: {problem}. Code that imports or runs this "
            f"file will fail until it is fixed.")


# ---------------------------------------------------------------------------------------------- C2: test errors
_ERR_LINE = re.compile(r"^(?:E\s+)?(?:[\w.]+\.)?[A-Z]\w*(?:Error|Exception|Exit|Interrupt)\b(?::|$)")
_FILE_LINE = re.compile(r'^(?:E\s+)?\s*File "[^"]+", line \d+')
_PY_LOC = re.compile(r"^\S+\.py:\d+: ")


def first_error(output: str, workdir: str | None = None) -> str | None:
    """The first exception in the output, with the lines from its nearest preceding `File "...", line N` (at most 8
    lines before it). None when there is none."""
    lines = output.splitlines()
    for i, ln in enumerate(lines):
        s = ln.rstrip()
        if not _ERR_LINE.match(s.strip() if not s.startswith("E") else s) or _PY_LOC.match(s):
            continue
        lo = i
        for j in range(i - 1, max(-1, i - 9), -1):
            if _FILE_LINE.match(lines[j]):
                lo = j
                break
        block = "\n".join(x.rstrip() for x in lines[lo:i + 1])
        if workdir:
            block = block.replace(os.path.abspath(workdir) + os.sep, "")
        return block[-FIRST_ERROR_CHARS:]
    return None


# ---------------------------------------------------------------------------------------------- C3: repeats
class Repeats:
    """Remembers each call (tool and arguments) and the hash of its answer."""

    def __init__(self):
        self.seen: dict[str, tuple[int, str]] = {}
        self.streak: dict[str, tuple[str, list[int]]] = {}     # key -> (answer digest, the turns that got it in a row)

    @staticmethod
    def _key(tool: str, args: dict) -> str:
        return tool + "|" + json.dumps(args, sort_keys=True, default=str)

    def check(self, turn: int, tool: str, args: dict, output: str) -> int | None:
        """The turn of an earlier identical call that got the identical answer, or None."""
        key, digest = self._key(tool, args), hashlib.sha256(output.encode("utf8", "replace")).hexdigest()
        prev = self.seen.get(key)
        self.seen[key] = (turn, digest)
        return prev[0] if prev is not None and prev[1] == digest else None

    def same(self, turn: int, tool: str, args: dict, output: str) -> list[int]:
        """The earlier turns, in order, at which this identical call got this identical answer with no different
        answer in between (empty: none). A different answer starts the count again. Also updates `check`'s memory."""
        key, digest = self._key(tool, args), hashlib.sha256(output.encode("utf8", "replace")).hexdigest()
        self.seen[key] = (turn, digest)
        prev = self.streak.get(key)
        earlier = list(prev[1]) if prev is not None and prev[0] == digest else []
        self.streak[key] = (digest, earlier + [turn])
        return earlier

    @staticmethod
    def refusal(earlier: list[int]) -> str:
        """POC 2 loop breaker (`AgentRun(guards=True)`): the answer to a call made a third time with nothing changed."""
        turns = ", ".join(map(str, earlier))
        return (f"not answered again: you made this exact call at turns {turns} and got the same answer each time; "
                f"nothing it reads has changed since. Its answer is in your history. Do something different: read "
                f"another part of the file, search for something else, make the edit, or run the tests.")

    @staticmethod
    def note(prev_turn: int) -> str:
        return (f"\n\nnote: this is the same call as your call at turn {prev_turn}, and the answer has not changed "
                f"since then.")
