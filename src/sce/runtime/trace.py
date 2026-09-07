"""`python -m sce.runtime.trace --output PATH -- pytest ...` - a thin
name-alias for `sce.runtime.tracer`'s own `__main__` entry point (the
tracing machinery itself lives in `tracer.py`; this module exists only so
the tool is invocable under the name `trace`, not just `tracer`). Prefer
`sce trace` (`src/sce/cli.py`) for everyday use.
"""
from __future__ import annotations

from sce.runtime.tracer import _main

if __name__ == "__main__":
    raise SystemExit(_main())
