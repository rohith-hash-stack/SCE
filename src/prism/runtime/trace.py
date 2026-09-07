"""`python -m prism.runtime.trace --output PATH -- pytest ...` - a thin
name-alias for `prism.runtime.tracer`'s own `__main__` entry point (the
tracing machinery itself lives in `tracer.py`; this module exists only so
the tool is invocable under the name `trace`, not just `tracer`). Prefer
`prism trace` (`src/prism/cli.py`) for everyday use.
"""
from __future__ import annotations

from prism.runtime.tracer import _main

if __name__ == "__main__":
    raise SystemExit(_main())
