"""Type 4's answer key: the production call edges a repository's own test suite really executes.

    SUITE_ROOT=<repo> SUITE_OUT=<edges.json> python -m pytest -p harness.experiments.suite.trace_edges <tests>

With `sys.setprofile`, every call of a production function (a file under SUITE_ROOT that is not a test file)
is attributed to the nearest production function below it on the stack. Library and test frames in between
are skipped, so `A -> (decorator, library) -> B` counts as `A -> B`: a change to B reaches A.
- Edges are kept unique, with counts, in memory.
- They are written every 60 seconds and at the end, so a suite that is stopped still leaves its edges.
- Names are `module.Qualname`, the same as PRISM's qualified names, with nested functions folded into the
  function that encloses them.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import Counter

from harness.experiments.layer4.trace_py import is_test_path, qualified

MAX_WALK = 12   # frames searched below a call for the production function it belongs to


class EdgeTracer:
    def __init__(self, root: str, out: str):
        self.root, self.out = os.path.abspath(root), out
        self.edges: Counter = Counter()
        self.called: Counter = Counter()
        self._files: dict[str, str | None] = {}
        self._last = time.time()

    def _rel(self, filename: str) -> str | None:
        """The repo-relative path of a production file, else None (cached per code file)."""
        got = self._files.get(filename, False)
        if got is not False:
            return got
        path = os.path.abspath(filename)
        rel = os.path.relpath(path, self.root) if path.startswith(self.root + os.sep) else None
        if rel is not None and (not rel.endswith(".py") or is_test_path(rel) or "site-packages" in rel
                                or rel.startswith((".venv", "venv", ".tox"))):
            rel = None
        self._files[filename] = rel
        return rel

    def _name(self, frame) -> str | None:
        rel = self._rel(frame.f_code.co_filename)
        if rel is None:
            return None
        qual = getattr(frame.f_code, "co_qualname", frame.f_code.co_name)
        if qual.startswith("<") or "<lambda>" in qual or "<genexpr>" in qual:
            return None
        return qualified(rel, qual)

    def profile(self, frame, event, arg):
        if event != "call":
            return
        callee = self._name(frame)
        if callee is None:
            return
        self.called[callee] += 1
        f, n = frame.f_back, 0
        while f is not None and n < MAX_WALK:
            caller = self._name(f)
            if caller is not None:
                if caller != callee:
                    self.edges[(caller, callee)] += 1
                break
            f, n = f.f_back, n + 1
        if time.time() - self._last > 60:
            self.save()

    def save(self) -> None:
        self._last = time.time()
        tmp = self.out + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"root": self.root, "edges": sorted([u, v, c] for (u, v), c in self.edges.items()),
                       "called": dict(sorted(self.called.items()))}, fh)
        os.replace(tmp, self.out)

    def start(self) -> None:
        sys.setprofile(self.profile)
        threading.setprofile(self.profile)

    def stop(self) -> None:
        sys.setprofile(None)
        threading.setprofile(None)
        self.save()


_tracer: EdgeTracer | None = None


def pytest_configure(config):
    global _tracer
    root, out = os.environ.get("SUITE_ROOT"), os.environ.get("SUITE_OUT")
    if root and out:
        _tracer = EdgeTracer(root, out)
        _tracer.start()


def pytest_unconfigure(config):
    if _tracer is not None:
        _tracer.stop()
