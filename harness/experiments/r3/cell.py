"""Render the R3 Kaggle cell (the qwen notice check), pinned to a code commit and to the repositories' revisions.

    python -m harness.experiments.r3.cell COMMIT SMOKE_REV NOTICE_REV   # kaggle/r3_cell.template.py -> kaggle/r3_cell.py

- COMMIT: the commit holding the harness;
- SMOKE_REV: the h3 revision for the install-only smoke test;
- NOTICE_REV: the werkzeug revision for the notice check.

It fills in the commit, the git tree ids of `src` and `harness` at that commit, and the two revisions. The cell
refuses to run if the code it checks out differs.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True).stdout.strip()


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 3 or not all(re.fullmatch(r"[0-9a-f]{40}", a) for a in args[1:]):
        raise SystemExit(__doc__)
    commit = git("rev-parse", args[0])
    fills = {"@@CODE_COMMIT@@": commit, "@@SRC_TREE@@": git("rev-parse", f"{commit}:src"),
             "@@HARNESS_TREE@@": git("rev-parse", f"{commit}:harness"), "@@SMOKE_REV@@": args[1],
             "@@NOTICE_REV@@": args[2]}
    text = (ROOT / "kaggle" / "r3_cell.template.py").read_text()
    for k, v in fills.items():
        if k not in text:
            raise SystemExit(f"{k} missing from the template")
        text = text.replace(k, v)
    (ROOT / "kaggle" / "r3_cell.py").write_text(text)
    print(fills)
    return 0


if __name__ == "__main__":
    sys.exit(main())
