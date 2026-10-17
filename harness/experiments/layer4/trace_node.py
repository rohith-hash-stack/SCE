"""Runtime ground truth for a focal JS/TS function: instrument it on disk
(`source_edit.instrument_js`), run a test command with the tracer runtime
(`trace_node_runtime.cjs`), restore the file, and map the recorded
`relpath:line:name` locations to Prism qualified names (the innermost
indexed function or method whose line range holds the line).

Mocha (Express): `node_modules/.bin/mocha --require <runtime> test/res.json.js`.
Vitest (tRPC): the same runtime as a `setupFiles` entry with
`--pool=forks` (one process per file, so the same-thread inspector
session can read coverage); see LAYER4.md for the constraints.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from harness.experiments.layer4.source_edit import edited, instrument_js, locate

RUNTIME = Path(__file__).with_name("trace_node_runtime.cjs")
ENTER = "(globalThis.__l4 ? globalThis.__l4.enter() : null)"
EXIT = "(globalThis.__l4 ? globalThis.__l4.exit : () => {})"


def run_traced(repo_root: str, focal_file: str, focal_line: int, command: list[str], out_path: str,
               env: dict | None = None, timeout: int = 1800) -> dict:
    """Instrument `focal_file:focal_line`, run `command` in `repo_root` (it
    must load `RUNTIME`), restore the file, and return the raw manifest
    with the command's exit status."""
    focal_abs = os.path.join(repo_root, focal_file)
    span = locate(focal_abs, focal_line)
    source = Path(focal_abs).read_bytes()
    is_async = source[span.start:span.body_start].lstrip().startswith(b"async") or b"async " in source[
        max(0, span.start - 6):span.start]
    focal_line = source[:span.start].count(b"\n") + 1  # the definition's own first line, as V8 reports it
    run_env = dict(os.environ, **(env or {}), L4_ROOT=repo_root, L4_OUT=out_path, L4_FOCAL_FILE=focal_file,
                   L4_FOCAL_LINE=str(focal_line), L4_FOCAL_ASYNC="1" if is_async else "0")
    with edited(focal_abs):
        instrument_js(span, ENTER, EXIT)
        proc = subprocess.run(command, cwd=repo_root, env=run_env, capture_output=True, text=True, timeout=timeout)
    raw = json.loads(Path(out_path).read_text()) if Path(out_path).exists() else {}
    raw["exit_code"] = proc.returncode
    raw["output_tail"] = (proc.stdout + proc.stderr)[-2000:]
    return raw


class LineMap:
    """`relpath:line` -> innermost Prism function/method qualified name."""

    def __init__(self, builder):
        root = builder.repo_root
        self.by_file: dict[str, list] = {}
        for s in builder.symbol_table:
            if s.kind in ("function", "method"):
                rel = os.path.relpath(s.file, root) if os.path.isabs(s.file) else s.file
                self.by_file.setdefault(rel.replace(os.sep, "/"), []).append(s)

    def name(self, rel: str, line: int) -> str | None:
        inside = [s for s in self.by_file.get(rel, ()) if s.line_range[0] <= line <= s.line_range[1]]
        return min(inside, key=lambda s: s.line_range[1] - s.line_range[0]).qualified_name if inside else None


def map_manifest(raw: dict, builder, focal_qname: str) -> dict:
    """The raw manifest with ancestors/descendants as qualified names
    (counts summed; unmappable locations kept under `unmapped`)."""
    lines = LineMap(builder)
    out = {"focal": [focal_qname], "focal_calls": raw.get("focal_calls", 0), "tests": raw.get("tests", []),
           "async_window": raw.get("async_window", False), "exit_code": raw.get("exit_code")}
    for key in ("ancestors", "descendants"):
        mapped: dict[str, int] = {}
        unmapped: dict[str, int] = {}
        for loc, count in raw.get(key, {}).items():
            rel, line, _name = loc.split(":", 2)
            q = lines.name(rel, int(line))
            if q is None:
                unmapped[loc] = unmapped.get(loc, 0) + count
            elif q != focal_qname:
                mapped[q] = mapped.get(q, 0) + count
        out[key] = dict(sorted(mapped.items()))
        out[f"{key}_unmapped"] = dict(sorted(unmapped.items()))
    return out
