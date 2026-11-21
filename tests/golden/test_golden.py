"""Golden-output tests: what PRISM's tools return on the two golden fixtures (GOLDEN_TESTS_PLAN.md §2-§3).

    pytest tests/golden                    # compare each tool's output with tests/golden/<repo>/<tool>/<name>.txt
    pytest tests/golden --update-golden    # rewrite the saved outputs (commit them with the change that caused them)

Each fixture is copied to a temporary folder, committed with git (everything except `worktree_overlay/`), and the
overlay is copied over it as an uncommitted edit. The tools are then served exactly as R3's agents receive them:
`harness.experiments.r3.tools.ToolLayer` over a `WorkingIndex`, without the per-call token cap (a harness setting,
not PRISM's output). `prism_blast_radius` also takes `include_tests` here (R3 fixes it to false).

The outputs depend on PRISM's token counts, so every test skips unless PRISM's tokenizer is exact
(`prism.slicer.tokenizer.is_exact()`): otherwise the saved outputs would differ between machines.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
FIXTURES = HERE.parent / "fixtures" / "golden"
QUERIES = json.loads((HERE / "queries.json").read_text())
OVERLAY = "worktree_overlay"


def _exact_tokenizer() -> bool:
    from prism.slicer.tokenizer import is_exact
    return is_exact()


pytestmark = pytest.mark.skipif(not _exact_tokenizer(),
                                reason="PRISM's tokenizer is not exact here (tiktoken's cl100k encoding unavailable): "
                                       "golden outputs depend on token counts")


def _cases():
    for repo, tools in sorted(QUERIES.items()):
        for tool, queries in sorted(tools.items()):
            for name in sorted(queries):
                yield pytest.param(repo, tool, name, id=f"{repo}/{tool}/{name}")


def golden_path(repo: str, tool: str, name: str) -> Path:
    return HERE / repo / tool / f"{name}.txt"


def setup_repo(repo: str, dest: Path) -> Path:
    """The fixture as a git repository with its overlay as an uncommitted edit."""
    work = dest / repo
    shutil.copytree(FIXTURES / repo, work, ignore=shutil.ignore_patterns(OVERLAY))
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.name=golden", "-c", "user.email=golden@example.invalid", "commit", "-qm", "base"]):
        subprocess.run(cmd, cwd=work, check=True, env=env, capture_output=True)
    if (FIXTURES / repo / OVERLAY).is_dir():
        shutil.copytree(FIXTURES / repo / OVERLAY, work, dirs_exist_ok=True)
    return work


_FLOAT = re.compile(r"(?<![\w.])(-?\d+\.\d{4})\d+")
_ENGINE_COMMIT = re.compile(r'(<engine commit=")[^"]*(")')


def normalize(text: str, roots: list[str]) -> str:
    """Repository-relative paths, no temporary-folder prefix, floats to 4 decimals, no trailing spaces, and the
    envelope's engine commit masked (it changes with every PRISM commit, not with what the tool returns)."""
    for root in sorted(roots, key=len, reverse=True):
        text = text.replace(root + os.sep, "").replace(root, ".")
    text = _FLOAT.sub(r"\1", text)
    text = _ENGINE_COMMIT.sub(r"\1*\2", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip() + "\n"


class _Layer:
    """R3's ToolLayer, plus `include_tests` for the blast radius."""

    def __init__(self, repo: str, base: Path):
        from harness.experiments.r3.tools import ToolLayer
        from harness.experiments.r3.working_index import WorkingIndex
        self.work = setup_repo(repo, base)
        self.wi = WorkingIndex(str(self.work), str(base / f"{repo}-shadow"))
        self.layer = ToolLayer(self.wi, None)

    def call(self, tool: str, args: dict) -> str:
        args = dict(args)
        if tool == "prism_blast_radius":
            include_tests = args.pop("include_tests", False)
            if include_tests:
                return self._blast_with_tests(args["symbol"])
            return self.layer.call(tool, **args)
        return self.layer.call(tool, **args)

    def _blast_with_tests(self, symbol: str) -> str:
        from harness.experiments.r3.tools import CAP_TOKENS
        from prism.mcp import server
        self.layer._sync()
        out = self.layer._mcp(server.prism_blast_radius, seed_symbol=symbol, budget_tokens=CAP_TOKENS,
                              include_tests=True)
        head = [f"Blast radius of `{out['seed']}`: {out['callers_total']} transitive callers"
                + (" (list cut)" if out["callers_truncated"] else "") + ", nearest first:"]
        head += [f"- hop {c['hop']}: `{c['symbol']}` ({c['file']}:{c['line']})" for c in out["callers"]]
        return ("\n".join(head) + "\n\n" + out["envelope"]).replace(self.wi.shadow + os.sep, "")

    def close(self):
        self.layer.close()
        subprocess.run(["git", "-C", str(self.work), "worktree", "remove", "--force", self.wi.shadow],
                       capture_output=True)


@pytest.fixture(scope="module")
def layers(tmp_path_factory):
    made = {}

    def get(repo):
        if repo not in made:
            made[repo] = _Layer(repo, tmp_path_factory.mktemp(f"golden-{repo}"))
        return made[repo]
    yield get
    for layer in made.values():
        layer.close()


@pytest.mark.parametrize("repo,tool,name", list(_cases()))
def test_golden_output(repo, tool, name, layers, request):
    layer = layers(repo)
    got = normalize(layer.call(tool, QUERIES[repo][tool][name]), [layer.wi.shadow, str(layer.work)])
    path = golden_path(repo, tool, name)
    if request.config.getoption("--update-golden"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(got)
        return
    assert path.exists(), f"no saved output for {repo}/{tool}/{name}: run `pytest tests/golden --update-golden`"
    want = path.read_text()
    if got != want:
        diff = "".join(difflib.unified_diff(want.splitlines(True), got.splitlines(True), f"saved/{path.name}",
                                            f"now/{path.name}"))
        pytest.fail(f"{repo}/{tool}/{name} changed:\n{diff}", pytrace=False)


def test_every_saved_output_has_a_query():
    """A saved file with no query in queries.json would go stale silently."""
    known = {golden_path(r, t, n) for r, tools in QUERIES.items() for t, qs in tools.items() for n in qs}
    saved = {p for repo in QUERIES for p in (HERE / repo).rglob("*.txt")}
    assert sorted(map(str, saved - known)) == []
