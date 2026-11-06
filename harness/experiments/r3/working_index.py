"""R3's index layer: the index every index-backed tool reads, kept equal to a cold build (amendment R3-5, Revision 1).

    wi = WorkingIndex(workdir, shadow_dir)     # the agent's checkout; a scratch path for the shadow worktree
    r = wi.refresh()                           # before every index-backed tool call
    wi.builder                                 # a ConcreteGraphBuilder over the shadow tree
    wi.notice(files)                           # the notice for any gated files an answer touches

How it stays equal to a cold build:
- The **shadow** is a git worktree of the workdir's HEAD. It holds exactly the bytes PRISM is allowed to show: every
  visible file as it is in the workdir, except gated files, which keep their last parsable bytes.
- The index is always a full `build_pipeline(shadow, use_cache=False)`, the same function a cold build runs. It is
  never patched. So "answers equal a cold build of the tree with gated files at their last parsable version" holds
  by construction, and tests E1-E3 check it against independent cold builds.
- Relative paths are the same in both trees, so rendered answers name the workdir's files.

The drift probe (`(size, mtime)`, then a sha256 of the files that disagree) runs over the workdir's visible files
(`prism.cli.discover_files`, git-aware). Only a byte change counts. Additions and deletions count too.

**Gate** (`_gated`): a changed file is gated when its new version has more tree-sitter ERROR/MISSING nodes than its
last indexed version. For a file that parsed cleanly before, this is exactly `root_node.has_error`. Files that report
errors without any edit, from grammar limits (0-1% of files in R3's pool), are not gated by a clean edit. [This
refinement of "does not parse" awaits the team's sign-off, recorded in R3-5 before the freeze.]
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass, field


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def error_nodes(path: str, data: bytes) -> int | None:
    """ERROR + MISSING nodes in the file's tree; None when the file has no grammar (not indexed by PRISM)."""
    from prism.parser.tree_sitter_loader import parse_source
    parsed = parse_source(path, data)
    if parsed is None or parsed.root_node is None:
        return None
    root = parsed.root_node
    if not root.has_error:
        return 0
    n, stack = 0, [root]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            n += 1
        stack.extend(node.children)
    return n


@dataclass
class FileState:
    size: int
    mtime_ns: int
    seen_sha: str            # the workdir bytes the probe last saw
    shown_sha: str           # the bytes in the shadow (what PRISM shows)
    shown_errors: int | None
    edits: int = 0           # byte changes the probe has seen (0 = the task's start state)
    shown_edit: int = 0      # which edit the shown bytes are
    gated: bool = False


@dataclass
class Refresh:
    changed: list[str] = field(default_factory=list)
    gated: list[str] = field(default_factory=list)       # gated after this refresh
    rebuilt: bool = False
    probe_seconds: float = 0.0
    build_seconds: float = 0.0


class WorkingIndex:
    def __init__(self, workdir: str, shadow: str, language_tier: str | None = None):
        from prism.cli import LANGUAGE_TIER_PERMISSIVE
        self.workdir, self.shadow = os.path.abspath(workdir), os.path.abspath(shadow)
        self.tier = language_tier or LANGUAGE_TIER_PERMISSIVE
        head = subprocess.run(["git", "-C", self.workdir, "rev-parse", "HEAD"], check=True, capture_output=True,
                              text=True).stdout.strip()
        if os.path.exists(self.shadow):
            raise FileExistsError(self.shadow)
        subprocess.run(["git", "-C", self.workdir, "worktree", "add", "-q", "--detach", self.shadow, head], check=True,
                       capture_output=True)
        self.files: dict[str, FileState] = {}
        self.builder = None
        self.tag_matrix = None
        # The start state: copy the workdir's visible bytes into the shadow (uncommitted start changes included), and
        # drop files the workdir does not show. Nothing is gated at the start: the start state is what it is.
        visible = self._visible()
        for rel in visible:
            data = self._read(rel)
            st = os.stat(os.path.join(self.workdir, rel))
            self._write_shadow(rel, data)
            self.files[rel] = FileState(st.st_size, st.st_mtime_ns, _sha(data), _sha(data),
                                        error_nodes(rel, data))
        from prism.cli import discover_files
        for path in discover_files(self.shadow, self.tier):
            rel = os.path.relpath(path, self.shadow)
            if rel not in self.files:
                os.remove(path)
        self._build()

    # ------------------------------------------------------------------ files
    def _visible(self) -> list[str]:
        from prism.cli import discover_files
        return sorted(os.path.relpath(p, self.workdir) for p in discover_files(self.workdir, self.tier))

    def _read(self, rel: str) -> bytes:
        with open(os.path.join(self.workdir, rel), "rb") as fh:
            return fh.read()

    def _write_shadow(self, rel: str, data: bytes) -> None:
        path = os.path.join(self.shadow, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "rb") as fh:
                if fh.read() == data:
                    return
        except OSError:
            pass
        with open(path, "wb") as fh:
            fh.write(data)

    def _build(self) -> float:
        import time
        from prism.cli import build_pipeline
        t = time.perf_counter()
        self.builder, self.tag_matrix = build_pipeline(self.shadow, self.tier, use_cache=False)
        return time.perf_counter() - t

    @staticmethod
    def _gated(new_errors: int | None, shown_errors: int | None) -> bool:
        """The gate rule (see the module docstring)."""
        if new_errors is None:
            return False
        return new_errors > (shown_errors or 0)

    # ------------------------------------------------------------------ the probe and the refresh
    def refresh(self) -> Refresh:
        import time
        t0 = time.perf_counter()
        out = Refresh()
        visible = set(self._visible())
        dirty = False
        for rel in sorted(visible | set(self.files)):
            st_path = os.path.join(self.workdir, rel)
            state = self.files.get(rel)
            if rel not in visible:                       # deleted (or no longer visible)
                if state is not None:
                    os.remove(os.path.join(self.shadow, rel)) if os.path.exists(os.path.join(self.shadow, rel)) else None
                    del self.files[rel]
                    out.changed.append(rel)
                    dirty = True
                continue
            st = os.stat(st_path)
            if state is not None and (st.st_size, st.st_mtime_ns) == (state.size, state.mtime_ns):
                continue
            data = self._read(rel)
            sha = _sha(data)
            if state is not None and sha == state.seen_sha:   # touched, same bytes
                state.size, state.mtime_ns = st.st_size, st.st_mtime_ns
                continue
            out.changed.append(rel)
            errors = error_nodes(rel, data)
            if state is None:                            # a new file
                state = FileState(st.st_size, st.st_mtime_ns, sha, "", None, edits=1)
                self.files[rel] = state
            else:
                state.size, state.mtime_ns, state.seen_sha = st.st_size, st.st_mtime_ns, sha
                state.edits += 1
            if self._gated(errors, state.shown_errors if state.shown_sha else 0):
                state.gated = True                       # the shadow keeps the last parsable bytes (or no file)
            else:
                state.gated = False
                state.shown_sha, state.shown_errors, state.shown_edit = sha, errors, state.edits
                self._write_shadow(rel, data)
                dirty = True
        out.gated = sorted(r for r, s in self.files.items() if s.gated)
        out.probe_seconds = time.perf_counter() - t0
        if dirty:
            out.build_seconds = self._build()
            out.rebuilt = True
        return out

    # ------------------------------------------------------------------ the notice
    def gated_files(self) -> list[str]:
        return sorted(r for r, s in self.files.items() if s.gated)

    def notice(self, files) -> str:
        """The R3-5 notice for the gated files among `files` (repo-relative); empty when none is gated."""
        lines = []
        for rel in sorted(set(files)):
            s = self.files.get(rel)
            if s is None or not s.gated:
                continue
            if s.shown_sha:
                lines.append(f"⚠️ File {rel} was edited and does not currently parse. Content shown is from the last "
                             f"parsable version (edit {s.shown_edit}, commit {s.shown_sha[:12]}). Read the file "
                             f"directly for the current content.")
            else:
                lines.append(f"⚠️ File {rel} was edited and does not currently parse. It has no parsable version, so "
                             f"nothing from it is shown. Read the file directly for the current content.")
        return "\n".join(lines) + ("\n\n" if lines else "")

    def close(self) -> None:
        subprocess.run(["git", "-C", self.workdir, "worktree", "remove", "--force", self.shadow], capture_output=True)
        shutil.rmtree(self.shadow, ignore_errors=True)
