"""Which files on disk belong to the repository, according to git.

Inside a git work tree, a file PRISM indexes must be tracked, or be new and not ignored
(`git ls-files --cached --others --exclude-standard`). Everything else on disk is left out:
an initialised submodule's checkout (aiohttp's `vendor/llhttp`, 13 Node files and 164 graph
nodes), ignored build output, and files left behind by a checkout. Before this, the index
depended on what happened to be on disk, so two clones of the same revision could disagree,
and an index cache faithfully kept the difference.

Outside a git work tree (or with `PRISM_GIT_DISCOVERY=0`), nothing is filtered.

No PRISM imports: `prism.cli` and `prism.traversal._cache_keys` both use this, and the
second cannot import the first (see its `_IGNORED_DIRS`).
"""
from __future__ import annotations

import os
import subprocess


def git_visible_files(repo_root: str) -> set[str] | None:
    """Absolute paths of the files git considers part of `repo_root`'s work tree, or `None`
    when `repo_root` is not in a git work tree, git is unavailable, or the check is disabled."""
    if os.environ.get("PRISM_GIT_DISCOVERY") == "0":
        return None
    root = os.path.abspath(repo_root)
    try:
        inside = subprocess.run(["git", "-C", root, "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True, timeout=30)
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return None
        out = subprocess.run(["git", "-C", root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return {os.path.join(root, p) for p in out.stdout.decode("utf-8", "surrogateescape").split("\0") if p}


def keep_git_visible(repo_root: str, files: list[str]) -> list[str]:
    """`files` without the ones git does not consider part of the work tree (unchanged outside git)."""
    visible = git_visible_files(repo_root)
    if visible is None:
        return files
    return [f for f in files if os.path.abspath(f) in visible]
