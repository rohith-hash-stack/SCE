"""Runtime ground truth for a focal Python function: a pytest plugin that
records, with `sys.setprofile`, every repo function on the call stack while
the focal function runs.

    L4_ROOT=<repo> L4_FOCAL=<module.Qual.name>[,...] L4_OUT=<manifest.json> \\
        python -m pytest -p harness.experiments.layer4.trace_py <tests>

For each call of a focal function the plugin records:
- **ancestors** - repo functions below it on the stack (who called it, at
  any depth): the upstream blast radius of changing it;
- **descendants** - repo functions entered while it is on the stack (what
  it runs, at any depth);
- **tests** - the test ids during which it ran;
- **sql** - statements Django executed while it was on the stack, by table
  and verb (when Django is importable), the database side effects.

Names are `module.Qualname` (`django.db.backends.base.base.BaseDatabaseWrapper.cursor`),
the same as Prism's qualified names; nested functions are folded into
their enclosing function. Only files under L4_ROOT that are not test files
count as repo functions. The manifest is deterministic (sorted keys,
counts). Limits: a coroutine resumed by the event loop rather than awaited
directly sees the loop's frames below it, not its logical caller, so
ancestors across `asyncio` task boundaries are partial; threads started by
the code under test are traced via `threading.setprofile`.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from collections import Counter

_SQL_TABLE = re.compile(
    r"""\b(?:FROM|INTO|UPDATE|JOIN|TABLE)\s+["'`]?([A-Za-z_][\w.]*)["'`]?""", re.I)
_SQL_VERB = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|SAVEPOINT|RELEASE|ROLLBACK|BEGIN|COMMIT)\b", re.I)
_TEST_PARTS = ("tests", "test", "testing")
_TRANSACTION = ("SAVEPOINT", "RELEASE", "ROLLBACK", "BEGIN", "COMMIT")


def module_name(relpath: str) -> str:
    mod = relpath[:-3] if relpath.endswith(".py") else relpath
    parts = mod.replace(os.sep, "/").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def qualified(relpath: str, qualname: str) -> str:
    """`module.Qual.name`, a nested function folded into its encloser."""
    qualname = qualname.split(".<locals>", 1)[0]
    return f"{module_name(relpath)}.{qualname}" if qualname != "<module>" else module_name(relpath)


def is_test_path(relpath: str) -> bool:
    parts = relpath.replace(os.sep, "/").split("/")
    name = parts[-1]
    return any(p in _TEST_PARTS for p in parts[:-1]) or name.startswith("test_") or name.endswith("_test.py") \
        or name == "conftest.py"


class Tracer:
    def __init__(self, root: str, focal: set[str]):
        self.root = os.path.realpath(root) + os.sep
        self.focal = focal
        self.names: dict[object, str | None] = {}  # code object -> repo qualified name (None = not repo code)
        self.local = threading.local()
        self.ancestors: Counter = Counter()
        self.descendants: Counter = Counter()
        self.focal_calls: Counter = Counter()
        self.tests: set[str] = set()
        self.sql: dict[str, Counter] = {}
        self.current_test: str | None = None

    def _name(self, code) -> str | None:
        if code not in self.names:
            filename = os.path.realpath(code.co_filename)
            name = None
            if filename.startswith(self.root):
                rel = filename[len(self.root):]
                if rel.endswith(".py") and not is_test_path(rel):
                    name = qualified(rel, getattr(code, "co_qualname", code.co_name))
            self.names[code] = name
        return self.names[code]

    def _stack(self) -> list:
        if not hasattr(self.local, "stack"):
            self.local.stack, self.local.depth = [], 0
        return self.local.stack

    def profile(self, frame, event, arg):
        if event == "call":
            stack = self._stack()
            name = self._name(frame.f_code)
            stack.append(name)
            if self.local.depth > 0 and name is not None and name not in self.focal:
                self.descendants[name] += 1
            if name in self.focal:
                self.focal_calls[name] += 1
                if self.current_test:
                    self.tests.add(self.current_test)
                for below in stack[:-1]:
                    if below is not None and below not in self.focal:
                        self.ancestors[below] += 1
                self.local.depth += 1
        elif event == "return":
            stack = self._stack()
            if stack:
                name = stack.pop()
                if name in self.focal:
                    self.local.depth -= 1

    def on_sql(self, sql: str) -> None:
        """Count one statement by table and verb while a focal call is on
        this thread's stack. Transaction control (savepoints, BEGIN/COMMIT)
        goes under `_transaction`."""
        if getattr(self.local, "depth", 0) <= 0 or not isinstance(sql, str):
            return
        verb = (_SQL_VERB.match(sql) or [None, "OTHER"])[1].upper()
        if verb in _TRANSACTION:
            self.sql.setdefault("_transaction", Counter())[verb] += 1
            return
        for table in dict.fromkeys(_SQL_TABLE.findall(sql)) or ["?"]:
            self.sql.setdefault(table, Counter())[verb] += 1

    def manifest(self) -> dict:
        return {
            "focal": sorted(self.focal),
            "focal_calls": dict(sorted(self.focal_calls.items())),
            "tests": sorted(self.tests),
            "ancestors": dict(sorted(self.ancestors.items())),
            "descendants": dict(sorted(self.descendants.items())),
            "sql": {t: dict(sorted(v.items())) for t, v in sorted(self.sql.items())},
        }


def _patch_django(tracer: Tracer) -> None:
    """Count every statement Django sends to the database. Both cursor
    wrappers (`CursorWrapper`, and `CursorDebugWrapper`, which
    `assertNumQueries` switches on and which calls the former) funnel into
    `CursorWrapper._execute_with_wrappers`, so that one method is patched -
    patching both `execute`s would count debug-cursor statements twice."""
    try:
        from django.db.backends import utils
    except Exception:
        return
    cls = utils.CursorWrapper
    if getattr(cls, "_l4_patched", False):
        return
    original = cls.__dict__.get("_execute_with_wrappers")
    if original is not None:
        def wrapped(self, sql, *args, __orig=original, **kwargs):
            tracer.on_sql(sql)
            return __orig(self, sql, *args, **kwargs)
        cls._execute_with_wrappers = wrapped
    else:  # Django < 2.0: no single choke point
        for meth in ("execute", "executemany"):
            orig = cls.__dict__.get(meth)
            if orig is not None:
                def wrapped_m(self, sql, *args, __orig=orig, **kwargs):
                    tracer.on_sql(sql)
                    return __orig(self, sql, *args, **kwargs)
                setattr(cls, meth, wrapped_m)
    cls._l4_patched = True


_TRACER: Tracer | None = None


def pytest_configure(config):
    global _TRACER
    focal = {f.strip() for f in os.environ.get("L4_FOCAL", "").split(",") if f.strip()}
    if not focal:
        return
    _TRACER = Tracer(os.environ.get("L4_ROOT", os.getcwd()), focal)
    _patch_django(_TRACER)
    sys.setprofile(_TRACER.profile)
    threading.setprofile(_TRACER.profile)


def pytest_runtest_setup(item):
    if _TRACER is not None:
        _TRACER.current_test = item.nodeid
        _patch_django(_TRACER)  # Django may be imported only by the tests


def pytest_unconfigure(config):
    if _TRACER is None:
        return
    sys.setprofile(None)
    threading.setprofile(None)
    out = os.environ.get("L4_OUT")
    if out:
        with open(out, "w") as fh:
            json.dump(_TRACER.manifest(), fh, indent=1, sort_keys=True)
