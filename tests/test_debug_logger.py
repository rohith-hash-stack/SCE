"""prism.debug: `prism mcp --debug-log` (over real stdio), the Copilot
chat-export reader, and the timeline join."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from prism.debug.copilot import load_copilot_session
from prism.debug.timeline import join, load_calls, render_markdown

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "test_frameworks"
SEED = "api.libraries.UserApi.UserApi.create_user"


class _Server:
    def __init__(self, repo: str, log_dir: str) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "prism.cli", "mcp", "--repo", repo, "--debug-log", log_dir],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(REPO.parent),
        )
        self.next_id = 1

    def request(self, method: str, params: dict) -> dict:
        rid = self.next_id
        self.next_id += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            assert line, self.proc.stderr.read()
            msg = json.loads(line)
            if msg.get("id") == rid:
                return msg

    def notify(self, method: str) -> None:
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def close(self) -> None:
        self.proc.stdin.close()
        self.proc.wait(timeout=60)


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("dl")
    repo = tmp / "repo"
    shutil.copytree(FIXTURE, repo)
    log_dir = tmp / "logs"
    server = _Server(str(repo), str(log_dir))
    try:
        server.request("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                      "clientInfo": {"name": "test", "version": "0"}})
        server.notify("notifications/initialized")
        call = lambda name, args: server.request("tools/call", {"name": name, "arguments": {"repo_path": str(repo), **args}})
        replies = [
            call("prism.blast_radius", {"seed_symbol": SEED, "budget_tokens": 4000, "api_key": "secret-key"}),
            call("prism.blast_radius", {"seed_symbol": "api.libraries.UserApi.UserApi.nope"}),
            call("get_symbol_context", {"target_symbol": "ui.pages.LoginPage.LoginPage.login"}),
        ]
    finally:
        server.close()
    return log_dir, replies, load_calls(str(log_dir))


def test_every_tool_call_is_recorded_with_stages(recorded):
    log_dir, replies, calls = recorded
    assert [c["tool"] for c in calls] == ["prism.blast_radius", "prism.blast_radius", "get_symbol_context"]
    blast = calls[0]
    assert blast["status"] == "ok" and blast["latency_ms"] > 0
    assert blast["index"]["source"] in ("full_build", "disk_cache") and blast["index"]["symbols"] > 0
    stages = [s["stage"] for s in blast["stages"]]
    assert {"index.total", "manifest", "hydrate", "caller_walk", "render"} <= set(stages)
    manifest = next(s for s in blast["stages"] if s["stage"] == "manifest")
    assert manifest["direction"] == "both" and manifest["by_role"]["caller"] == 5
    res = blast["result"]
    assert res["callers_total"] == 5 and "2|test|api.tests.users.Delete_User" in res["callers"]
    assert res["tokens"] > 0 and any(d.startswith("seed|") for d in res["delivered"])
    session = log_dir / calls[0]["session"]
    assert (session / res["envelope_payload"]).read_text().startswith("<prism_context")
    assert "caller" in (session / manifest["payload"]).read_text()
    # the second and third calls reuse the server's in-memory index
    assert calls[1]["index"]["source"] == "memory" and calls[2]["index"]["source"] == "memory"


def test_errors_and_secrets(recorded):
    log_dir, replies, calls = recorded
    error = calls[1]
    assert error["status"] == "error" and error["error"]["code"] == -32002
    assert "error" in replies[1] or replies[1]["result"].get("isError")      # the client still got the error
    text = "".join(p.read_text() for p in log_dir.rglob("*") if p.is_file())
    assert "secret-key" not in text and "api_key" not in calls[0]["args"]
    assert calls[2]["result"]["tokens"] > 0 and calls[2]["result"]["payload"].endswith("result.md")


def _export(path: Path, timestamps: bool) -> None:
    """A chat in the shape VS Code's "Chat: Export Chat..." writes."""
    base = 1_800_000_000_000
    req = lambda i, text, tools, answer: {
        "requestId": f"r{i}", "message": {"text": text, "parts": []}, "modelId": "copilot/gpt-test",
        **({"timestamp": base + i * 60_000} if timestamps else {}),
        "response": [{"value": "Let me check. "},
                     *[{"kind": "toolInvocationSerialized", "toolId": t, "toolCallId": f"c{i}{n}",
                        "invocationMessage": {"value": f"Running {t}"},
                        "toolSpecificData": {"kind": "input", "rawInput": {"seed_symbol": SEED}}}
                       for n, t in enumerate(tools)],
                     {"kind": "markdownContent", "content": {"value": answer}}],
        "result": {"timings": {"firstProgress": 900, "totalElapsed": 4200}},
    }
    path.write_text(json.dumps({"requests": [
        req(1, "Which tests break if I change create_user?", ["mcp_prism_prism_blast_radius"], "Four tests."),
        req(2, "And the login page?", ["mcp_prism_prism_blast_radius", "mcp_prism_get_symbol_context"], "Three."),
        req(3, "thanks", [], "You're welcome."),
    ]}))


def test_copilot_export_is_read(tmp_path):
    path = tmp_path / "chat.json"
    _export(path, timestamps=True)
    turns = load_copilot_session(str(path))
    assert [t["user_text"] for t in turns][0] == "Which tests break if I change create_user?"
    first = turns[0]
    assert first["model"] == "copilot/gpt-test" and first["total_elapsed_ms"] == 4200
    assert first["response_text"] == "Let me check. Four tests."
    assert first["tool_calls"][0]["tool_id"] == "mcp_prism_prism_blast_radius"
    assert first["tool_calls"][0]["input"] == {"seed_symbol": SEED}
    assert first["user_tokens_est"] > 0 and first["timestamp"].startswith("2027-")


def test_timeline_joins_by_order_without_timestamps(recorded, tmp_path):
    log_dir, _replies, calls = recorded
    path = tmp_path / "chat.json"
    _export(path, timestamps=False)
    turns, unmatched = join(load_copilot_session(str(path)), calls)
    assert [c["call_id"] for c in turns[0]["prism_calls"]] == [calls[0]["call_id"]]
    assert [c["tool"] for c in turns[1]["prism_calls"]] == ["prism.blast_radius", "get_symbol_context"]
    assert turns[2]["prism_calls"] == [] and unmatched == []
    text = render_markdown(turns, unmatched, str(log_dir))
    assert "Which tests break" in text and "callers: 5 (4 tests)" in text and "-32002" in text


def test_timeline_joins_by_time_and_cli_writes_a_report(recorded, tmp_path):
    log_dir, _replies, calls = recorded
    path = tmp_path / "chat.json"
    _export(path, timestamps=True)           # all turns are in 2027: every call precedes them
    turns, unmatched = join(load_copilot_session(str(path)), calls)
    assert all(not t["prism_calls"] for t in turns) and len(unmatched) == 3
    out = tmp_path / "timeline.md"
    proc = subprocess.run([sys.executable, "-m", "prism.cli", "debug", "timeline", "--log-dir", str(log_dir),
                           "--out", str(out)], capture_output=True, text=True, cwd=str(REPO.parent))
    assert proc.returncode == 0, proc.stderr
    assert "## Prism calls" in out.read_text() and "prism.blast_radius" in out.read_text()


def test_find_lists_exact_symbol_names(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    proc = subprocess.run([sys.executable, "-m", "prism.cli", "debug", "find", "--repo", str(repo), "create", "user"],
                          capture_output=True, text=True, cwd=str(REPO.parent))
    assert proc.returncode == 0, proc.stderr
    assert f"{SEED}\n    method, implementation, api/libraries/UserApi.py:13, 1 direct caller(s)" in proc.stdout
    assert "3 match(es)" in proc.stdout


def test_mcp_without_debug_log_records_nothing(tmp_path, monkeypatch):
    """Off by default: plain `prism mcp` never imports the recorder."""
    monkeypatch.delenv("PRISM_DEBUG_LOG", raising=False)
    probe = ("import sys, prism.cli, prism.mcp.server as s; s.run_server = lambda **k: None; "
             "sys.argv = ['prism', 'mcp']; "
             "import contextlib\nwith contextlib.suppress(SystemExit): prism.cli.main()\n"
             "print('prism.debug.recorder' in sys.modules)")
    proc = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "False"
