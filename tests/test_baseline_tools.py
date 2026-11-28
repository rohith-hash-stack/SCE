"""Phase 3: BASELINE_LSP's IDE-agent tools (`harness/experiments/r3/baseline_tools.py`): BM25 semantic search over
code chunks, ast-grep structural search, and a read-only shell."""
import os
import subprocess

import pytest

from harness.experiments.r3.baseline_tools import BaselineToolError, BaselineTools


@pytest.fixture()
def work(tmp_path):
    files = {"pkg/conn.py": ("class Connection:\n    def close_socket(self):\n        '''Shut the network socket.'''\n"
                             "        return 1\n\n\ndef open_database(url):\n    return url\n"),
             "pkg/user.py": "def finish(c, o):\n    o.close()\n    return c.close(1, 2)\n",
             "README.md": "Connections are pooled.\n"}
    for p, t in files.items():
        os.makedirs(os.path.dirname(tmp_path / p) or tmp_path, exist_ok=True)
        (tmp_path / p).write_text(t)
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "first"]):
        subprocess.run(["git", *cmd], cwd=tmp_path, check=True)
    return str(tmp_path)


def test_semantic_search_ranks_by_words_and_sees_edits(work):
    bt = BaselineTools(work)
    out = bt.semantic_search("shut down the network socket")
    assert out.splitlines()[2].startswith("pkg/conn.py:2-4 `Connection.close_socket`")
    open(os.path.join(work, "pkg/user.py"), "a").write("\n\ndef socket_teardown():\n    '''network socket shut'''\n")
    assert "socket_teardown" in bt.semantic_search("network socket shut")
    assert "open_database" in bt.semantic_search("database url")


def test_ast_search_structural(work):
    bt = BaselineTools(work)
    out = bt.ast_search("$X.close($A, $B)")
    assert "pkg/user.py:3:12: return c.close(1, 2)" in out and "o.close()" not in out
    assert "o.close()" in bt.ast_search("$X.close()")
    with pytest.raises(BaselineToolError):
        bt.ast_search("$X", "../outside")


def test_shell_reads_and_refuses_writes(work):
    bt = BaselineTools(work)
    assert "first" in bt.run_shell("git log --oneline")
    assert "pkg/conn.py" in bt.run_shell("ls pkg | sort && echo pkg/conn.py")
    assert bt.run_shell("cat pkg/user.py | wc -l").strip() == "3"
    for bad in ("rm -rf pkg", "echo x > a.txt", "sed -i s/a/b/ pkg/user.py", "find . -delete",
                "find . -exec rm {} ;", "git commit -m x", "git -c core.pager=sh log", "cat $(ls)", "sleep 1 &",
                "awk 'BEGIN{system(\"rm x\")}'", "rg --pre sh x", "python -c 1", "sort -o out pkg/user.py"):
        with pytest.raises(BaselineToolError):
            bt.run_shell(bad)
    assert os.path.exists(os.path.join(work, "pkg/user.py")) and not os.path.exists(os.path.join(work, "a.txt"))
