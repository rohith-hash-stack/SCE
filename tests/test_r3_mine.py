"""R3 mining: backports, cherry-picks and repeats of one diff are not separate tasks."""
from __future__ import annotations

import subprocess

from harness.experiments.r3.mine import window_commits


def _git(repo, *args, date="2024-10-15T12:00:00Z"):
    env = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date, "GIT_AUTHOR_NAME": "x", "GIT_AUTHOR_EMAIL": "x@y",
           "GIT_COMMITTER_NAME": "x", "GIT_COMMITTER_EMAIL": "x@y", "PATH": "/usr/bin:/bin"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout


def commit(repo, path, text, msg, date="2024-10-15T12:00:00Z"):
    (repo / path).write_text(text)
    _git(repo, "add", path, date=date)
    _git(repo, "commit", "-qm", msg, date=date)
    return _git(repo, "rev-parse", "HEAD").strip()


def test_backports_cherry_picks_and_repeats_are_dropped(tmp_path):
    _git(tmp_path, "init", "-q")
    commit(tmp_path, "a.py", "x = 1\n", "start", date="2024-09-01T00:00:00Z")
    first = commit(tmp_path, "a.py", "x = 2\n", "Change x (#1)")
    commit(tmp_path, "b.py", "y = 1\n", "[PR #1/abc123 backport][3.10] Change y (#2)")
    commit(tmp_path, "c.py", "z = 1\n", "Change z\n\n(cherry picked from commit abc123)")
    commit(tmp_path, "a.py", "x = 1\n", "Revert x")
    again = commit(tmp_path, "a.py", "x = 2\n", "Change x again")      # the same diff as `first`
    late = commit(tmp_path, "d.py", "w = 1\n", "Late", date="2024-12-05T00:00:00Z")
    kept, counts = window_commits(str(tmp_path), "2024-10-01", "2024-11-30")
    assert first in kept and again not in kept and late not in kept
    assert counts == {"commits_in_window": 5, "backports_dropped": 2, "repeats_dropped": 1}
    assert len(kept) == 2                                                 # `first` and the revert
