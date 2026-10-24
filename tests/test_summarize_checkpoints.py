"""`prism summarize` checkpoints: an interrupted run leaves the last valid
checkpoint and the same command resumes from it.

The kill test runs the real CLI in a subprocess against a local fake Ollama
server (`/api/chat`) that answers a fixed number of calls and then hangs,
SIGKILLs the process once a checkpoint is on disk, and resumes.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from prism.cli import build_pipeline
from prism.summaries import index as S
from prism.summaries.pipeline import BATCH, build_index

ROOT = Path(__file__).resolve().parents[1]
GOOD = "Persists the customer purchase and charges the payment card"
N_FUNCTIONS = 40  # 4 batches of BATCH (12): 12, 12, 12, 4


def _repo(root: Path, n: int = N_FUNCTIONS) -> Path:
    (root / "app").mkdir(parents=True)
    (root / "app/__init__.py").write_text("")
    (root / "app/ops.py").write_text("".join(f"def op{i}(x):\n    return x + {i}\n\n\n" for i in range(n)))
    return root


class FakeOllama:
    """`/api/chat` with the locked prompts' JSON replies; after `answer`
    calls every further request hangs until `release()`."""

    def __init__(self, answer: int | None = None):
        self.answer, self.calls, self._gate = answer, 0, threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                prompt = body["messages"][0]["content"]
                owner.calls += 1
                if owner.answer is not None and owner.calls > owner.answer:
                    owner._gate.wait(30)  # hang: the summarizer is stuck mid-run
                    return
                if prompt.startswith("The sentence below"):
                    content = {"summary": GOOD}
                elif "its source code follows" in prompt:  # candidate v4's escalation
                    content = {"effects": "stores the purchase; charges the card", "summary": GOOD}
                else:
                    content = {q: GOOD for q in re.findall(r"^Qualified Name: (\S+)$", prompt, re.M)}
                out = json.dumps({"message": {"content": json.dumps(content)}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def release(self):
        self._gate.set()
        self.server.shutdown()


def _summarize(repo: Path, url: str, *extra: str) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": f"{ROOT / 'src'}{os.pathsep}{ROOT}"}
    return subprocess.Popen(
        [sys.executable, "-m", "prism.cli", "summarize", "--repo", str(repo), "--base-urls", url, "--threads", "1",
         "--checkpoint-every", "1", *extra],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _entries(repo: Path) -> int:
    data = S.read_index(S.index_path(str(repo)))
    return len(data["functions"]) if data else 0


def test_sigkill_mid_run_leaves_the_last_checkpoint_and_the_rerun_resumes(tmp_path):
    repo = _repo(tmp_path / "repo")
    hung = FakeOllama(answer=2)  # batches 1-2 answered, batch 3 hangs
    proc = _summarize(repo, hung.url)
    try:
        deadline = time.time() + 120
        while _entries(repo) < 2 * BATCH:
            assert proc.poll() is None, proc.stderr.read()
            assert time.time() < deadline, "no checkpoint written"
            time.sleep(0.1)
        os.kill(proc.pid, signal.SIGKILL)  # no cleanup, no finally blocks
        proc.wait(30)
    finally:
        hung.release()

    data = S.read_index(S.index_path(str(repo)))  # valid JSON, valid format
    assert data is not None and data["pipeline"] == S.locked_pipeline()
    assert len(data["functions"]) == 2 * BATCH
    assert all(e["summary"] == GOOD and e["key"] and e["source_sha"] for e in data["functions"].values())
    builder = build_pipeline(str(repo), use_cache=False)[0]
    partial = S.state_for(builder, str(repo))
    assert partial.valid == 2 * BATCH and partial.weight == 0.0  # 60% coverage: search ignores it

    server = FakeOllama()
    try:
        out, err = _summarize(repo, server.url).communicate(timeout=180)
    finally:
        server.release()
    assert "coverage 100.0% of 40 functions" in out and "kept 24" in out, out + err
    assert server.calls == 2  # only the two missing batches: 12 + 4 functions
    final = S.state_for(builder, str(repo))
    assert final.valid == N_FUNCTIONS and final.weight == S.SUMMARY_WEIGHT
    assert not list(S.index_path(str(repo)).parent.glob("*.tmp"))  # any temp file the kill left is swept


def test_a_temp_file_left_by_a_killed_writer_is_swept_by_the_next_write(tmp_path):
    path = S.index_path(str(tmp_path))
    S.write_index(path, S.locked_pipeline(), {})
    orphan = path.parent / (path.name + ".x1y2z3.tmp")
    orphan.write_text('{"format": 1, "functi')  # what a SIGKILL mid-write leaves
    assert S.read_index(path) is not None  # readers never look at it
    S.write_index(path, S.locked_pipeline(), {})
    assert not orphan.exists() and S.read_index(path) is not None


class _LLM:
    def __init__(self, bad=()):
        self.bad, self.calls, self.repairs = set(bad), 0, 0

    def json(self, prompt, max_out, seed=42):
        self.calls += 1
        if prompt.startswith("The sentence below"):
            self.repairs += 1
            return {"summary": GOOD}
        return {q: (f"Calls {q}() directly" if q in self.bad else GOOD)
                for q in re.findall(r"^Qualified Name: (\S+)$", prompt, re.M)}


def test_checkpoints_are_written_every_n_batches(tmp_path):
    repo = _repo(tmp_path / "repo")
    builder = build_pipeline(str(repo), use_cache=False)[0]
    counts = build_index(builder, str(repo), llm=_LLM(), batch=4, threads=1, checkpoint_every=3)
    assert counts["checkpoints"] == 3  # 10 batches: after 3, 6 and 9; the final write is not a checkpoint
    assert counts["coverage"] == 1.0


def test_a_crash_during_the_write_keeps_the_previous_index(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "repo", n=6)
    builder = build_pipeline(str(repo), use_cache=False)[0]
    build_index(builder, str(repo), llm=_LLM())
    path = S.index_path(str(repo))
    before = path.read_bytes()

    def crash(_fd):
        raise KeyboardInterrupt("killed while writing")
    monkeypatch.setattr(os, "fsync", crash)
    with pytest.raises(KeyboardInterrupt):
        S.write_index(path, S.locked_pipeline(), {})
    assert path.read_bytes() == before and S.read_index(path) is not None
    assert not list(path.parent.glob("*.tmp"))


def test_an_unrepaired_summary_from_an_interrupted_run_is_repaired_on_resume(tmp_path):
    repo = _repo(tmp_path / "repo", n=6)
    builder = build_pipeline(str(repo), use_cache=False)[0]
    build_index(builder, str(repo), llm=_LLM())
    path = S.index_path(str(repo))
    data = S.read_index(path)
    entry = data["functions"]["app.ops.op3"]
    entry.update(summary="Calls app.ops.op3() directly", checked=False)  # summarized, killed before repair
    S.write_index(path, data["pipeline"], data["functions"])
    llm = _LLM()
    counts = build_index(builder, str(repo), llm=llm)
    assert counts["to_summarize"] == 0 and counts["repaired"] == 1 and llm.repairs == 1 and llm.calls == 1
    fixed = S.read_index(path)["functions"]["app.ops.op3"]
    assert fixed["summary"] == GOOD and fixed["checked"]


def test_only_summarizes_the_listed_functions(tmp_path):
    root = _repo(tmp_path / "repo", n=6)
    builder = build_pipeline(str(root), use_cache=False)[0]
    srv = FakeOllama()
    try:
        counts = build_index(builder, str(root), base_urls=[srv.url], threads=1, only={"app.ops.op1", "app.ops.op4"})
    finally:
        srv.release()
    assert counts["functions"] == 2 and counts["coverage"] == 1.0
    assert set(S.read_index(S.index_path(str(root)))["functions"]) == {"app.ops.op1", "app.ops.op4"}
