"""Render the R2 Kaggle cell, pinned to a code commit (run after the freeze commit).

    python -m harness.experiments.r2.cell COMMIT        # kaggle/r2_cell.template.py -> kaggle/r2_cell.py

It fills in the commit, the git tree ids of `src` and `harness` at that commit, and the sha256 of
`harness/experiments/r2/manifest.json` at that commit. The cell refuses to run if any of them differ.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True, text=True).stdout


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        raise SystemExit(__doc__)
    commit = git("rev-parse", args[0]).strip()
    man = subprocess.run(["git", "-C", str(ROOT), "show", f"{commit}:harness/experiments/r2/manifest.json"],
                         check=True, capture_output=True).stdout
    fills = {"@@CODE_COMMIT@@": commit, "@@SRC_TREE@@": git("rev-parse", f"{commit}:src").strip(),
             "@@HARNESS_TREE@@": git("rev-parse", f"{commit}:harness").strip(),
             "@@MANIFEST_SHA@@": hashlib.sha256(man).hexdigest()}
    text = (ROOT / "kaggle" / "r2_cell.template.py").read_text()
    for k, v in fills.items():
        if k not in text:
            raise SystemExit(f"{k} missing from the template")
        text = text.replace(k, v)
    (ROOT / "kaggle" / "r2_cell.py").write_text(text)
    print(fills)
    return 0


if __name__ == "__main__":
    sys.exit(main())
