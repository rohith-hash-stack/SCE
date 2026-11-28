"""Execution-based grading for backward contract adaptation: splice a
candidate implementation into the target function on disk, run the test
subset that exercises it, and restore the file byte-for-byte.

A task is valid only if both gates hold (`gates`):
- **positive**: the tests pass with the original code;
- **negative**: the tests fail with the body replaced by a stub that raises
  (`raise NotImplementedError` / `throw new Error(...)`) - otherwise the
  tests do not exercise the function and a pass would mean nothing.

`contamination` runs a no-context generator (the model with no repository
context) and flags the task when it passes too often: the model may be
recalling the library's code rather than adapting to its callers.

Default test commands per corpus (`TEST_COMMANDS`); a task names the test
files or labels. Run sequentially: edits are in place.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from harness.experiments.layer4.source_edit import STUB_MESSAGE, edited, locate, replace_body, replace_function, stub

TEST_COMMANDS = {
    "fastapi": [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"],
    "django": [sys.executable, "tests/runtests.py", "--parallel=1"],
    "express": ["node_modules/.bin/mocha", "--reporter", "dot"],
    "trpc": ["pnpm", "vitest", "run"],  # run from the package directory
}
CONTAMINATION_THRESHOLD = 0.5
_FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.S)


@dataclass
class SpliceTask:
    task_id: str
    root: str  # corpus checkout
    file: str  # relative path of the target's file
    line: int  # a line of the target's definition
    command: list[str]  # full test command (cwd = root / cwd)
    cwd: str = "."
    timeout: int = 900
    name: str = ""  # the target's name, to tell a full definition from a body
    env: dict = field(default_factory=dict)

    @property
    def path(self) -> str:
        return os.path.join(self.root, self.file)


def _drop_bytecode(path: str) -> None:
    """Python checks a .pyc against the source's mtime in whole seconds and
    its size, so a candidate written in the same second as an equal-sized
    earlier version (the stub, another candidate) would run stale bytecode."""
    if path.endswith(".py"):
        Path(importlib.util.cache_from_source(path)).unlink(missing_ok=True)


def _run(task: SpliceTask) -> dict:
    _drop_bytecode(task.path)
    t = time.perf_counter()
    try:
        proc = subprocess.run(task.command, cwd=os.path.join(task.root, task.cwd),
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **task.env},
                              capture_output=True, text=True, timeout=task.timeout)
        full = proc.stdout + proc.stderr
        code, tail, stub_seen = proc.returncode, full[-3000:], STUB_MESSAGE in full
    except subprocess.TimeoutExpired:
        code, tail, stub_seen = None, f"timed out after {task.timeout}s", False
    return {"passed": code == 0, "exit_code": code, "seconds": round(time.perf_counter() - t, 1), "output_tail": tail,
            "stub_seen": stub_seen}


_OPEN_FENCE = re.compile(r"```[a-zA-Z0-9_+-]*[ \t]*\n(.*)\Z", re.S)


def code_blocks(reply: str) -> list[str]:
    """The fenced blocks of a reply, the last one also when its closing
    fence is missing (a stop sequence, a length cut)."""
    blocks, end = [], 0
    for m in _FENCE.finditer(reply):
        blocks.append(m.group(1))
        end = m.end()
    if m := _OPEN_FENCE.search(reply[end:]):
        blocks.append(m.group(1))
    return blocks


def extract_code(reply: str, language: str | None = None, name: str = "") -> str:
    """The code in a model reply: of its fenced blocks the last that
    defines `name` (when given), else the last; with no fence, the text."""
    blocks = code_blocks(reply)
    if not blocks:
        return reply.strip("\n")
    if language and name:
        defining = [b for b in blocks if is_full_definition(b, language, name)]
        if defining:
            return defining[-1].strip("\n")
    return blocks[-1].strip("\n")


def is_full_definition(code: str, language: str, name: str) -> bool:
    text = code.lstrip()
    if language == "python":
        try:
            tree = ast.parse(textwrap_dedent(code))
        except SyntaxError:
            return False
        return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and (not name or n.name == name)
                   for n in tree.body)
    pattern = rf"^(export\s+)?(async\s+)?(function\s*\*?\s*{re.escape(name)}\b|{re.escape(name)}\s*\(|(static\s+)?(get|set)\s+{re.escape(name)}\b)" \
        if name else r"^(export\s+)?(async\s+)?function\b"
    stripped = strip_js_assignment(text, name)
    if stripped is not text:  # `obj.name = function(...)`: the assignment names it, the function may be anonymous
        return re.match(r"^(async\s+)?function\b", stripped) is not None
    return re.match(pattern, text) is not None


def strip_js_assignment(code: str, name: str) -> str:
    """`res.format = function(obj) {...};` -> `function(obj) {...}`: the
    function expression an assigned method's span covers (Express defines
    its methods this way)."""
    m = re.match(rf"^\s*[\w$]+(?:\.[\w$]+)*\.{re.escape(name)}\s*=\s*(?=(?:async\s+)?function\b)", code) if name else None
    if not m:
        return code
    return re.sub(r";\s*$", "", code[m.end():].rstrip())


def drop_kept_prefix(code: str, span, source: bytes) -> str:
    """A full definition without what the file keeps outside the span: the
    `export` / `export default` before a JS/TS function on its line, the
    decorator lines above a Python one. A reply that repeats them would
    otherwise splice to `export export function ...` or decorate twice."""
    text = code.lstrip()
    if span.language == "python":
        line_start = source.rfind(b"\n", 0, span.start) + 1  # the definition's own line, indentation included
        above = source[:line_start].decode(errors="replace").splitlines()
        kept = []
        while above and above[-1].strip().startswith("@"):
            kept.insert(0, above.pop().strip())
        lines = text.splitlines()
        n = 0
        while n < len(lines) and n < len(kept) and lines[n].strip() == kept[n]:
            n += 1
        return "\n".join(lines[n:]) if n else code
    line_start = source.rfind(b"\n", 0, span.start) + 1
    prefix = source[line_start:span.start].decode(errors="replace").strip()
    if prefix and re.match(rf"{re.escape(prefix)}\s", text) and re.fullmatch(r"(export(\s+default)?|declare)", prefix):
        return text[len(prefix):].lstrip()
    return code


#: a reply `drop_kept_prefix` may change: a block opening with `export` or a decorator
PREFIX_SENSITIVE = re.compile(r"```[a-zA-Z0-9_+-]*[ \t]*\n\s*(export\b|@)")


def textwrap_dedent(code: str) -> str:
    import textwrap
    return textwrap.dedent(code)


def _python_syntax_ok(path: str) -> str | None:
    try:
        ast.parse(Path(path).read_text())
        return None
    except SyntaxError as e:
        return f"syntax error: {e}"


def gates(task: SpliceTask) -> dict:
    """Positive (original passes) and negative (stub fails) gates. The stub
    throws `STUB_IMPLEMENTATION`; `negative["stub_seen"]` records whether
    that message appears anywhere in the run's output (validity rests on
    the exit code; task validation also requires the stub to be seen, so a
    suite failing for another reason is not mistaken for a valid gate)."""
    positive = _run(task)
    with edited(task.path):
        stub(locate(task.path, task.line))
        negative = _run(task)

    return {"positive": positive, "negative": negative,
            "valid": positive["passed"] and negative["exit_code"] is not None and not negative["passed"]}


def evaluate(task: SpliceTask, reply: str) -> dict:
    """Splice the candidate in `reply` and run the tests. A full definition
    replaces the function (decorators kept); anything else replaces the body."""
    span = locate(task.path, task.line)
    code = extract_code(reply, span.language, task.name)
    full = is_full_definition(code, span.language, task.name)
    if full:
        code = drop_kept_prefix(code, span, Path(task.path).read_bytes())
    if full and span.language != "python":
        code = strip_js_assignment(code.lstrip(), task.name)
    with edited(task.path):
        if full:
            replace_function(span, code)
        else:
            replace_body(span, code)
        syntax_ok = _parses(task.path)
        if span.language == "python" and (err := _python_syntax_ok(task.path)):
            return {"passed": False, "exit_code": None, "seconds": 0.0, "output_tail": err, "syntax_ok": False,
                    "mode": "function" if full else "body"}
        result = _run(task)  # JS/TS: the runner decides (tree-sitter is error-tolerant), syntax_ok is recorded
    result["mode"] = "function" if full else "body"
    result["syntax_ok"] = syntax_ok
    return result


def _parses(path: str) -> bool | None:
    """Whether the spliced file parses without error (tree-sitter, every
    language Prism indexes); None when no grammar is available."""
    try:
        from prism.parser.tree_sitter_loader import parse_source
        parsed = parse_source(path, Path(path).read_bytes())
    except Exception:
        return None
    return None if parsed is None else not parsed.root_node.has_error


def contamination(task: SpliceTask, generate: Callable[[int], str], seeds: list[int],
                  threshold: float = CONTAMINATION_THRESHOLD) -> dict:
    """Run the no-context candidates `generate(seed)`; flag the task when
    their pass rate reaches `threshold`."""
    results = [evaluate(task, generate(seed)) for seed in seeds]
    rate = sum(r["passed"] for r in results) / len(results) if results else 0.0
    return {"no_context_pass_rate": rate, "flagged": rate >= threshold, "n": len(results),
            "passed": [r["passed"] for r in results]}
