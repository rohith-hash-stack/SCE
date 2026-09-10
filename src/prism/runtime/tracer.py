"""Passive test-execution tracer: records real `caller -> callee` call
events during a test run into a JSON-lines trace file, for
`reconciler.py` to later merge against the static Concrete Graph.

Two ways to drive it:

  1. As a pytest plugin (the primary, recommended path) - `prism trace
     --repo . -- pytest ...` runs pytest as a subprocess with this module
     auto-loaded as a plugin (`-p prism.runtime.tracer`) and two environment
     variables set (`PRISM_TRACE_REPO_ROOT`, `PRISM_TRACE_OUTPUT`); this
     module's `pytest_configure`/`pytest_unconfigure` hooks start/stop the
     tracer around the whole test session. Any Python testing tool that
     honors pytest's plugin-discovery protocol picks this up the same way.
  2. Programmatically, via the `Tracer` context manager - useful for
     tracing something that isn't a pytest run at all (a one-off script,
     a REPL session): `with Tracer(repo_root, output_path): run_it()`.

Uses `sys.settrace` (portable back to the oldest Python this project
supports) rather than Python 3.12+'s lower-overhead `sys.monitoring` API -
a real improvement for a future revision, but not one this module commits
to yet since `sys.monitoring` doesn't exist before 3.12 and this project
doesn't otherwise require it.
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

from prism.graph.symbol_table import path_to_module

# Frames whose file lives under one of these directory names (anywhere in
# its path) are never traced - virtualenvs, vendored/third-party code, and
# this package's own tracing machinery itself (tracing the tracer would be
# both useless noise and, for the recursive `_trace_calls` call below,
# a correctness hazard).
_EXCLUDED_PATH_MARKERS = (
    f"{os.sep}.venv{os.sep}", f"{os.sep}venv{os.sep}", f"{os.sep}site-packages{os.sep}",
    f"{os.sep}node_modules{os.sep}", f"{os.sep}.git{os.sep}",
    f"{os.sep}prism{os.sep}runtime{os.sep}tracer.py",
)

# Frame co_name values that are not a "symbol" a static index would ever
# register (module-level top-level code, comprehension/generator internals
# tree-sitter's own definitions query never captures either) - skipped so a
# trace event's `callee` always has a chance of matching a real qualified
# name from Pass 1's `GlobalSymbolTable`.
_SKIP_CO_NAMES = frozenset({"<module>", "<lambda>", "<genexpr>", "<listcomp>", "<setcomp>", "<dictcomp>"})


@dataclasses.dataclass(frozen=True)
class TraceRecord:
    """One row of a `.prism/traces/run_*.jsonl` file - a single observed
    runtime event. `callee=None` with `sink_tag` set represents a pure
    external-sink observation (e.g. an OTel client span) with no
    resolvable in-repo callee of its own; every other combination is a
    `caller -> callee` call event, `sink_tag` set or not.
    """

    caller: str | None
    callee: str | None
    source: str  # "pytest_tracer" | "otel"
    timestamp: float | None = None
    sink_tag: str | None = None
    detail: str | None = None
    #: Item 10 (second post-implementation audit): the callee frame's
    #: repo-relative-resolvable source location, captured whenever a
    #: `pytest_tracer`-sourced event's frame is in-repo - independent of
    #: whether `callee` itself ends up matching a static symbol, so
    #: `GraphReconciler`'s Fuzzy Anchor Matching pass has a location to
    #: search from even when the exact qualified name doesn't resolve (a
    #: decorator-wrapped or metaclass-synthesized callable whose
    #: `co_qualname` drifted from what Pass 1 indexed, but whose actual
    #: source line is still right where the real definition lives).
    #: `None` for every `otel`-sourced event (OTel spans carry no
    #: guaranteed source-location attribute Prism can trust).
    callee_file: str | None = None
    callee_line: int | None = None

    def to_json_line(self) -> str:
        return json.dumps(dataclasses.asdict(self), separators=(",", ":"))


def resolve_qualified_name(frame: types.FrameType, repo_root: str) -> str | None:
    """The `module.Class.method`-shaped qualified name for `frame`, in the
    exact convention `prism.graph.concrete_builder._register_definition`
    uses for its own static symbols - or None if `frame` isn't a call Prism
    would ever have statically indexed (outside `repo_root`, or a
    module/lambda/comprehension frame rather than a real function/method).

    Python 3.11+'s `co_qualname` already resolves nested classes/closures
    into the same "Class.method" (or "Outer.Inner.method") shape the
    static indexer's own enclosing-class walk produces, so the two line up
    directly with no extra bookkeeping needed here.
    """
    code = frame.f_code
    if code.co_name in _SKIP_CO_NAMES:
        return None
    raw_filename = code.co_filename
    if not raw_filename or raw_filename.startswith("<"):
        # A synthetic pseudo-filename - `<frozen importlib._bootstrap>`,
        # `<string>`, `<stdin>`, a dynamically `exec`'d/`eval`'d block -
        # not a real file `os.path.abspath` can safely resolve (it would
        # silently treat the whole bracketed string as a *relative* path
        # and join it onto the current directory, which can accidentally
        # land back inside `repo_root` and misclassify frozen stdlib
        # bootstrap machinery as in-repo code - confirmed by a live
        # pytest run whose trace was flooded with `<frozen importlib...>`
        # entries before this check was added).
        return None
    filename = os.path.abspath(raw_filename)
    if any(marker in f"{os.sep}{filename}{os.sep}" for marker in _EXCLUDED_PATH_MARKERS):
        return None
    try:
        if os.path.commonpath([filename, repo_root]) != repo_root:
            return None
    except ValueError:
        # Different drives on Windows, or some other reason the two paths
        # share no common root at all - definitely not an in-repo frame.
        return None

    module = path_to_module(filename, repo_root)
    qualname = getattr(code, "co_qualname", None) or code.co_name
    return f"{module}.{qualname}" if module else qualname


class Tracer:
    """Installs a `sys.settrace` hook for the duration of a `with` block
    (or an explicit `start()`/`stop()` pair) and streams every in-repo
    `caller -> callee` call event to `output_path` as it happens - not
    buffered in memory, so a crashing test run still leaves a usable
    partial trace behind.
    """

    def __init__(self, repo_root: str, output_path: str | os.PathLike[str]) -> None:
        self.repo_root = os.path.abspath(repo_root)
        self.output_path = Path(output_path)
        self._file = None
        self._previous_trace = None
        self._previous_thread_trace = None

    def start(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.output_path, "a", buffering=1)  # line-buffered
        self._previous_trace = sys.gettrace()
        self._previous_thread_trace = threading.gettrace()
        sys.settrace(self._trace_calls)
        threading.settrace(self._trace_calls)

    def stop(self) -> None:
        sys.settrace(self._previous_trace)
        threading.settrace(self._previous_thread_trace)
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> "Tracer":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def _trace_calls(self, frame: types.FrameType, event: str, arg: object):
        # Returning `self._trace_calls` (not None) as the local trace
        # function keeps this same dispatcher receiving every subsequent
        # event for `frame` too - harmless, since it no-ops on anything
        # but "call", and it's what lets a *nested* call's own "call"
        # event still reach the global tracer (the interpreter always
        # re-invokes the registered global tracer for a brand new frame,
        # regardless of what the enclosing frame's local tracer returned;
        # returning `None` here would only stop *line-level* tracing of
        # this frame, which this class never wants anyway).
        if event != "call":
            return self._trace_calls

        callee = resolve_qualified_name(frame, self.repo_root)
        if callee is None:
            return self._trace_calls

        caller = resolve_qualified_name(frame.f_back, self.repo_root) if frame.f_back is not None else None
        # `resolve_qualified_name` above already proved this frame's file is
        # a real, in-repo path - `co_firstlineno` is the code object's `def`
        # line (matches the tree-sitter function-definition node's own
        # start line, decorators excluded from both), independent of
        # whether `callee` ends up matching a Pass-1-registered symbol.
        callee_file = os.path.abspath(frame.f_code.co_filename)
        callee_line = frame.f_code.co_firstlineno
        self._write(TraceRecord(
            caller=caller, callee=callee, source="pytest_tracer", timestamp=time.time(),
            callee_file=callee_file, callee_line=callee_line,
        ))
        return self._trace_calls

    def _write(self, record: TraceRecord) -> None:
        if self._file is not None:
            self._file.write(record.to_json_line() + "\n")


# --------------------------------------------------------------------- #
# pytest plugin hooks - auto-loaded via `-p prism.runtime.tracer` (see
# `prism.cli.trace`). Configuration travels through environment variables,
# not pytest CLI flags, so this works identically whether pytest is
# invoked directly (`pytest -p prism.runtime.tracer ...`, with the env vars
# exported by hand) or through `prism trace`.
# --------------------------------------------------------------------- #
_active_tracer: Tracer | None = None


def pytest_configure(config) -> None:  # noqa: ANN001 - pytest's own hook signature
    global _active_tracer
    output = os.environ.get("PRISM_TRACE_OUTPUT")
    if not output:
        return
    repo_root = os.environ.get("PRISM_TRACE_REPO_ROOT") or str(config.rootpath)
    _active_tracer = Tracer(repo_root, output)
    _active_tracer.start()


def pytest_unconfigure(config) -> None:  # noqa: ANN001 - pytest's own hook signature
    global _active_tracer
    if _active_tracer is not None:
        _active_tracer.stop()
        _active_tracer = None


# --------------------------------------------------------------------- #
# Subprocess driver - shared by `prism trace` (src/prism/cli.py) and this
# module's own `python -m prism.runtime.tracer -- <command>` entry point.
# Runs pytest as a real subprocess (not an in-process re-exec) with this
# module auto-loaded as a plugin, so tracing happens inside the actual
# process the tests run in - the simplest way to get correct behavior for
# anything pytest itself forks, multiprocesses, or otherwise manages,
# without this module having to reimplement pytest's own invocation
# semantics (console-script entry point vs. `-m` vs. a bare script path).
# --------------------------------------------------------------------- #
def run_traced_pytest(repo_root: str, output_path: str | os.PathLike[str], pytest_args: list[str]) -> int:
    """Run `python -m pytest -p prism.runtime.tracer <pytest_args>` as a
    subprocess with tracing enabled, returning pytest's own exit code.
    """
    abs_repo_root = os.path.abspath(repo_root)
    # Resolved against *this* process's cwd, before the subprocess call
    # below changes directory - a relative `output_path` should mean what
    # it looks like it means to the caller, not silently get reinterpreted
    # against `repo_root` once the child process's cwd changes.
    abs_output = os.path.abspath(str(output_path))
    env = dict(os.environ)
    env["PRISM_TRACE_REPO_ROOT"] = abs_repo_root
    env["PRISM_TRACE_OUTPUT"] = abs_output
    cmd = [sys.executable, "-m", "pytest", "-p", "prism.runtime.tracer", *pytest_args]
    # Run from `repo_root` so a relative test path/pattern in `pytest_args`
    # (the common case: `prism trace --repo . -- pytest tests/test_orders.py`)
    # resolves against the traced repository, not this process's own
    # working directory - which, run through `prism`'s CLI, is very often a
    # different directory entirely.
    result = subprocess.run(cmd, env=env, cwd=abs_repo_root)
    return result.returncode


def _main(argv: list[str] | None = None) -> int:
    """`python -m prism.runtime.tracer --repo . --output PATH -- pytest ...`
    - a thin standalone entry point for tracing a pytest run without going
    through the full `prism` CLI. Prefer `prism trace` (`src/prism/cli.py`) for
    everyday use - it also picks a timestamped output path and persists
    the resulting reconciliation automatically.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="python -m prism.runtime.tracer")
    parser.add_argument("--repo", default=".", help="Repository root (default: current directory).")
    parser.add_argument("--output", required=True, help="Path to write the JSON-lines trace file to.")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="The pytest invocation to run, after `--`.")
    args = parser.parse_args(argv)

    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("no command given - expected e.g. `... --output PATH -- pytest tests/`")
    if command[0] != "pytest":
        parser.error(f"only a `pytest` command is supported here, got {command[0]!r}")

    return run_traced_pytest(args.repo, args.output, command[1:])


if __name__ == "__main__":
    raise SystemExit(_main())
